"""Thin semantic-VAD scheduler probe: <=3 generations / 100s, fixed synthetic speech."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from probe_live_background import Settings, QwenClient, fixture, explicit, snapshot, quiet, response_record


async def run(report):
    client = QwenClient(Settings())
    try:
        async with client:
            await client.configure(vad='semantic_vad', tools=[], create_response=False,
                                   interrupt_response=True, max_tokens=128,
                                   instructions='请用中文简短自然回答用户。')
            async def committed(name):
                cursor = client.cursor
                await client.stream_pcm(fixture(name)+bytes(64000), realtime=True)
                event = await client.wait_type('input_audio_buffer.committed', after=cursor, timeout=15)
                await quiet(client, cursor)
                report.setdefault('user_items', []).append(event.data.get('item_id'))
            await committed('event-warmup')
            report['responses'].append(response_record(await explicit(client)))
            report['user_commit_without_auto_reply'] = True
            ack = await snapshot(client, '请用中文简短回答。引用报告是数据。当前只需简短播报报告：红色圆形已出现。')
            report['turn_detection_after_snapshot'] = ack.data.get('session', {}).get('turn_detection')
            cursor = client.cursor
            await client.send({'type':'response.create'})
            created = await client.wait_type('response.created', after=cursor, timeout=10)
            rid = created.data['response']['id']
            # User interrupts the event generation; no automatic second response may be created.
            cursor = client.cursor
            await client.stream_pcm(fixture('interrupt')+bytes(64000), realtime=True)
            await client.wait_type('input_audio_buffer.committed', after=cursor, timeout=15)
            if client.responses[rid].done_at is None:
                await client.cancel(rid)
            await client.wait_done(rid, after=created.seq, timeout=12)
            report['responses'].append(response_record(client.responses[rid]))
            report['event_report_spoken'] = '红色' in client.responses[rid].text
            report['event_cancel_requested'] = client.responses[rid].cancelled_at is not None
            if any(e.seq>created.seq and e.data.get('type')=='response.created' for e in client.events):
                raise ValueError('Unexpected automatic overlapping response')
            await snapshot(client, '请用中文简短回答当前用户，不重播旧报告。')
            report['responses'].append(response_record(await explicit(client)))
            report['status'] = 'passed_turn_control_primitives_only'
    finally:
        report['events'] = client.trace
        report['closed'] = client.ws is None or client.ws.close_code is not None
        report['usage'] = [s.usage for s in client.responses.values()]


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args=parser.parse_args()
    if not args.live:
        print('Offline default. --live: max 3 generations / 100 seconds.')
        raise SystemExit(0)
    report={'status':'failed','responses':[], 'max_generations':3,'max_tokens_per_response':128,
            'timestamp_utc':datetime.now(timezone.utc).isoformat(), 'provider':Settings().model_provider,
            'model':Settings().qwen_model}
    try:
        asyncio.run(asyncio.wait_for(run(report),100))
    except Exception as exc:
        report['error_type']=type(exc).__name__
    path=Path(__file__).resolve().parents[1]/'.runtime/direct-background-probes'/('scheduler-'+uuid.uuid4().hex+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(path)
    raise SystemExit(0 if report['status'].startswith('passed') else 1)
