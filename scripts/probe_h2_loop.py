"""One bounded four-generation check of restoring internal tools after speech."""
import asyncio
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from probe_harness import configure, commit, decide, generate, pcm_fixture, Settings, QwenClient


async def run(report, *, fixed_tools=False):
    async with QwenClient(Settings()) as client:
        try:
            await configure(client, fixed_audio=True, fixed_tools=fixed_tools)
            for index in range(2):
                if index:
                    await configure(client, fixed_audio=True, fixed_tools=fixed_tools)
                await client.stream_pcm(pcm_fixture(), realtime=False)
                await commit(client)
                await decide(client, report)
                await configure(client, speak=True, fixed_tools=fixed_tools)
                state, calls = await generate(client, modalities=["text", "audio"])
                report["responses"].append({"response_id": state.id, "status": state.status,
                    "audio_bytes": state.audio_bytes, "call_count": len(calls)})
                if not state.audio_bytes or calls:
                    raise RuntimeError("Speech not usable")
            report["status"] = "passed"
        finally:
            report["events"] = client.trace


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--fixed-tools", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print("Offline by default; --live runs at most four fixed-fixture generations.")
        raise SystemExit(0)
    report = {"status": "failed", "responses": [], "max_generations": 4,
              "timestamp_utc": datetime.now(timezone.utc).isoformat(), "fixed_tools": args.fixed_tools}
    try:
        asyncio.run(asyncio.wait_for(run(report, fixed_tools=args.fixed_tools), 90))
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    path = Path(__file__).resolve().parents[1] / ".runtime/harness-probes" / ("loop-" + uuid.uuid4().hex + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)
    raise SystemExit(0 if report["status"] == "passed" else 1)
