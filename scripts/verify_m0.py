"""Small paid protocol checks against real Qwen, using fixed non-personal fixtures."""

import argparse
from array import array
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT / "src"))
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.providers.qwen_client import QwenClient, QwenProtocolError

FIXTURES = ROOT / ".runtime/m0-fixtures"


def expected_content(case: str, text: str) -> bool:
    terms = {
        "greeting": ("语音测试成功",), "memory": ("蓝鲸七号",),
        "recall": ("蓝鲸七号",), "dog": ("狗", "犬", "萨摩耶"),
        "red": ("红",), "blue": ("蓝",), "after_cancel": ("新的回合",),
        "semantic_auto_turn": ("语音测试成功",),
        "semantic_interrupt_new": ("已经停止",),
    }
    return any(term in text for term in terms[case])


def pcm(name: str) -> bytes:
    with wave.open(str(FIXTURES / (name + ".wav"))) as wav:
        assert (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) == (16000, 2, 1)
        return wav.readframes(wav.getnframes())


def trimmed_pcm(name: str) -> bytes:
    data = pcm(name)
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    voiced = [n for n, sample in enumerate(samples) if abs(sample) > 300]
    if not voiced:
        raise ValueError("Fixture has no audible speech")
    start = max(0, voiced[0] - 640)
    end = min(len(samples), voiced[-1] + 1280)
    return data[start * 2:end * 2]


def result(state, *, input_end: float | None = None) -> dict:
    return {
        "response_id": state.id, "status": state.status, "text": state.text,
        "audio_bytes": state.audio_bytes,
        "audio_duration_s": round(state.audio_bytes / 48000, 3),
        "dropped_audio_bytes": state.dropped_audio_bytes,
        "first_audio_after_input_ms": round((state.first_audio_at - input_end) * 1000, 2)
            if state.first_audio_at is not None and input_end is not None else None,
        "cancel_to_done_ms": round((state.done_at - state.cancelled_at) * 1000, 2)
            if state.done_at is not None and state.cancelled_at is not None else None,
        "usage": state.usage,
    }


async def manual_turn(client, name, *, image=None):
    cursor = client.cursor
    await client.stream_pcm(pcm(name), image=image)
    input_end = time.monotonic()
    await client.commit_and_create()
    created = await client.wait_type("response.created", after=cursor)
    state = await client.wait_done(created.data["response"]["id"], after=created.seq)
    if state.status != "completed" or not state.audio_bytes or not state.text:
        raise QwenProtocolError("Expected completed response with audio and transcript")
    return state, result(state, input_end=input_end)


async def manual(client, report, save_audio, output_dir):
    await client.configure()
    for name in ("greeting", "memory", "recall"):
        state, item = await manual_turn(client, name)
        item["case"] = name
        item["expected_content_found"] = expected_content(name, state.text)
        report["cases"].append(item)
        print(f"{name}: {state.status} / {state.text}", flush=True)
        if save_audio and name == "greeting":
            path = output_dir / "greeting-output.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(state.audio)
            item["saved_audio"] = str(path.relative_to(ROOT))


async def vision(client, report, *args):
    await client.configure()
    for name, utterance in [("dog", "photo"), ("red", "color"), ("blue", "color")]:
        _, item = await manual_turn(client, utterance, image=(FIXTURES / f"{name}.jpg").read_bytes())
        item.update(case=name, expected_content_found=expected_content(name, item["text"]))
        report["cases"].append(item)
        print(f"{name}: {item['status']} / {item['text']}", flush=True)


async def cancellation(client, report, *args):
    await client.configure()
    cursor = client.cursor
    await client.stream_pcm(pcm("long"))
    await client.commit_and_create()
    created = await client.wait_type("response.created", after=cursor)
    rid = created.data["response"]["id"]
    first_audio = await client.wait(lambda e: e.data.get("type") == "response.audio.delta"
        and e.data.get("response_id") == rid, after=created.seq)
    await client.cancel(rid)
    await client.wait_done(rid, after=first_audio.seq)
    await asyncio.sleep(0.6)
    item = result(client.responses[rid])
    item["case"] = "explicit_cancel"
    item["remote_cancel_confirmed"] = item["status"] in {"cancelled", "canceled"}
    report["cases"].append(item)
    print(f"cancel: {item['status']} / {item['cancel_to_done_ms']} ms / dropped {item['dropped_audio_bytes']} bytes", flush=True)
    _, item = await manual_turn(client, "recover")
    item.update(case="after_cancel", expected_content_found="新的回合" in item["text"])
    report["cases"].append(item)
    print("after_cancel: " + item["text"], flush=True)


