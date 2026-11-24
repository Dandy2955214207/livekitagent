"""Subscribe only the chosen participant's camera, with explicit invalidation."""

import asyncio
from livekit import rtc
from .video import SourceFrame


class CameraInput:
    def __init__(self, room, identity, session):
        self.room, self.identity, self.session = room, identity, session
        self.current = None
        self.tasks = set()
        self.closed = False
        self.handlers = {
            "track_published": self.published, "track_subscribed": self.subscribed,
            "track_unsubscribed": self.unsubscribed, "track_unpublished": self.unpublished,
            "track_muted": self.muted, "track_unmuted": self.unmuted,
        }
        for event, handler in self.handlers.items():
            room.on(event, handler)
        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                self.published(publication, participant)
                if publication.track:
                    self.subscribed(publication.track, publication, participant)

    def target(self, publication, participant):
        return (not self.closed and participant.identity == self.identity
                and publication.source == rtc.TrackSource.SOURCE_CAMERA)

    def published(self, publication, participant):
        if self.target(publication, participant):
            publication.set_subscribed(True)

    def reset(self):
        self.current = None
        self.session.clear_video()
        for task in self.tasks:
            task.cancel()

    def subscribed(self, track, publication, participant):
        if not self.target(publication, participant) or publication.muted:
            return
        if self.current == publication.sid:
            return
        self.reset()
        self.current = publication.sid
        task = asyncio.create_task(self.read(track, publication.sid))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def read(self, track, sid):
        stream = rtc.VideoStream(track, capacity=1)
        try:
            async for event in stream:
                if self.current == sid and not self.closed:
                    rotations = {rtc.VideoRotation.VIDEO_ROTATION_0: 0,
                                 rtc.VideoRotation.VIDEO_ROTATION_90: 90,
                                 rtc.VideoRotation.VIDEO_ROTATION_180: 180,
                                 rtc.VideoRotation.VIDEO_ROTATION_270: 270}
                    self.session.push_video(SourceFrame(event.frame, rotations.get(event.rotation, 0)))
        except asyncio.CancelledError:
            raise
        except Exception:
            # Video failure leaves the voice session usable.
            if self.current == sid:
                self.session.video.error = "摄像头媒体接收失败，请重新开启摄像头。"
        finally:
            await stream.aclose()

    def unpublished(self, publication, participant):
        if self.target(publication, participant) and self.current == publication.sid:
            self.reset()

    def unsubscribed(self, track, publication, participant):
        self.unpublished(publication, participant)

    def muted(self, participant, publication):
        self.unpublished(publication, participant)

    def unmuted(self, participant, publication):
        if publication.track:
            self.subscribed(publication.track, publication, participant)

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        for event, handler in self.handlers.items():
            self.room.off(event, handler)
        self.reset()
        await asyncio.gather(*self.tasks, return_exceptions=True)
