"""Short, explicit Qwen connection/config probe. Sends no media or response.create."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from livekit_vlm_agent.config import Settings


class ProbeError(Exception):
    pass


async def receive_until(ws, expected: str, report: dict, secret: str) -> dict:
    async with asyncio.timeout(12):
        for _ in range(40):
            event = json.loads(await ws.recv())
            event_type = event.get("type", "unknown")
            report["events"].append(event_type)
            print("RECV " + event_type)
            if event_type == "error":
                error = event.get("error", {})
                # No raw server payload, credentials, or media in output/log files.
                message = str(error.get("message", "Request rejected")).replace(secret, "[REDACTED]")
                raise ProbeError(str(error.get("code", "unknown")) + ": " + message[:400])
            if event_type == expected:
                return event
    raise ProbeError("Expected event not received: " + expected)


async def probe(settings: Settings, report: dict) -> None:
    secret = settings.realtime_api_key()
    endpoint = settings.qwen_endpoint() + "?" + urlencode({"model": settings.qwen_model})
    async with connect(
        endpoint,
        additional_headers=settings.realtime_headers(),
        open_timeout=12,
        close_timeout=3,
        max_size=2 * 1024 * 1024,
        proxy=None,
    ) as ws:
        await receive_until(ws, "session.created", report, secret)
        report["connection"] = "passed"
        session = {
            "modalities": ["text", "audio"],
            "turn_detection": None,
            "audio": {
                "input": {"format": {
                    "type": "pcm", "sample_rate": settings.audio_input_sample_rate,
                    "sample_format": "s16le", "channels": 1,
                    "packing": "interleaved", "channel_layout": "mono",
                }},
                "output": {
                    "voice": settings.qwen_voice,
                    "format": {"type": "pcm", "sample_rate": settings.audio_output_sample_rate},
                },
            },
        }
        await ws.send(json.dumps({"type": "session.update", "session": session}))
        await receive_until(ws, "session.updated", report, secret)
        report["manual_config"] = "passed"
        await ws.send(json.dumps({
            "type": "session.update",
            "session": {"turn_detection": {"type": settings.qwen_turn_detection}},
        }))
        await receive_until(ws, "session.updated", report, secret)
        report["turn_detection"] = settings.qwen_turn_detection
        report["turn_detection_config"] = "passed"
        print("OK connection and configuration accepted; no media/generation requested")


def main() -> int:
    settings = Settings()
    missing = settings.qwen_missing()
    if missing:
        print("PENDING " + ", ".join(missing) + "; no connection attempted")
        return 2
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model": settings.qwen_model,
        "provider": settings.model_provider,
        "region": settings.qwen_region,
        "voice": settings.qwen_voice,
        "events": [],
        "media_sent": False,
        "generation_requested": False,
        "status": "failed",
    }
    try:
        asyncio.run(asyncio.wait_for(probe(settings, report), timeout=40))
        report["status"] = "passed"
    except InvalidStatus as exc:
        report["error"] = "WebSocket HTTP " + str(exc.response.status_code)
    except ProbeError as exc:
        report["error"] = str(exc)
    except Exception as exc:
        report["error"] = type(exc).__name__
    report_path = ROOT / ".runtime/qwen-probe.json"
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if report["status"] != "passed":
        print("FAILED " + report["error"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