async def vad_turn(client, name):
    cursor = client.cursor
    await client.stream_pcm(pcm(name))
    input_end = time.monotonic()
    await client.stream_pcm(bytes(32000 * 2))
    created = await client.wait_type("response.created", after=cursor, timeout=18)
    return cursor, created, input_end


async def semantic(client, report, *args):
    await client.configure(vad=True)
    cursor, created, input_end = await vad_turn(client, "greeting")
    rid = created.data["response"]["id"]
    state = await client.wait_done(rid, after=created.seq)
    item = result(state, input_end=input_end)
    item.update(case="semantic_auto_turn", expected_content_found="语音测试成功" in state.text,
        speech_events=[e.data["type"] for e in client.events[cursor:]
            if e.data.get("type", "").startswith("input_audio_buffer.speech_")])
    report["cases"].append(item)
    print("semantic_auto_turn: " + state.text, flush=True)

    # An isolated acknowledgement is a diagnostic, not a universal VAD guarantee.
    cursor = client.cursor
    await client.stream_pcm(pcm("backchannel"))
    await client.stream_pcm(bytes(32000 * 2))
    await asyncio.sleep(1)
    spontaneous = [e for e in client.events[cursor:] if e.data.get("type") == "response.created"]
    report["cases"].append({"case": "isolated_backchannel", "responses_created": len(spontaneous)})
    for event in spontaneous:
        await client.wait_done(event.data["response"]["id"], after=event.seq)
    # Ensure a filtered acknowledgement is not carried into the next fixed case.
    cursor = client.cursor
    await client.send({"type": "input_audio_buffer.clear"})
    await client.wait_type("input_audio_buffer.cleared", after=cursor)

    cursor = client.cursor
    await client.stream_pcm(pcm("long"))
    # Keep sending silence in parallel so the interrupt starts at the first output.
    silence_task = asyncio.create_task(client.stream_pcm(bytes(32000 * 2)))
    try:
        created = await client.wait_type("response.created", after=cursor, timeout=18)
        old_id = created.data["response"]["id"]
        first_audio = await client.wait(lambda e: e.data.get("type") == "response.audio.delta"
            and e.data.get("response_id") == old_id, after=created.seq)
    finally:
        silence_task.cancel()
        await asyncio.gather(silence_task, return_exceptions=True)
    interrupt_cursor = client.cursor
    interrupt_started = time.monotonic()
    speech_task = asyncio.create_task(client.stream_pcm(pcm("interrupt") + bytes(32000 * 2)))
    try:
        speech = await client.wait_type("input_audio_buffer.speech_started", after=interrupt_cursor, timeout=12)
        # Simulate output buffer invalidation, while observing model-side cancellation.
        client.gate.cancel(old_id)
        old = await client.wait_done(old_id, after=first_audio.seq, timeout=20)
        new = await client.wait(lambda e: e.data.get("type") == "response.created"
            and e.data.get("response", {}).get("id") != old_id,
            after=interrupt_cursor, timeout=25)
        fresh = await client.wait_done(new.data["response"]["id"], after=new.seq)
        await speech_task
    finally:
        if not speech_task.done():
            speech_task.cancel()
        await asyncio.gather(speech_task, return_exceptions=True)
    item = result(old)
    item.update(case="semantic_interrupt_old", remote_cancel_confirmed=old.status in {"cancelled", "canceled"},
        input_to_speech_started_ms=round((speech.at - interrupt_started) * 1000, 2),
        speech_started_to_done_ms=round((old.done_at - speech.at) * 1000, 2))
    report["cases"].append(item)
    item = result(fresh)
    item.update(case="semantic_interrupt_new", expected_content_found="已经停止" in fresh.text)
    report["cases"].append(item)
    print(f"semantic_interrupt: old={old.status}, new={fresh.text}", flush=True)


