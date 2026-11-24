"""Opt-in, local evidence for one phone test attempt."""

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
import uuid

from .config import PROJECT_ROOT

ROOT = PROJECT_ROOT / ".runtime/mobile-tests"
MAX_RUN_BYTES = 256 * 1024 * 1024
MAX_BROWSER_BYTES = 4 * 1024 * 1024
RUN_ID = re.compile(r"[0-9a-f]{32}")
FRAME_KEY = re.compile(r"[0-9a-f]{64}")


def timestamp():
    return time.time_ns() // 1_000_000


def line(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


class MobileTests:
    def __init__(self, root=None):
        self.root = Path(root if root is not None else os.environ.get("LIVEKIT_MOBILE_TEST_ROOT", ROOT))

    def directory(self, run_id):
        if not RUN_ID.fullmatch(run_id or ""):
            raise KeyError(run_id)
        path = self.root / run_id
        if not (path / "run.json").is_file():
            raise KeyError(run_id)
        return path

    def start(self):
        run_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        path = self.root / run_id
        path.mkdir(parents=True)
        started_at = timestamp()
        (path / ".capture-key").write_text(hashlib.sha256(token.encode()).hexdigest(), encoding="ascii")
        manifest = path / "run.tmp"
        manifest.write_bytes(line({"id": run_id, "startedAt": started_at, "schema": 1}))
        manifest.replace(path / "run.json")
        return {"id": run_id, "token": token, "serverAt": timestamp()}

    def authorize(self, run_id, token):
        path = self.directory(run_id)
        expected = (path / ".capture-key").read_text(encoding="ascii")
        if not secrets.compare_digest(expected, hashlib.sha256(token.encode()).hexdigest()):
            raise KeyError(run_id)
        return path

    def bind(self, path):
        # Exclusive creation prevents two calls from sharing a trace.
        with (path / "bound").open("xb"):
            pass

    def list(self):
        if not self.root.is_dir():
            return []
        runs = []
        for file in self.root.glob("*/run.json"):
            try:
                runs.append(json.loads(file.read_bytes()))
            except (OSError, ValueError):
                continue
        return sorted(runs, key=lambda run: run["startedAt"], reverse=True)


def append_events(path, rows, limit):
    data = b"".join(line(row) for row in rows)
    if path.exists() and path.stat().st_size + len(data) > limit:
        raise ValueError("capture_limit")
    with path.open("ab") as stream:
        stream.write(data)


class WorkerCapture:
    """Write bounded batches off the media loop. Sequence gaps remain visible."""

    def __init__(self, directory):
        self.directory = directory
        self.queue = asyncio.Queue(maxsize=256)
        self.sequence = 0
        self.dropped = 0
        self.frames = 0
        self.frame_bytes = 0
        self.closed = False
        self.writer = asyncio.create_task(self._write())

    def event(self, kind, data=None, image=None):
        if self.closed or self.writer.done():
            return
        self.sequence += 1
        row = {"source": "worker", "seq": self.sequence, "at": timestamp(), "kind": kind, "data": data or {}}
        try:
            self.queue.put_nowait((row, image))
        except asyncio.QueueFull:
            self.dropped += 1

    def frame(self, frame):
        self.frames += 1
        self.event("frame.bridge", {
            "frameId": self.frames, "cameraEpoch": frame.epoch, "width": frame.width, "height": frame.height,
            "bytes": frame.jpeg_bytes, "encodeMs": frame.encode_ms,
            "receiveToEncodeMs": round((frame.encoded_at - frame.received_at) * 1000),
            "ageMs": round((time.monotonic() - frame.received_at) * 1000),
        }, frame.image)
        return self.frames

    def _persist(self, batch):
        rows = []
        for row, image in batch:
            if image is not None:
                decoded = base64.b64decode(image, validate=True)
                key = hashlib.sha256(decoded).hexdigest()
                row["data"]["frameKey"] = key
                path = self.directory / "frames" / (key + ".jpg")
                if not path.exists():
                    if self.frame_bytes + len(decoded) > MAX_RUN_BYTES:
                        row["data"]["imageMissing"] = "capture_limit"
                    else:
                        path.parent.mkdir(exist_ok=True)
                        temporary = path.with_suffix(".tmp")
                        temporary.write_bytes(decoded)
                        temporary.replace(path)
                        self.frame_bytes += len(decoded)
            rows.append(row)
        append_events(self.directory / "worker.jsonl", rows, 64 * 1024 * 1024)

    async def _write(self):
        try:
            while (first := await self.queue.get()) is not None:
                batch = [first]
                closing = False
                while len(batch) < 32 and not self.queue.empty():
                    item = self.queue.get_nowait()
                    if item is None:
                        closing = True
                        break
                    batch.append(item)
                await asyncio.to_thread(self._persist, batch)
                if closing:
                    break
        except (OSError, ValueError):
            try:
                await asyncio.to_thread((self.directory / "write-error").touch)
            except OSError:
                pass

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.writer.done():
            return
        row = {"source": "worker", "seq": self.sequence + 1, "at": timestamp(),
               "kind": "capture.worker_end", "data": {"dropped": self.dropped}}
        try:
            async with asyncio.timeout(3):
                await self.queue.put((row, None))
                await self.queue.put(None)
                await self.writer
        except TimeoutError:
            self.writer.cancel()
            await asyncio.gather(self.writer, return_exceptions=True)
