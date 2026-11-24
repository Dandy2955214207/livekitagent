"""Bounded immutable encoded samples; consumers never remove each other's frames."""

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class VideoSample:
    frame_id: int
    camera_epoch: int
    received_at: float
    image: str


class VideoFanout:
    def __init__(self, *, max_frames=10, max_bytes=3_000_000, max_age=10):
        self.frames = deque()
        self.max_frames, self.max_bytes, self.max_age = max_frames, max_bytes, max_age
        self.bytes = 0
        self.sequence = 0

    def publish(self, frame):
        self.sequence += 1
        sample = VideoSample(self.sequence, frame.epoch, frame.received_at, frame.image)
        self.frames.append(sample)
        self.bytes += len(sample.image)
        self._trim(frame.received_at)

    def _trim(self, now):
        while self.frames and (len(self.frames) > self.max_frames or self.bytes > self.max_bytes
                               or now - self.frames[0].received_at > self.max_age):
            self.bytes -= len(self.frames.popleft().image)

    def read(self, *, after=0, camera_epoch, now):
        self._trim(now)
        return tuple(frame for frame in self.frames
                     if frame.frame_id > after and frame.camera_epoch == camera_epoch)

    def clear(self):
        self.frames.clear()
        self.bytes = 0
