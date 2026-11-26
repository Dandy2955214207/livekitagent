import asyncio
import base64
import io
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from livekit import rtc
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.media.video import LatestVideo, SourceFrame, encode_jpeg
from livekit_vlm_agent.official.bridge import OfficialBridge


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


class VideoTests(unittest.IsolatedAsyncioTestCase):
    async def test_burst_wakes_normal_wait_samples_real_frames_and_returns_to_idle(self):
        samples = []
        video = LatestVideo(Settings(_env_file=None), asyncio.Event(),
                            encoder=lambda frame, _: (str(frame), 10, 10, 3))
        video.sample_fps = 1
        try:
            video.offer('first')
            await until(lambda: video.pending is not None)
            samples.append(video.take().encoded_at)
            video.offer('second')
            await asyncio.sleep(.05)  # Already sleeping for the normal 1 FPS deadline.
            video.burst(.6)
            await until(lambda: video.pending is not None)
            samples.append(video.take().encoded_at)
            self.assertLess(samples[1] - samples[0], .65)
            video.offer('third')
            await until(lambda: video.pending is not None)
            samples.append(video.take().encoded_at)
            self.assertLess(samples[2] - samples[1], .65)
            await asyncio.sleep(.25)
            self.assertIsNone(video.pending)  # No fabricated duplicate without new input.
            video.offer('fourth')
            await until(lambda: video.pending is not None)
            self.assertGreater(video.take().encoded_at - samples[2], .85)
            video.burst(); video.clear()
            self.assertEqual(video._burst_until, 0)
        finally:
            await video.aclose()

    async def test_jpeg_preserves_portrait_aspect_and_provider_size_limit(self):
        settings = Settings(_env_file=None)
        frame = rtc.VideoFrame(480, 640, rtc.VideoBufferType.RGBA, bytes([210, 20, 10, 255])*480*640)
        image, width, height, size = await asyncio.to_thread(encode_jpeg, frame, settings)
        self.assertEqual((width, height), (360, 480))
        self.assertLess(len(image), 256*1024)
        decoded = Image.open(io.BytesIO(base64.b64decode(image)))
        self.assertEqual(decoded.format, "JPEG")
        self.assertEqual(len(base64.b64decode(image)), size)
        _, width, height, _ = await asyncio.to_thread(encode_jpeg, SourceFrame(frame,90), settings)
        self.assertEqual((width, height), (640,480))

    async def test_latest_raw_replaces_backlog_and_inflight_encode_is_invalidated(self):
        started, release = threading.Event(), threading.Event()
        def slow(frame, settings):
            started.set()
            release.wait(2)
            return str(frame), 10, 10, 3
        video = LatestVideo(Settings(_env_file=None, video_fps=5), asyncio.Event(), encoder=slow)
        try:
            video.offer("old")
            await until(started.is_set)
            for i in range(100):
                video.offer(i)
            self.assertEqual(video.raw[0], 99)
            self.assertEqual(video.counts["replaced_raw"], 99)
            video.clear()
            release.set()
            await until(lambda: video.counts["expired_or_cleared"] == 1)
            self.assertIsNone(video.pending)
            self.assertIsNone(video.raw)
        finally:
            release.set()
            await video.aclose()

    async def test_stale_frame_is_discarded_before_send(self):
        video = LatestVideo(Settings(_env_file=None, video_max_frame_age_ms=100), asyncio.Event(),
                            encoder=lambda *args: ("test", 10, 10, 3))
        try:
            video.offer("x")
            await until(lambda: video.pending is not None)
            video.pending.received_at = time.monotonic() - 1
            self.assertIsNone(video.take())
            self.assertEqual(video.counts["expired"], 1)
        finally:
            await video.aclose()

    async def test_slow_encoding_does_not_block_official_bridge_audio(self):
        started, release = threading.Event(), threading.Event()
        def slow(*args):
            started.set(); release.wait(2)
            return "aW1hZ2U=", 10, 10, 5
        bridge = OfficialBridge(Settings(_env_file=None, model_provider="yibuapi"), "test", AsyncMock())
        bridge.video.encoder = slow
        with patch.object(bridge, "_write", new_callable=AsyncMock) as send:
            bridge.tasks.append(asyncio.create_task(bridge._writer()))
            try:
                bridge.push_video("frame")
                await until(started.is_set)
                await bridge.push_audio(rtc.AudioFrame(bytes(640), 16000, 1, 320))
                await until(lambda: send.await_count == 1)
                self.assertEqual(send.await_args.args[0]["type"], "audio")
                release.set()
                await until(lambda: bridge.video.counts["sent"] == 1)
                self.assertEqual(send.await_args.args[0]["type"], "image")
            finally:
                release.set()
                await bridge.aclose()


if __name__ == "__main__":
    unittest.main()
