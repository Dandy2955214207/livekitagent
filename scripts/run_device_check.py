"""Phone capability endpoint only: static page + TLS, no API/worker/model."""

import argparse
import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MOBILE = ROOT / ".runtime/mobile"
STOP = MOBILE / "probe.stop"
MARKER = MOBILE / "probe-running.json"


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


async def serve():
    from livekit_vlm_agent.mobile_gateway import main as gateway
    event = asyncio.Event()

    async def watch():
        while not STOP.exists():
            await asyncio.sleep(0.25)
        event.set()

    watcher = asyncio.create_task(watch())
    try:
        await gateway(event)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.stop:
        if not MARKER.exists():
            print("No capability gateway is recorded.")
            return
        STOP.write_text("stop", encoding="utf-8")
        for _ in range(40):
            if not MARKER.exists():
                print("Capability gateway stopped.")
                return
            time.sleep(0.25)
        raise SystemExit("Stop request pending.")
    profile = json.loads((MOBILE / "profile.json").read_text(encoding="utf-8-sig"))
    if not profile.get("phone_ip"):
        raise SystemExit("Prepare the phone IP allowlist before starting.")
    os.environ.update(API_PORT="18000", MOBILE_PROBE_ONLY="true")
    server = ThreadingHTTPServer(("127.0.0.1",18000), partial(QuietStaticHandler,directory=str(ROOT / "web/dist")))
    claimed = False
    serving = False
    try:
        with MARKER.open("x", encoding="utf-8") as marker:
            json.dump({"pid":os.getpid(),"mode":"capability-only","phone_ip":profile['phone_ip']},marker)
        claimed = True
        STOP.unlink(missing_ok=True)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        serving = True
        print(f"Phone setup: http://{profile['computer_ip']}:{profile['bootstrap_port']}/",flush=True)
        print(f"Device check: {profile['origin']}/device-check.html",flush=True)
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    finally:
        if serving:
            server.shutdown()
        server.server_close()
        if claimed:
            MARKER.unlink(missing_ok=True); STOP.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
