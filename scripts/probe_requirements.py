"""Opt-in multi-turn Bailian routing probe: synthetic speech only, no images.

At most 14 generations / 130 seconds. Playback receipts are simulated: this
tests actual model tool choice and retained tasks, not phone/acoustic latency.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from livekit import rtc
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.official.bridge import OfficialBridge


async def probe():
    settings = Settings(harness_mode='qwen_live_official', qwen_live_proactive_enabled=True,
                        qwen_live_max_generations=14, qwen_live_max_monitor_generations=2)
    report = dict(status='failed', physical_devices=False, playback='simulated_receipts',
                  max_generations=14, cases=[], usage={}, tools=[], diagnostics=[], latency=[], transcripts=[])
    scene, auxiliary, outputs, replies, user_turns = {}, [], 0, 0, 0
    last_activity = time.monotonic()
    playing = set()
    bridge = None

    async def event(e):
        nonlocal scene, auxiliary, outputs, replies, user_turns, last_activity
        if e['type'] in ('audio', 'audio_end', 'tool', 'transcript'):
            last_activity = time.monotonic()
        if e['type'] == 'scene':
            scene = e
        elif e['type'] == 'requirements':
            auxiliary = e['auxiliary']
        elif e['type'] == 'audio':
            if e['outputId'] not in playing:
                playing.add(e['outputId'])
                bridge.control(dict(type='playback_started', callId=bridge.call_id, epoch=1, outputId=e['outputId']))
        elif e['type'] == 'audio_clear':
            playing.clear()
        elif e['type'] == 'audio_end':
            outputs += 1
            playing.discard(e['outputId'])
            bridge.control(dict(type='playback_completed', callId=bridge.call_id, epoch=1, outputId=e['outputId'], sequence=e['sequence']))
        elif e['type'] == 'transcript':
            if e.get('role') == 'assistant':
                replies += 1
            else:
                user_turns += 1
            report['transcripts'].append({k: e.get(k) for k in ('role', 'text')})  # Synthetic probe only.
        elif e['type'] == 'usage':
            report['usage'] = {k: e.get(k) for k in ('generations', 'monitorGenerations', 'inputTokens', 'outputTokens')}
        elif e['type'] == 'tool':
            report['tools'].append({k: e.get(k) for k in ('name', 'code')})
        elif e['type'] == 'latency':
            report['latency'].append({k: e.get(k) for k in ('metric', 'ms', 'kind')})
        elif e['type'] in ('diagnostic', 'fatal'):
            report['diagnostics'].append({k: e.get(k) for k in ('type', 'code', 'stage')})

    bridge = OfficialBridge(settings, uuid.uuid4().hex, event)
    async def silence():
        await bridge.push_audio(rtc.AudioFrame(bytes(640), 16000, 1, 320))
        await asyncio.sleep(.02)

    async def utterance(name, predicate):
        while playing or time.monotonic() - last_activity < 2:
            await silence()
        with wave.open(str(ROOT / '.runtime/m0-fixtures' / f'{name}.wav')) as sound:
            assert (sound.getframerate(), sound.getnchannels(), sound.getsampwidth()) == (16000, 1, 2)
            pcm = bytes(16000) + sound.readframes(sound.getnframes())
        before, before_user = outputs, user_turns
        for at in range(0, len(pcm), 640):
            chunk = pcm[at:at + 640]
            await bridge.push_audio(rtc.AudioFrame(chunk + bytes(640 - len(chunk)), 16000, 1, 320))
            await asyncio.sleep(.02)
        for _ in range(1500):
            await silence()
            if user_turns > before_user and outputs > before and predicate() and not playing and time.monotonic() - last_activity >= 2:
                report['cases'].append(name)
                print(json.dumps({'case': name, 'usage': report['usage']}), flush=True)
                return
            if bridge.failed:
                raise RuntimeError('bridge_failed')
        raise TimeoutError(name)

    started = time.monotonic()
    try:
        async with asyncio.timeout(130):
            await bridge.start()
            await utterance('scene-teach', lambda: scene.get('status') == 'waiting_step')
            primary_id, step = scene['id'], scene['step']
            await utterance('requirement-timer', lambda: any(r['kind'] == 'timer' and r['status'] == 'running' for r in auxiliary))
            assert scene['id'] == primary_id and scene['step'] == step
            previous_replies = replies
            await utterance('requirement-question', lambda: replies > previous_replies)
            assert scene['id'] == primary_id and scene['step'] == step and scene['status'] == 'waiting_step'
            await utterance('requirement-cancel', lambda: any(r['kind'] == 'timer' and r['status'] == 'cancelled' for r in auxiliary))
            assert scene['id'] == primary_id and scene['status'] == 'waiting_step'
            await utterance('requirement-pause', lambda: scene.get('status') == 'paused')
            assert scene['id'] == primary_id and scene['step'] == step
            report['status'] = 'passed_model_routing_and_state_only'
    except Exception as exc:
        report['error'] = type(exc).__name__
    finally:
        await bridge.aclose()
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        report['owned_child_exited'] = bridge.process is None or bridge.process.returncode is not None
        report['final_scene'] = {k: scene.get(k) for k in ('mode', 'status', 'step', 'completedSteps', 'delivery')}
        report['final_auxiliary'] = auxiliary
        out = ROOT / '.runtime/requirement-probes' / f'{uuid.uuid4().hex}.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: report[k] for k in ('status', 'cases', 'usage', 'elapsed_seconds', 'owned_child_exited')} | {'report': str(out)}, ensure_ascii=True))
    return 0 if report['status'].startswith('passed') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if args.live:
        raise SystemExit(asyncio.run(probe()))
    print('Offline. --live allows at most 14 real generations, synthetic speech, no camera, 130 seconds.')
