"""Start and stop only this launcher's child processes; no machine-wide services."""

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
from secrets import token_urlsafe
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from livekit_vlm_agent.config import Settings

RUNTIME = ROOT / ".runtime"
PID_FILE = RUNTIME / "local-services.json"
STOP_FILE = RUNTIME / "local-services.stop"


@contextmanager
def record_lock():
    # Serialize starts and stops. The OS releases the lock if a launcher exits abruptly.
    with PID_FILE.with_suffix(".lock").open("a+b") as guard:
        guard.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(guard, fcntl.LOCK_EX)
        yield


def read_record():
    try:
        record = json.loads(PID_FILE.read_text(encoding="utf-8"))
        children = record["children"]
        creation_times = record.get("creation_times", {})
        if (not isinstance(children, dict) or "launcher" in children
                or not isinstance(creation_times, dict)
                or any(type(pid) is not int or pid <= 0
                       for pid in [record["launcher"], *children.values()])
                or any(type(value) not in (int, float) or not 0 < value < float("inf")
                       for value in creation_times.values())):
            raise ValueError("Invalid process record")
        return record
    except FileNotFoundError:
        return None
    except (ValueError, TypeError, KeyError):
        raise SystemExit(f"Cannot verify {PID_FILE}; inspect the recorded processes before clearing it.") from None


def recorded_process_alive(pid, creation_time):
    try:
        process = psutil.Process(pid)
        # Legacy records have only PIDs. Treat any live PID as possibly owned.
        return creation_time is None or process.create_time() == creation_time
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def discard_stale_record(record):
    times = record.get("creation_times", {})
    if recorded_process_alive(record["launcher"], times.get("launcher")):
        return False
    if any(recorded_process_alive(pid, times.get(name)) for name, pid in record["children"].items()):
        raise SystemExit("The recorded launcher exited, but some service processes may still be running. "
                         f"Inspect {PID_FILE} before restarting or stopping them.")
    STOP_FILE.unlink(missing_ok=True)
    PID_FILE.unlink()
    print("Cleared a stale local service record.")
    return True


def write_record(record):
    temporary = PID_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(PID_FILE)


def claim_record():
    with record_lock():
        existing = read_record()
        if existing is not None and not discard_stale_record(existing):
            raise SystemExit("Local services already have a launcher; use stop-local.ps1 first.")
        STOP_FILE.unlink(missing_ok=True)
        record = {"launcher": os.getpid(), "children": {}, "started": datetime.now().isoformat(),
                  "creation_times": {"launcher": psutil.Process().create_time()}}
        write_record(record)
        return record


def stop_services():
    with record_lock():
        record = read_record()
        if record is None:
            print("No services owned by this launcher are recorded.")
            return 0
        if discard_stale_record(record):
            return 0
        STOP_FILE.write_text("stop", encoding="utf-8")
    owner = (record["launcher"], record.get("creation_times", {}).get("launcher"))
    for _ in range(60):
        with record_lock():
            current = read_record()
            if (current is None or (current["launcher"], current.get("creation_times", {}).get("launcher")) != owner
                    or discard_stale_record(current)):
                print("Local services stopped.")
                return 0
        time.sleep(0.5)
    raise SystemExit("Stop request pending; inspect .runtime/logs and the launcher console.")


