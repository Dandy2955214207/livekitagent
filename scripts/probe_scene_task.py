"""Opt-in synthetic voice routing probe. At most 6 generations, 2 background, 55 seconds."""
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
import livekit_vlm_agent.official.bridge as bridge_module


async def probe(mode):
    with wave.open(str(ROOT / f'.runtime/m0-fixtures/scene-{mode}.wav')) as sound:
        assert (sound.getframerate(), sound.getnchannels(), sound.getsampwidth()) == (16000, 1, 2)
        pcm = bytes(16000) + sound.readframes(sound.getnframes())
    settings = Settings(harness_mode='qwen_live_official', qwen_live_proactive_enabled=True,
                        qwen_live_max_generations=6, qwen_live_max_monitor_generations=2)
    report = dict(status='failed', mode=mode, physical_devices=False, playback='simulated_receipts_not_browser',
                  max_generations=6, scenes=[], transcripts=[], tools=[], synthetic_tool_arguments=[], responses=[], usage={}, diagnostics=[], outputs=0)
    done = asyncio.Event()
    bridge = None

    async def event(e):
        if e['type'] == 'scene':
            report['scenes'].append({k: e.get(k) for k in ('mode', 'goal', 'status', 'step', 'total', 'instruction', 'revision')})
        elif e['type'] == 'audio_end':
            report['outputs'] += 1
            for kind in ('playback_started', 'playback_completed'):
                bridge.control(dict(type=kind, callId=bridge.call_id, epoch=1, outputId=e['outputId'], sequence=e['sequence']))
        elif e['type'] == 'transcript':
            report['transcripts'].append({k: e.get(k) for k in ('role', 'text')})
        elif e['type'] == 'usage':
            report['usage'] = {k: e.get(k) for k in ('generations', 'monitorGenerations', 'inputTokens', 'outputTokens')}
        elif e['type'] == 'tool':
            report['tools'].append({k: e.get(k) for k in ('name', 'code')})
        elif e['type'] == 'probe_tool_arguments':
            report['synthetic_tool_arguments'].append({k: e.get(k) for k in ('name', 'arguments', 'code')})
        elif e['type'] == 'probe_response':
            report['responses'].append({k: e.get(k) for k in ('status', 'details', 'usage')})
        elif e['type'] in ('diagnostic', 'fatal'):
            report['diagnostics'].append({k: e.get(k) for k in ('type', 'code', 'stage')})
        target = 'waiting_step' if mode == 'teach' else 'watching'
        if report['outputs'] and report['scenes'] and report['scenes'][-1]['mode'] == mode and report['scenes'][-1]['status'] == target:
            done.set()

    bridge_module.BRIDGE = ROOT / 'harness/qwen-live/test/scene-probe-bridge.mjs'
    bridge = OfficialBridge(settings, uuid.uuid4().hex, event)
    started = time.monotonic()
    try:
        async with asyncio.timeout(55):
            await bridge.start()
            for at in range(0, len(pcm) + 960000, 640):
                chunk = pcm[at:at + 640] if at < len(pcm) else bytes(640)
                await bridge.push_audio(rtc.AudioFrame(chunk + bytes(640 - len(chunk)), 16000, 1, 320))
                await asyncio.sleep(.02)
                if done.is_set(): break
            await asyncio.wait_for(done.wait(), 5)
            await asyncio.sleep(.3)
            report['status'] = 'passed_scene_registration_and_first_instruction_only'
    except Exception as exc:
        report['error'] = type(exc).__name__
    finally:
        await bridge.aclose()
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        report['owned_child_exited'] = bridge.process is None or bridge.process.returncode is not None
        out = ROOT / '.runtime/scene-probes' / f'{uuid.uuid4().hex}.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'report': str(out), **report}, ensure_ascii=True))
    return 0 if report['status'].startswith('passed') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--mode', choices=['teach', 'guide'], default='teach')
    args = parser.parse_args()
    if args.live: raise SystemExit(asyncio.run(probe(args.mode)))
    print('Offline. --live uses only local synthetic speech; no camera, at most 6 generations / 55 seconds.')