async def pause(client, report, *args, vad_mode="semantic_vad"):
    await client.configure(vad=False if vad_mode == "manual" else vad_mode)
    cursor = client.cursor
    await client.stream_pcm(trimmed_pcm("pause_first"))
    await client.stream_pcm(bytes(12800))  # 400 ms explicit pause, plus <=80 ms retained tail
    second_start_cursor = client.cursor
    premature = [e for e in client.events[cursor:second_start_cursor]
        if e.data.get("type") == "response.created"]
    await client.stream_pcm(trimmed_pcm("pause_second"))
    input_end = time.monotonic()
    await client.stream_pcm(bytes(64000))
    if vad_mode == "manual":
        await client.commit_and_create()
    created = await client.wait_type("response.created", after=cursor, timeout=18)
    state = await client.wait_done(created.data["response"]["id"], after=created.seq)
    await asyncio.sleep(0.5)
    all_created = [e for e in client.events[cursor:] if e.data.get("type") == "response.created"]
    for extra in all_created[1:]:
        await client.wait_done(extra.data["response"]["id"], after=extra.seq)
    item = result(state, input_end=input_end)
    item.update(case="short_pause", premature_responses=len(premature),
        vad_mode=vad_mode,
        responses_created=len(all_created), single_response=len(all_created) == 1,
        expected_content_found=any(word in state.text for word in ("二", "2", "两")),
        no_premature_response=not premature,
        input_transcripts=[e.data.get("transcript", "") for e in client.events[cursor:]
            if e.data.get("type") == "conversation.item.input_audio_transcription.completed"])
    report["cases"].append(item)
    print(f"short_pause: premature={len(premature)}, text={state.text}", flush=True)


async def controls(client, report, *args):
    await pause(client, report, vad_mode=client.settings.qwen_turn_detection)
    await backchannel(client, report)


async def backchannel(client, report, *args):
    await client.configure(vad=True)
    cursor = client.cursor
    await client.stream_pcm(pcm("long"))
    silence_task = asyncio.create_task(client.stream_pcm(bytes(64000)))
    try:
        created = await client.wait_type("response.created", after=cursor, timeout=18)
        rid = created.data["response"]["id"]
        await client.wait(lambda e: e.data.get("type") == "response.audio.delta"
            and e.data.get("response_id") == rid, after=created.seq)
    finally:
        silence_task.cancel()
        await asyncio.gather(silence_task, return_exceptions=True)
    active_at_backchannel = client.responses[rid].done_at is None
    backchannel_cursor = client.cursor
    await client.stream_pcm(pcm("backchannel") + bytes(64000))
    state = await client.wait_done(rid, after=created.seq)
    await asyncio.sleep(0.5)
    extra = [e for e in client.events[backchannel_cursor:]
        if e.data.get("type") == "response.created"]
    item = result(state)
    item.update(case="backchannel_during_output", active_at_backchannel=active_at_backchannel,
        extra_responses=len(extra), backchannel_filtered=active_at_backchannel
            and state.status == "completed" and not extra)
    report["cases"].append(item)
    print(f"backchannel_during_output: old={state.status}, extra_responses={len(extra)}", flush=True)
    for event in extra:
        await client.wait_done(event.data["response"]["id"], after=event.seq)


async def run(args, report, output_dir):
    async with QwenClient(Settings(), capture_audio=args.save_audio) as client:
        try:
            func = {"manual": manual, "vision": vision, "cancel": cancellation,
                "semantic": semantic, "controls": controls, "pause": pause,
                "backchannel": backchannel}[args.stage]
            if args.stage == "pause":
                await pause(client, report, vad_mode=args.pause_mode)
            else:
                await func(client, report, args.save_audio, output_dir)
        finally:
            (output_dir / f"{args.stage}-events.json").write_text(
                json.dumps(client.trace, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["manual", "vision", "cancel", "semantic", "controls", "pause", "backchannel"])
    parser.add_argument("--save-audio", action="store_true", help="Save fixed greeting output only")
    parser.add_argument("--pause-mode", choices=["manual", "semantic_vad", "semantic_vad_ec"], default="semantic_vad")
    args = parser.parse_args()
    output_dir = ROOT / ".runtime/m0-results"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for suffix in ("report", "events"):
        existing = output_dir / f"{args.stage}-{suffix}.json"
        if existing.exists():
            history = output_dir / "history"
            history.mkdir(exist_ok=True)
            shutil.copy2(existing, history / f"{stamp}-{args.stage}-{suffix}.json")
    report = {"stage": args.stage, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "failed", "cases": [], "input_source": "Windows local TTS fixed PCM fixtures",
        "physical_devices_used": False, "turn_detection": Settings().qwen_turn_detection,
        "pause_mode": args.pause_mode if args.stage == "pause" else None}
    try:
        asyncio.run(asyncio.wait_for(run(args, report, output_dir), timeout=180))
        report["status"] = "completed"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        if isinstance(exc, QwenProtocolError):
            report["error"] = str(exc)
        print("FAILED " + report["error_type"] + ": " + report.get("error", "See stage event trace"), flush=True)
    assertions = [case[key] for case in report["cases"]
        for key in ("expected_content_found", "remote_cancel_confirmed", "no_premature_response",
            "backchannel_filtered", "single_response") if key in case]
    report["checks_passed"] = report["status"] == "completed" and bool(assertions) and all(assertions)
    (output_dir / f"{args.stage}-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
