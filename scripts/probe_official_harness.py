"""Bounded official-core provider smoke test. Offline by default; synthetic audio only."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
import uuid
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from livekit import rtc
from livekit_vlm_agent.config import PROJECT_ROOT, Settings
from livekit_vlm_agent.official.bridge import OfficialBridge


async def probe(connect_only=False, explicit_intent=False):
    maximum = 3 if explicit_intent else 2
    settings = Settings(harness_mode='qwen_live_official', qwen_live_memory_enabled=False, qwen_live_proactive_enabled=explicit_intent,
                        qwen_live_max_generations=maximum, qwen_live_max_monitor_generations=1,
                        qwen_live_max_call_seconds=30)
    if settings.qwen_missing():
        raise RuntimeError('Configured provider credentials are required')
    fixture = PROJECT_ROOT / ('.runtime/m0-fixtures/explicit-intent.wav' if explicit_intent else '.runtime/m0-fixtures/event-warmup.wav')
    with wave.open(str(fixture)) as sound:
        if (sound.getframerate(), sound.getnchannels(), sound.getsampwidth()) != (16000, 1, 2):
            raise RuntimeError('Expected mono PCM16/16000 synthetic fixture')
        pcm = sound.readframes(sound.getnframes())
    if explicit_intent:
        pcm = bytes(16000) + pcm  # 500 ms settling time, like an already-open mic.
    report = {'status': 'failed', 'provider': settings.model_provider, 'model': settings.qwen_model,
              'engine': 'qwen-live-harness@1.0.0', 'max_generations': maximum, 'timeout_seconds': 60,
              'input': 'local synthetic recurring cup reminder instruction' if explicit_intent else 'existing local synthetic neutral greeting + carrier silence',
              'physical_devices': False, 'playback_confirmed': False, 'memory_enabled': False,
              'proactive_enabled': explicit_intent, 'intents': [], 'audio_bytes': 0, 'completed_audio_outputs': 0,
              'user_transcripts': 0, 'assistant_transcripts': 0, 'speech_started': 0, 'speech_stopped': 0,
              'usage': {}, 'failures': [], 'diagnostics': []}
    finished = asyncio.Event()

    async def event_received(event):
        kind = event.get('type')
        if kind == 'audio':
            import base64
            report['audio_bytes'] += len(base64.b64decode(event['audio']))
        elif kind == 'audio_end':
            report['completed_audio_outputs'] += 1
            finished.set()
        elif kind == 'transcript':
            report[event['role'] + '_transcripts'] += 1
        elif kind == 'usage':
            report['usage'] = {key: event[key] for key in
                ('connections', 'generations', 'monitorGenerations', 'inputTokens', 'outputTokens')}
        elif kind == 'fatal':
            report['failures'].append(event.get('code', 'unknown'))
            finished.set()
        elif kind == 'diagnostic':
            report['diagnostics'].append({key: event.get(key) for key in ('source', 'code', 'stage', 'kind', 'status') if key in event})
        elif kind == 'intent':
            report['intents'].append({key: event.get(key) for key in ('generation', 'repeat', 'kind', 'status')})
        elif kind in ('speech_started', 'speech_stopped'):
            report[kind] += 1

    bridge = OfficialBridge(settings, uuid.uuid4().hex, event_received)
    started = time.monotonic()
    try:
        async with asyncio.timeout(60):
            await bridge.start()
            if connect_only:
                report['input'] = 'none; connection and session configuration only'
                report['status'] = 'passed_provider_configuration_only'
            # One real, synthetic utterance. No fabricated transcript, user text, or tool call.
            for at in range(0, 0 if connect_only else len(pcm) + (640000 if explicit_intent else 64000), 640):
                chunk = pcm[at:at + 640] if at < len(pcm) else bytes(640)
                chunk += bytes(640 - len(chunk))
                await bridge.push_audio(rtc.AudioFrame(chunk, 16000, 1, 320))
                await asyncio.sleep(.02)
                if explicit_intent and finished.is_set():
                    break
            if not connect_only:
                await asyncio.wait_for(finished.wait(), 20)
                await asyncio.sleep(.2)  # Allow response.done usage after the audio end marker.
                if explicit_intent:
                    # Registration speech can end before the independent Monitor
                    # handshake. Do not tear it down while it is provisioning.
                    ready_by = time.monotonic() + 8
                    while (not any(intent['status'] == 'running' for intent in report['intents'])
                           and time.monotonic() < ready_by and not bridge.failed):
                        await asyncio.sleep(.1)
            if (not connect_only and not bridge.failed and not report['failures'] and report['audio_bytes'] > 0
                    and report['completed_audio_outputs'] and report['assistant_transcripts']):
                report['status'] = 'passed_provider_audio_generation_only'
                if explicit_intent:
                    report['status'] = 'passed_recurring_intent_creation_only' if any(
                        intent['repeat'] is True and intent['kind'] == 'state' and intent['status'] == 'running'
                        for intent in report['intents']) else 'failed_recurring_intent_creation'
    except Exception as exc:
        report['failure_type'] = type(exc).__name__
    finally:
        await bridge.aclose()
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        report['owned_child_exited'] = bridge.process is None or bridge.process.returncode is not None
        out = PROJECT_ROOT / '.runtime/official-harness-probes' / (uuid.uuid4().hex + '.json')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'report': str(out), **report}, ensure_ascii=False))
    return 0 if report['status'].startswith('passed') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--connect-only', action='store_true', help='With --live: negotiate session only, no input or generation')
    parser.add_argument('--explicit-intent', action='store_true', help='Synthetic recurring reminder request, at most 3 generations; no images')
    args = parser.parse_args()
    if args.live:
        raise SystemExit(asyncio.run(probe(args.connect_only, args.explicit_intent)))
    print('Offline: no connection. --live uses at most 2 generations / 60 seconds; no observation or memory.')
