"""Bounded H0 probes; default prints a checklist, --live opts into fixed fixtures.

No physical devices, no playback, no automatic retries, at most two generations per run.
The CLI never promotes production capabilities from a configuration acknowledgement.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.harness.types import Decision
from livekit_vlm_agent.providers.qwen_client import QwenClient, QwenProtocolError
from livekit_vlm_agent.providers.qwen_control import FunctionCallCollector

CHECKS = {
    "P1": "manual commit + explicit generation, no automatic response",
    "P2": "complete function item + successful response + acknowledged tool result",
    "P3": "quiet DECIDE then tools-disabled audio SPEAK (protocol only)",
    "P4": "red/blue observation using transport_silence, no replayed question",
    "P5": "tool result, bounded wait, then explicit continuation",
    "P6": "pending: LiveKit segment playout + cancellation + measured audio tail",
    "P7": "pending: LiveKit generation ownership and automatic input endpoints",
    "P8": "pending: RTC frame association; historical images separately unverified",
}
TOOL = {"type": "function", "name": "submit_task_decision",
        "description": "提交一次内部控制决定，不直接向用户输出。",
        "parameters": Decision.model_json_schema()}


def pcm_fixture():
    with wave.open(str(ROOT / ".runtime/m0-fixtures/greeting.wav")) as wav:
        if (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) != (16000, 2, 1):
            raise ValueError("Invalid fixed PCM fixture")
        if wav.getnframes() > 16000 * 10:
            raise ValueError("Fixture exceeds ten-second input cap")
        return wav.readframes(wav.getnframes())


async def configure(client, *, speak=False, observe=False, fixed_audio=False, fixed_tools=False):
    instructions = (
        "这是固定非真人协议实验。只调用一次 submit_task_decision，不直接回答。"
        "提交 action=answer，speech_intent 为'协议探针成功'；其他字段省略。"
    )
    if observe:
        instructions = (
            "这是固定红蓝画面协议实验。仅根据本次新提交的画面，"
            "调用一次 submit_task_decision，action=answer，speech_intent 仅填'红色'或'蓝色'。"
            "不根据历史画面作答，其他字段省略。"
        )
    if speak:
        instructions = "工具结果已回填。请用中文简短说：协议探针成功。不要调用工具。"
    if fixed_tools:
        instructions = (
            "这是多轮协议测试。每一个新的用户音频轮次都必须重新调用 submit_task_decision，"
            "action=answer，speech_intent='协议探针成功'，其他字段省略。"
            "历史已经调用过工具不能代替本轮调用，禁止对新的用户轮次直接回答。"
            "只有刚收到工具执行结果、且没有新的用户输入的续答请求，才直接语音说协议探针成功，"
            "这次续答不调用工具。之后新的用户音频又必须先调用工具。"
        )
    await client.configure(vad=False, instructions=instructions,
                           modalities=["text", "audio"] if speak or fixed_audio else ["text"],
                           tools=[] if speak and not fixed_tools else [TOOL], max_tokens=256 if speak else 2048)


async def expect_quiet(client, after, seconds=0.5):
    try:
        await client.wait_type("response.created", after=after, timeout=seconds)
    except TimeoutError:
        return
    raise QwenProtocolError("Unexpected automatic generation")


async def commit(client):
    cursor = client.cursor
    await client.send({"type": "input_audio_buffer.commit"})
    await client.wait_type("input_audio_buffer.committed", after=cursor, timeout=10)
    await expect_quiet(client, cursor)


async def generate(client, *, modalities=None):
    cursor = client.cursor
    request = {"type": "response.create"}
    if modalities is not None:
        request["response"] = {"modalities": modalities}
    await client.send(request)
    created = await client.wait_type("response.created", after=cursor, timeout=10)
    response_id = created.data["response"]["id"]
    state = await client.wait_done(response_id, after=created.seq, timeout=20)
    if state.status != "completed":
        raise QwenProtocolError("Response did not complete")
    collector = FunctionCallCollector(response_id)
    calls = ()
    for event in client.events:
        if event.seq > created.seq:
            result = collector.feed(event.data)
            if result is not None:
                calls = result
    return state, calls


async def decide(client, report):
    state, calls = await generate(client)
    report["responses"].append({"response_id": state.id, "status": state.status,
                                "audio_bytes": state.audio_bytes, "call_count": len(calls),
                                "text_chars": len(state.text)})
    if len(calls) != 1 or calls[0].name != "submit_task_decision":
        raise QwenProtocolError("Expected exactly one complete internal function item")
    decision = Decision.model_validate_json(calls[0].arguments)
    if decision.action != "answer":
        raise QwenProtocolError("Unexpected probe decision")
    report["responses"][-1]["call_id"] = calls[0].id
    item_id = "item_" + uuid.uuid4().hex
    cursor = client.cursor
    await client.send({"type": "conversation.item.create", "item": {
        "id": item_id, "type": "function_call_output", "call_id": calls[0].id,
        "output": json.dumps({"accepted": True, "reason": "fixed_protocol_probe",
                              "authoritative_state": None})}})
    await client.wait(lambda event: event.data.get("type") == "conversation.item.created"
                      and event.data.get("item", {}).get("call_id") == calls[0].id,
                      after=cursor, timeout=10)
    report["responses"][-1]["tool_result_acknowledged"] = True
    return state, decision


async def run_probe(case, report, *, fixed_audio=False):
    settings = Settings()
    if settings.qwen_missing():
        raise ValueError("Missing Qwen settings")
    async with QwenClient(settings) as client:
        try:
            await configure(client, observe=case == "vision", fixed_audio=fixed_audio)
            if case == "control":
                await client.stream_pcm(pcm_fixture(), realtime=False)
                await commit(client)
                report["checks"]["P1"] = "manual_commit_quiet"
                state, _ = await decide(client, report)
                report["checks"]["P2"] = "passed_fixed_probe"
                report["checks"]["P1"] = "passed_fixed_probe"
                report["decide_audio_bytes"] = state.audio_bytes
                cursor = client.cursor
                await expect_quiet(client, cursor, seconds=1)
                await configure(client, speak=True)
                spoken, calls = await generate(client, modalities=["text", "audio"])
                report["responses"].append({"response_id": spoken.id, "status": spoken.status,
                                            "audio_bytes": spoken.audio_bytes})
                report["checks"]["P5"] = "passed_one_second_delay"
                if calls or not spoken.audio_bytes or not spoken.text:
                    report["checks"]["P3"] = "failed_speak_missing_audio_or_unexpected_tool"
                    raise QwenProtocolError("SPEAK did not return tools-disabled audio/text")
                report["checks"]["P3"] = ("passed_protocol_only" if state.audio_bytes == 0
                                           else "failed_unexpected_decide_audio")
                report["speak_audio_bytes"] = spoken.audio_bytes
            else:
                for color in ("red", "blue"):
                    image = (ROOT / f".runtime/m0-fixtures/{color}.jpg").read_bytes()
                    # Transport silence is synthetic, never presented as user speech.
                    await client.stream_pcm(bytes(16000), image=image, realtime=False)
                    await commit(client)
                    state, decision = await decide(client, report)
                    expected = "红色" if color == "red" else "蓝色"
                    report["observations"].append({"frame_id": "probe-" + color,
                        "audio_origin": "transport_silence", "expected": expected,
                        "matched": decision.speech_intent == expected,
                        "response_id": state.id, "decide_audio_bytes": state.audio_bytes})
                    cursor = client.cursor
                    await expect_quiet(client, cursor)
                if not all(item["matched"] for item in report["observations"]):
                    raise QwenProtocolError("Fresh red/blue image comparison failed")
                report["checks"]["P4"] = "passed_fixed_transport_silence_only"
            report["status"] = "completed_probe"
        finally:
            report["events"] = client.trace
            report["usage"] = [state.usage for state in client.responses.values()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use paid Qwen with fixed fixtures")
    parser.add_argument("--case", choices=("control", "vision"), default="control")
    parser.add_argument("--fixed-audio", action="store_true", help="Keep audio enabled from first config")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"network": False, "checks": CHECKS}, indent=2))
        return 0
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "case": args.case,
              "status": "failed", "checks": {key: "unknown" for key in CHECKS},
              "observations": [], "responses": [], "max_generations": 2, "physical_devices": False,
              "production_capabilities_changed": False}
    report["fixed_audio"] = args.fixed_audio
    try:
        asyncio.run(asyncio.wait_for(run_probe(args.case, report, fixed_audio=args.fixed_audio), timeout=90))
    except Exception as exc:
        # Exception messages can contain server payloads. Store class only.
        report["error_type"] = type(exc).__name__
    output = ROOT / ".runtime/harness-probes" / (args.case + "-" + uuid.uuid4().hex + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output))
    return 0 if report["status"] == "completed_probe" else 1


if __name__ == "__main__":
    raise SystemExit(main())
