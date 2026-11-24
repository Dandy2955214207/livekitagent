"""Local speech boundaries only; no transcription or remote generation."""

import asyncio
from collections import deque

from livekit.agents import vad


class SpeechInput:
    def __init__(self, detector, *, start, audio, end, failed):
        self.stream = detector.stream()
        self.start, self.audio, self.end, self.failed = start, audio, end, failed
        self.preroll = deque(maxlen=15)  # 300ms at the RoomIO 20ms frame size
        self.speaking = False
        self.received = self.processed = 0
        self.task = asyncio.create_task(self._read())

    def push(self, frame):
        self.received += frame.samples_per_channel
        if self.received - self.processed > 48000:
            self.failed("vad_backlog")
            return
        data = bytes(frame.data)
        self.preroll.append(data)
        if self.speaking:
            self.audio(data)
        self.stream.push_frame(frame)

    async def _read(self):
        try:
            async for event in self.stream:
                if event.type == vad.VADEventType.INFERENCE_DONE:
                    self.processed = event.samples_index
                elif event.type == vad.VADEventType.START_OF_SPEECH:
                    self.speaking = True
                    self.start(b"".join(self.preroll))
                elif event.type == vad.VADEventType.END_OF_SPEECH:
                    self.speaking = False
                    self.preroll.clear()
                    self.end()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failed("vad_failed")

    async def aclose(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await self.stream.aclose()
        self.preroll.clear()
