"""Isolated LiveKit client integration. Fake provider unless --memory-live is explicit."""
import json
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scenario = parser.add_mutually_exclusive_group()
    scenario.add_argument('--scene', choices=['teach', 'guide'])
    scenario.add_argument('--call-view', action='store_true', help='Exercise the mobile call interface.')
    scenario.add_argument('--mobile-test-report', action='store_true', help='Exercise local phone test capture and replay.')
    scenario.add_argument('--memory-live', action='store_true', help='Use configured paid provider with synthetic media for visual recall.')
    scenario.add_argument('--harmony-gateway', action='store_true', help='Exercise the native WebSocket gateway with the real RTC transport.')
    args = parser.parse_args()
    ports = (7890, 7891, 7892, 8002, 8083)
    for port in ports:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'Isolated test port {port} is in use; existing services left untouched')
    out = ROOT / '.runtime/official-harness-rtc' / uuid.uuid4().hex
    out.mkdir(parents=True)
    config = out / 'livekit.yaml'
    config.write_text('port: 7890\nbind_addresses: [127.0.0.1]\nrtc:\n  tcp_port: 7891\n  udp_port: 7892\n'
        '  node_ip: 127.0.0.1\n  use_external_ip: false\n  enable_loopback_candidate: true\n'
        'keys:\n  fixture: fixture-secret-only-local-tests-0123456789012345\nlogging:\n  level: warn\n', encoding='utf-8')
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONUNBUFFERED': '1',
        'API_PORT': '8002', 'WORKER_PORT': '8083', 'WEB_ORIGIN': 'http://127.0.0.1:8002',
        'HARNESS_MODE': 'qwen_live_official', 'AGENT_NAME': 'official-fixture-verification',
        'LIVEKIT_URL': 'ws://127.0.0.1:7890', 'LIVEKIT_PUBLIC_URL': 'ws://127.0.0.1:7890',
        'LIVEKIT_API_KEY': 'fixture', 'LIVEKIT_API_SECRET': 'fixture-secret-only-local-tests-0123456789012345',
        'MODEL_PROVIDER': 'yibuapi', 'YIBUAPI_API_KEY': 'fixture-provider-key', 'QWEN_API_KEY': '',
        'QWEN_LIVE_MAX_CALL_SECONDS': '60', 'QWEN_LIVE_PROACTIVE_ENABLED': 'true', 'QWEN_LIVE_MEMORY_ENABLED': 'false', 'OFFICIAL_TEST_OUTPUT': str(out),
        'OFFICIAL_TEST_SCENE': 'report' if args.mobile_test_report else 'guide' if args.call_view else args.scene or ''}
    if args.mobile_test_report:
        env['LIVEKIT_MOBILE_TEST_ROOT'] = str(out / 'mobile-tests')
    if args.harmony_gateway:
        env.update(QWEN_LIVE_PROACTIVE_ENABLED='false', QWEN_VOICE='harmony-rtc-fixture')
    if args.memory_live:
        # Let Settings resolve the real credentials from the normal environment
        # or .env, never from command-line arguments or test reports.
        for key in ('MODEL_PROVIDER', 'YIBUAPI_API_KEY', 'QWEN_API_KEY'):
            if key in os.environ:
                env[key] = os.environ[key]
            else:
                env.pop(key, None)
        env.update(QWEN_LIVE_MEMORY_ENABLED='true', QWEN_LIVE_MAX_CALL_SECONDS='120',
                   MEMORY_RTC_LIVE='1', MEMORY_RTC_ROOT='http://127.0.0.1:8002')
    processes, files = [], []
    print(out, flush=True)
    try:
        commands = [
            ('livekit', [str(ROOT / '.tools/livekit/livekit-server.exe'), '--config', str(config)]),
            ('api', [sys.executable, '-m', 'uvicorn', 'livekit_vlm_agent.api:app', '--host', '127.0.0.1', '--port', '8002', '--no-access-log']),
            ('worker', [sys.executable, 'tests/official_memory_worker.py', 'start'] if args.memory_live else
                       [sys.executable, 'tests/official_fixture_worker.py', 'start']),
        ]
        for name, command in commands:
            output = (out / f'{name}.log').open('w', encoding='utf-8'); files.append(output)
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {})))
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError('Isolated fixture service exited')
            try:
                ready = urlopen('http://127.0.0.1:8002/api/health', timeout=1).status == 200
            except Exception:
                ready = False
            if ready and 'registered worker' in (out / 'worker.log').read_text(encoding='utf-8'):
                break
            time.sleep(.3)
        else:
            raise RuntimeError('Isolated fixture services not ready')
        if args.harmony_gateway:
            return subprocess.run([sys.executable, 'tests/harmony_gateway_rtc.py'], cwd=ROOT, env=env, timeout=70).returncode
        browser_test = ('web/tests/mobile-test-report-rtc.mjs' if args.mobile_test_report else
            'web/tests/live-memory-rtc.mjs' if args.memory_live else
            'web/tests/call-view-rtc.mjs' if args.call_view else 'web/tests/official-rtc.mjs')
        return subprocess.run(['node', browser_test], cwd=ROOT, env=env, timeout=150 if args.memory_live else 85).returncode
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=3)
        for output in files:
            output.close()
        (out / 'cleanup.json').write_text(json.dumps({'owned_services_exited': all(p.poll() is not None for p in processes),
            'existing_services_touched': False, 'provider_calls': None if args.memory_live else 0,
            'live_provider': args.memory_live}), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
