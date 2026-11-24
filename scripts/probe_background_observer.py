"""Actual application observer with synthetic drawing: max 2 evaluations / 40s."""
import argparse
import asyncio
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
import time
import uuid
import sys

from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.coordination.direct import DirectState
from livekit_vlm_agent.media.fanout import VideoFanout
from livekit_vlm_agent.observation.background import BackgroundObserver


async def run(report):
    image=Image.new('RGB',(480,360),'white')
    draw=ImageDraw.Draw(image)
    draw.ellipse((50,100,180,230),outline='gray',width=22)
    draw.line((170,165,390,165),fill='gray',width=24)
    draw.line((340,165,340,220),fill='gray',width=24)
    draw.line((380,165,380,220),fill='gray',width=24)
    jpeg=io.BytesIO(); image.save(jpeg,format='JPEG')
    encoded=base64.b64encode(jpeg.getvalue()).decode('ascii')
    state=DirectState('synthetic-probe',memory_root=ROOT/'.runtime/memory',observation_available=True)
    session=SimpleNamespace(settings=Settings(),video=SimpleNamespace(enabled=True,epoch=0),background_frames=VideoFanout())
    observer=BackgroundObserver(session,state)
    observer.runner.max_per_call=2
    try:
        await state.control({'action':'observation_create','request_id':'fixed-target','content':'看到画面中出现钥匙时报告。'})
        async with asyncio.timeout(40):
            while state.tasks.task.status=='active':
                now=time.monotonic()
                session.background_frames.publish(SimpleNamespace(epoch=0,received_at=now,image=encoded))
                await asyncio.sleep(1)
        report['task_status']=state.tasks.task.status
        report['observer']=observer.snapshot()
        report['events']=(await state.status())['events']
        if state.tasks.task.status!='completed' or not report['events']:
            raise ValueError('No fresh accepted match')
        report['status']='passed_text_observer'
    finally:
        report['usage']=list(observer.runner.evaluator.usage)
        report['generations']=observer.runner.counts['generations']
        await observer.aclose(); await state.aclose()
        report['observer_closed']=observer._loop.done()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--live',action='store_true'); args=parser.parse_args()
    if not args.live:
        print('Offline default. --live uses actual observer; max 2 generations / 40s.'); raise SystemExit(0)
    report={'status':'failed','provider':Settings().model_provider,'model':Settings().qwen_model,'physical_devices':False,'max_generations':2,'max_seconds':40}
    try: asyncio.run(run(report))
    except Exception as exc: report['error_type']=type(exc).__name__
    path=ROOT/'.runtime/direct-background-probes'/('observer-'+uuid.uuid4().hex+'.json')
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(path)
    raise SystemExit(0 if report['status'].startswith('passed') else 1)
