"""Isolated RTC verification services. Existing LiveKit/services are left running."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.runtime/direct-background-rtc'/uuid.uuid4().hex


def main():
    for port in (8001,8082):
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',port))==0:
                raise RuntimeError('Isolated verification port in use')
    OUT.mkdir(parents=True,exist_ok=True)
    print(OUT, flush=True)
    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'PYTHONUNBUFFERED':'1',
         'API_PORT':'8001','WORKER_PORT':'8082','WEB_ORIGIN':'http://127.0.0.1:8001',
         'HARNESS_MODE':'direct_background','AGENT_NAME':'direct-background-verification',
         'LIVEKIT_PUBLIC_URL':'ws://127.0.0.1:7880', 'BACKGROUND_MAX_GENERATIONS_PER_CALL':'2',
         'BACKGROUND_OBSERVATION_ENABLED':'true',
         'DIRECT_TEST_OUTPUT':str(OUT)}
    processes=[]; files=[]
    try:
        for name,cmd in [('api',[sys.executable,'-m','uvicorn','livekit_vlm_agent.api:app','--host','127.0.0.1','--port','8001','--no-access-log']),
                         ('worker',[sys.executable,'-m','livekit_vlm_agent.worker','start'])]:
            f=(OUT/f'{name}.log').open('w',encoding='utf-8'); files.append(f)
            processes.append(subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW))
        for _ in range(60):
            time.sleep(.5)
            if any(p.poll() is not None for p in processes):
                raise RuntimeError('Isolated service exited')
            try:
                ready=urlopen('http://127.0.0.1:8001/api/health',timeout=1).status==200
            except Exception:
                ready=False
            if ready and 'registered worker' in (OUT/'worker.log').read_text(encoding='utf-8'):
                break
        else:
            raise RuntimeError('Isolated services not ready')
        result=subprocess.run(['node','web/tests/direct-rtc.mjs'],cwd=ROOT,env=env,timeout=100)
        return result.returncode
    finally:
        for p in reversed(processes):
            if p.poll() is None: p.terminate()
        for p in reversed(processes):
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=3)
        for f in files: f.close()
        (OUT/'cleanup.json').write_text(json.dumps({'owned_children_exited':all(p.poll() is not None for p in processes),
                                                   'existing_services_touched':False}),encoding='utf-8')


if __name__=='__main__':
    raise SystemExit(main())
