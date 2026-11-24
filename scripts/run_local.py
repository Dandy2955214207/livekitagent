"""Start and stop only this launcher's child processes; no machine-wide services."""

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--mobile", action="store_true", help="Use prepared project-local phone TLS profile")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--harness", action="store_true", help="Controlled guidance and automatic object introductions")
    modes.add_argument("--direct-background", action="store_true", help="D1 direct dialogue skeleton; proactive speech disabled")
    modes.add_argument('--qwen-live', action='store_true', help='Experimental official Qwen Live Harness with LiveKit Host')
    parser.add_argument('--official-proactive', action='store_true', help='Enable official observation/timer tools; requires --qwen-live')
    parser.add_argument('--observe-text', action='store_true', help='Experimental independent text observation; requires --direct-background')
    args = parser.parse_args()
    if args.observe_text and not args.direct_background:
        parser.error('--observe-text requires --direct-background')
    if args.official_proactive and not args.qwen_live:
        parser.error('--official-proactive requires --qwen-live')
    RUNTIME.mkdir(exist_ok=True)
    if args.stop:
        return stop_services()
    settings = Settings()
    if args.qwen_live:
        if settings.qwen_missing():
            raise SystemExit('Missing provider configuration: ' + ', '.join(settings.qwen_missing()))
        settings.qwen_endpoint()
        if not (ROOT / 'harness/qwen-live/node_modules/qwen-live-harness/dist/build-info.json').exists():
            raise SystemExit('Install official core first: npm.cmd ci --prefix harness/qwen-live --ignore-scripts')
    mobile = None
    if args.mobile:
        mobile = json.loads((RUNTIME / "mobile/profile.json").read_text(encoding="utf-8-sig"))
        if (RUNTIME / "mobile/probe-running.json").exists():
            raise SystemExit("Stop the capability gateway first: scripts/stop-device-check.ps1")
    lk_port = 7880
    for port in (lk_port, settings.api_port, settings.worker_port):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"Port {port} is already in use. Existing services were left untouched.")
    if not (ROOT / "web/dist/index.html").exists():
        raise SystemExit("Build the page first: npm.cmd run build --prefix web")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUNBUFFERED": "1"}
    if args.harness:
        env["HARNESS_MODE"] = "controlled_manual"
    elif args.direct_background:
        env["HARNESS_MODE"] = "direct_background"
    elif args.qwen_live:
        env['HARNESS_MODE'] = 'qwen_live_official'
        env['QWEN_LIVE_PROACTIVE_ENABLED'] = str(args.official_proactive).lower()
    if args.observe_text:
        env['BACKGROUND_OBSERVATION_ENABLED'] = 'true'
    if mobile:
        env.update(LIVEKIT_PUBLIC_URL=mobile["livekit_public_url"], WEB_ORIGIN=mobile["origin"], MOBILE_PROBE_ONLY="false")
    python = str(ROOT / ".venv/Scripts/python.exe")
    commands = [
        ("livekit", [str(ROOT / ".tools/livekit/livekit-server.exe"), "--config", str(RUNTIME / "mobile/livekit.yaml" if mobile else ROOT / "config/livekit.local.yaml")]),
        ("api", [python, "-m", "uvicorn", "livekit_vlm_agent.api:app", "--host", "127.0.0.1", "--port", str(settings.api_port), "--no-access-log"]),
        ("worker", [python, "-m", "livekit_vlm_agent.worker", "start"]),
    ]
    if mobile:
        commands.append(("mobile", [python, "-m", "livekit_vlm_agent.mobile_gateway"]))
    secrets = [settings.qwen_api_key.get_secret_value(), settings.yibuapi_api_key.get_secret_value(), settings.livekit_api_key.get_secret_value(), settings.livekit_api_secret.get_secret_value()]
    log_dir = RUNTIME / "logs"
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
            if registered and gateway_ready and ready(f"http://127.0.0.1:{settings.api_port}/api/health"):
                break
            time.sleep(0.3)
        else:
            raise RuntimeError("Services did not become ready within 30 seconds.")
        print(f"Ready: http://127.0.0.1:{settings.api_port}", flush=True)
        if mobile:
            print(f"Phone call: {mobile['origin']}/", flush=True)
            print("Phone needs scoped TCP 7881 / UDP 7882 firewall rules: scripts/mobile-firewall.ps1 -Media", flush=True)
        print("Ctrl+C or scripts/stop-local.ps1 stops these local services.", flush=True)
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