def ready(url):
    try:
        with urlopen(url, timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def trial_ports():
    with ExitStack() as stack:
        listeners = [stack.enter_context(socket.socket()) for _ in range(4)]
        listeners.append(stack.enter_context(socket.socket(type=socket.SOCK_DGRAM)))
        for listener in listeners:
            listener.bind(('127.0.0.1', 0))
        return [listener.getsockname()[1] for listener in listeners]


def main():
    global PID_FILE, STOP_FILE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--mobile", action="store_true", help="Use prepared project-local phone TLS profile")
    parser.add_argument('--trial-port', type=int, help='Run an isolated browser trial gateway on loopback')
    parser.add_argument('--reuse-trial-invite', action='store_true', help='Keep the browser trial invite code for this port')
    parser.add_argument('--qwen-live', action='store_true', help='Compatibility flag for the official Harness')
    parser.add_argument('--official-proactive', action='store_true', help='Enable official observation/timer tools')
    args = parser.parse_args()
    trial = args.trial_port is not None
    if trial and (args.mobile or not 1024 <= args.trial_port <= 65535):
        parser.error('--trial-port requires a valid port and cannot be combined with --mobile')
    if args.reuse_trial_invite and (not trial or args.stop):
        parser.error('--reuse-trial-invite requires a running trial configuration')
    trial_dir = RUNTIME / 'remote-trial'
    instance_dir = trial_dir / str(args.trial_port) if trial else RUNTIME
    if trial:
        PID_FILE = instance_dir / 'local-services.json'
        STOP_FILE = instance_dir / 'local-services.stop'
    instance_dir.mkdir(parents=True, exist_ok=True)
    if args.stop:
        return stop_services()
    if trial:
        api_port, worker_port, lk_port, rtc_tcp_port, rtc_udp_port = trial_ports()
        settings = Settings(api_port=api_port, worker_port=worker_port)
    else:
        lk_port, settings = 7880, Settings()
    from livekit_vlm_agent.official.bridge import check_startup
    try:
        check_startup(settings)
    except (RuntimeError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    mobile = None
    if args.mobile:
        mobile = json.loads((RUNTIME / "mobile/profile.json").read_text(encoding="utf-8-sig"))
        if (RUNTIME / "mobile/probe-running.json").exists():
            raise SystemExit("Stop the capability gateway first: scripts/stop-device-check.ps1")
    ports = (lk_port, settings.api_port, settings.worker_port)
    if trial:
        ports += (rtc_tcp_port, args.trial_port)
        if len(set(ports)) != len(ports):
            parser.error('--trial-port conflicts with an isolated service port')
    for port in ports:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"Port {port} is already in use. Existing services were left untouched.")
    if not (ROOT / "web/dist/index.html").exists():
        raise SystemExit("Build the page first: npm.cmd run build --prefix web")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUNBUFFERED": "1"}
    if args.official_proactive or args.qwen_live:
        env['QWEN_LIVE_PROACTIVE_ENABLED'] = str(args.official_proactive).lower()
    if mobile:
        env.update(LIVEKIT_PUBLIC_URL=mobile["livekit_public_url"], WEB_ORIGIN=mobile["origin"], MOBILE_PROBE_ONLY="false")
    livekit_config = RUNTIME / 'mobile/livekit.yaml' if mobile else ROOT / 'config/livekit.local.yaml'
    if trial:
        livekit_config = instance_dir / 'livekit.yaml'
        livekit_url = f'ws://127.0.0.1:{lk_port}'
        invite = trial_dir / f'live-{args.trial_port}-invite.txt'
        if args.reuse_trial_invite and not invite.is_file():
            raise SystemExit(f'Cannot reuse a missing trial invite: {invite}')
        trial_code = invite.read_text(encoding='ascii').strip() if args.reuse_trial_invite else token_urlsafe(24)
        env.update(API_PORT=str(settings.api_port), WORKER_PORT=str(settings.worker_port),
                   LIVEKIT_URL=livekit_url, LIVEKIT_PUBLIC_URL=livekit_url,
                   LIVEKIT_API_KEY=token_urlsafe(18), LIVEKIT_API_SECRET=token_urlsafe(32),
                   HARMONY_GATEWAY_TOKEN=token_urlsafe(32), HARMONY_TRIAL_CODE=trial_code,
                   HARMONY_TRIAL_EVENT_KEY=token_urlsafe(32),
                   QWEN_LIVE_PROACTIVE_ENABLED='true', QWEN_LIVE_MEMORY_ENABLED='true')
    python = str(ROOT / ".venv/Scripts/python.exe")
    commands = [
        ("livekit", [str(ROOT / ".tools/livekit/livekit-server.exe"), "--config", str(livekit_config)]),
        ("api", [python, "-m", "uvicorn", "livekit_vlm_agent.api:app", "--host", "127.0.0.1", "--port", str(settings.api_port), "--no-access-log"]),
        ("worker", [python, "-m", "livekit_vlm_agent.worker", "start"]),
    ]
    if mobile:
        commands.append(("mobile", [python, "-m", "livekit_vlm_agent.mobile_gateway"]))
    if trial:
        commands.append(('trial', [python, '-m', 'livekit_vlm_agent.harmony_gateway', '--mode', 'live',
                                   '--port', str(args.trial_port), '--api-url', f'http://127.0.0.1:{settings.api_port}',
                                   '--livekit-url', livekit_url]))
    secrets = [settings.qwen_api_key.get_secret_value(), settings.yibuapi_api_key.get_secret_value(),
               env.get('LIVEKIT_API_KEY', settings.livekit_api_key.get_secret_value()),
               env.get('LIVEKIT_API_SECRET', settings.livekit_api_secret.get_secret_value()), env.get('HARMONY_TRIAL_EVENT_KEY')]
    log_dir = instance_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    children, threads, claimed = [], [], False

    def log_output(process, destination):
        with destination.open("w", encoding="utf-8") as output:
            for line in process.stdout:
                for secret in filter(None, secrets):
                    line = line.replace(secret, "[REDACTED]")
                output.write(line)
                output.flush()

    try:
        record = claim_record()
        claimed = True
        if trial:
            livekit_config.write_text(json.dumps({
                'port': lk_port, 'bind_addresses': ['127.0.0.1'],
                'rtc': {'tcp_port': rtc_tcp_port, 'udp_port': rtc_udp_port, 'node_ip': '127.0.0.1',
                        'use_external_ip': False, 'enable_loopback_candidate': True},
                'keys': {env['LIVEKIT_API_KEY']: env['LIVEKIT_API_SECRET']},
            }), encoding='utf-8')
        for name, command in commands:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            children.append((name, process))
            record["children"][name] = process.pid
            record["creation_times"][name] = psutil.Process(process.pid).create_time()
            with record_lock():
                write_record(record)
            thread = threading.Thread(target=log_output, args=(process, log_dir / f"{name}.log"), daemon=True)
            thread.start(); threads.append(thread)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any(proc.poll() is not None for _, proc in children):
                raise RuntimeError("A child service exited. Inspect .runtime/logs.")
            worker_log = log_dir / "worker.log"
            registered = worker_log.exists() and "registered worker" in worker_log.read_text(encoding="utf-8")
            gateway_ready = not mobile or ((log_dir / "mobile.log").exists() and "Mobile TLS gateway ready" in (log_dir / "mobile.log").read_text(encoding="utf-8"))
            if trial:
                gateway_ready = ready(f'http://127.0.0.1:{args.trial_port}/health')
            if registered and gateway_ready and ready(f"http://127.0.0.1:{settings.api_port}/api/health"):
                break
            time.sleep(0.3)
        else:
            raise RuntimeError("Services did not become ready within 30 seconds.")
        print(f"Ready: http://127.0.0.1:{settings.api_port}", flush=True)
        if trial:
            invite.write_text(env['HARMONY_TRIAL_CODE'] + '\n', encoding='ascii')
            print(f'Trial page: http://127.0.0.1:{args.trial_port}/  Invite code file: {invite}', flush=True)
        if mobile:
            print(f"Phone call: {mobile['origin']}/", flush=True)
            print("Phone needs scoped TCP 7881 / UDP 7882 firewall rules: scripts/mobile-firewall.ps1 -Media", flush=True)
        stop_script = 'scripts/start-remote-trial.ps1 -Stop' if trial else 'scripts/stop-local.ps1'
        print(f'Ctrl+C or {stop_script} stops these local services.', flush=True)
        while not STOP_FILE.exists():
            if any(proc.poll() is not None for _, proc in children):
                raise RuntimeError("A child service exited. Restart to reset all sessions.")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for _, process in reversed(children):
            if process.poll() is None:
                process.terminate()
        for _, process in reversed(children):
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for thread in threads:
            thread.join(timeout=2)
        if claimed:
            with record_lock():
                STOP_FILE.unlink(missing_ok=True)
                PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
