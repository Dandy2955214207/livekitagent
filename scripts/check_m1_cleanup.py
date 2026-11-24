"""Check the real local room list after M1 smoke tests; never delete other rooms."""

import asyncio
import json
from pathlib import Path
import sys
import time
import aiohttp

from livekit import api

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from livekit_vlm_agent.config import Settings


async def main():
    settings = Settings()
    async with api.LiveKitAPI(url=settings.livekit_url,
        api_key=settings.livekit_api_key.get_secret_value(),
        api_secret=settings.livekit_api_secret.get_secret_value()) as client:
        rooms = await client.room.list_rooms(api.ListRoomsRequest())
    remaining = [room.name for room in rooms.rooms if room.name.startswith("voice-")]
    async with aiohttp.ClientSession() as http:
        deadline = time.monotonic() + 20
        while True:
            async with http.get(f"http://127.0.0.1:{settings.worker_port}/worker") as response:
                response.raise_for_status()
                worker = await response.json()
            if worker.get("active_jobs", 0) == 0 or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.25)
        async with http.get(f"http://127.0.0.1:{settings.api_port}/api/health") as response:
            response.raise_for_status()
            backend = await response.json()
    # Protobuf JSON omits integer fields whose value is zero.
    active_jobs = worker.get("active_jobs", 0)
    active_api = backend.get("active_sessions")
    report = {"remaining_demo_rooms": len(remaining), "active_worker_jobs": active_jobs,
              "active_api_sessions": active_api,
              "passed": not remaining and active_jobs == 0 and active_api == 0}
    output = ROOT / ".runtime/m1-results/cleanup-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
