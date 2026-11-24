"""Latest-frame sampling. All ages use server receive time, never phone clocks."""

import asyncio
import base64
from collections import Counter, deque
from dataclasses import dataclass
import io
import json
import logging
import time

from livekit import rtc
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class SourceFrame:
    frame: rtc.VideoFrame
    rotation: int = 0


@dataclass
class EncodedFrame:
    image: str
    received_at: float
    encoded_at: float
    epoch: int
    width: int
    height: int
    jpeg_bytes: int
    encode_ms: float
    signature: object | None = None


def encode_jpeg(frame, settings, *, guide_grid=False):
    # Conversion, resizing, JPEG and Base64 all run outside the audio event loop.
    rotation = frame.rotation if isinstance(frame, SourceFrame) else 0
    frame = frame.frame if isinstance(frame, SourceFrame) else frame
    converted = frame if frame.type == rtc.VideoBufferType.RGBA else frame.convert(rtc.VideoBufferType.RGBA)
    image = Image.frombytes("RGBA", (frame.width, frame.height), bytes(converted.data)).convert("RGB")
    if rotation:
        image = image.rotate(-rotation, expand=True)
    image.thumbnail((settings.video_max_width, settings.video_max_height))
    for quality in (75, 55, 35):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        data = buffer.getvalue()
        # Provider limit: <=256 KiB AFTER Base64. Leave protocol headroom.
        if len(data) <= 190_000:
            result = base64.b64encode(data).decode("ascii"), image.width, image.height, len(data)
            if guide_grid:
                return (*result, base64.b64encode(image.resize((12, 8), Image.Resampling.BOX).tobytes()).decode("ascii"))
            return result
    raise ValueError("jpeg_too_large")


class LatestVideo:
    def __init__(self, settings, wake, *, encoder=encode_jpeg):
        self.settings, self.wake, self.encoder = settings, wake, encoder
        self.epoch = 0
        self.enabled = False
        self.raw = None
        self.pending = None
        self.counts = Counter()
        self.samples = deque(maxlen=128)
        self.last_sent = None
        self.error = None
        self.sample_fps = settings.video_fps
        self.on_sample = None
        self.on_encoded = None
        self.motion = None
        self._changed = asyncio.Event()
        self._rate_changed = asyncio.Event()
        self._burst_until = 0
        self._last_sample = 0
        self._task = asyncio.create_task(self._run())

    def burst(self, seconds=2):
        """Briefly sample fresh source frames at 4 FPS; never replay a cached frame."""
        if not self.enabled:
            return
        self._burst_until = time.monotonic() + min(2, max(0, seconds))
        self._rate_changed.set()

    def stop_burst(self):
        self._burst_until = 0
        self._rate_changed.set()

    def offer(self, frame):
        if self.raw is not None:
            self.counts["replaced_raw"] += 1
        self.enabled = True
        self.raw = (frame, time.monotonic(), self.epoch)
        self.counts["received"] += 1
        self._changed.set()

    def clear(self):
        self.epoch += 1
        self.enabled = False
        self.raw = self.pending = None
        self.last_sent = None
        self.error = None
        self.motion = None
        self.stop_burst()

    def fresh(self, frame):
        return (self.enabled and frame.epoch == self.epoch
                and (time.monotonic() - frame.received_at) * 1000 <= self.settings.video_max_frame_age_ms)

    async def _run(self):
        while True:
            await self._changed.wait()
            while True:
                self._rate_changed.clear()
                now = time.monotonic()
                fps = max(self.sample_fps, 4) if now < self._burst_until else self.sample_fps
                delay = self._last_sample + 1 / fps - now
                if delay <= 0:
                    break
                try:
                    async with asyncio.timeout(delay):
                        await self._rate_changed.wait()
                except asyncio.TimeoutError:
                    pass
            self._changed.clear()
            if self.raw is None:
                continue
            frame, received_at, epoch = self.raw
            self.raw = None
            self._last_sample = time.monotonic()
            start = time.monotonic()
            if (start - received_at) * 1000 > self.settings.video_max_frame_age_ms:
                self.counts["expired"] += 1
                continue
            try:
                (image, width, height, size, *extra) = await asyncio.to_thread(self.encoder, frame, self.settings)
            except Exception:
                if epoch != self.epoch:
                    continue
                self.counts["encode_errors"] += 1
                self.error = "图像编码失败，请关闭摄像头后重试。"
                continue
            now = time.monotonic()
            encoded = EncodedFrame(image, received_at, now, epoch, width, height, size, (now-start)*1000, extra[0] if extra else None)
            if not self.fresh(encoded):
                self.counts["expired_or_cleared"] += 1
                continue
            if self.pending is not None:
                self.counts["replaced_encoded"] += 1
            self.pending = encoded
            self.error = None
            self.counts["encoded"] += 1
            if self.on_encoded:
                try:
                    self.on_encoded(encoded)
                except Exception:
                    self.counts["fanout_errors"] += 1
            if self.on_sample:
                from .presentation import fingerprint
                signature = await asyncio.to_thread(fingerprint, encoded.image)
                encoded.signature = signature
                if self.fresh(encoded):
                    self.on_sample(encoded, signature)
            self.wake.set()

    def take(self):
        frame, self.pending = self.pending, None
        if frame is not None and not self.fresh(frame):
            self.counts["expired"] += 1
            return None
        return frame

    def sent(self, frame, started):
        now = time.monotonic()
        sample = {"received_mono": round(frame.received_at, 4),
                  "encoded_mono": round(frame.encoded_at, 4), "sent_mono": round(now, 4),
                  "age_ms": round((now-frame.received_at)*1000, 2),
                  "encode_ms": round(frame.encode_ms, 2), "send_ms": round((now-started)*1000, 2),
                  "width": frame.width, "height": frame.height, "jpeg_bytes": frame.jpeg_bytes,
                  "epoch": frame.epoch}
        self.counts["sent"] += 1
        self.samples.append(sample)
        logger.info("video_frame_metrics %s", json.dumps(sample))
        if self.fresh(frame):
            self.last_sent = now

    def snapshot(self):
        return {"enabled": self.enabled, "counts": dict(self.counts), "error": self.error,
                "motion": self.motion,
                "last_sent_ago_ms": round((time.monotonic()-self.last_sent)*1000) if self.last_sent else None,
                "latest": self.samples[-1] if self.samples else None}

    async def aclose(self):
        self.clear()
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
