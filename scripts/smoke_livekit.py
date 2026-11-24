"""Local-only room API smoke test; always cleans up its own random room."""

import asyncio
from array import array
from datetime import timedelta
import math
from pathlib import Path
import sys
import uuid

from livekit import api, rtc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from livekit_vlm_agent.config import Settings


async def check_media(settings: Settings, room_name: str) -> None:
    sender, receiver = rtc.Room(), rtc.Room()
    source = rtc.AudioSource(16000, 1, queue_size_ms=200)
    subscribed = asyncio.get_running_loop().create_future()
    stream = None
    send_task = None

    @receiver.on("track_subscribed")
    def on_track(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO and not subscribed.done():
            subscribed.set_result(track)

    def token(identity: str) -> str:
        return (
            api.AccessToken(
                settings.livekit_api_key.get_secret_value(),
                settings.livekit_api_secret.get_secret_value(),
            )
            .with_identity(identity)
            .with_ttl(timedelta(minutes=2))
            .with_grants(api.VideoGrants(room_join=True, room=room_name))
            .to_jwt()
        )

    async def send_tone() -> None:
        samples = array("h", [int(1000 * math.sin(2 * math.pi * 440 * n / 16000)) for n in range(320)])
        if sys.byteorder != "little":
            samples.byteswap()
        frame = rtc.AudioFrame(samples.tobytes(), 16000, 1, 320)
        for _ in range(100):
            await source.capture_frame(frame)
            await asyncio.sleep(0.02)

    try:
        await receiver.connect(settings.livekit_url, token("setup-receiver"))
        await sender.connect(settings.livekit_url, token("setup-sender"))
        track = rtc.LocalAudioTrack.create_audio_track("setup-synthetic-tone", source)
        await sender.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        remote_track = await asyncio.wait_for(subscribed, timeout=10)
        stream = rtc.AudioStream(remote_track, sample_rate=16000, num_channels=1, capacity=8)
        send_task = asyncio.create_task(send_tone())
        # Confirm actual decoded non-silent audio, not just successful signaling.
        async with asyncio.timeout(10):
            async for event in stream:
                if any(abs(value) > 50 for value in event.frame.data):
                    print("OK two RTC peers connected; synthetic PCM published/subscribed/decoded")
                    break
            else:
                raise RuntimeError("Audio stream ended before receiving the test signal")
    finally:
        if send_task is not None:
            send_task.cancel()
            await asyncio.gather(send_task, return_exceptions=True)
        if stream is not None:
            await stream.aclose()
        await asyncio.gather(sender.disconnect(), receiver.disconnect())
        await source.aclose()


async def main() -> None:
    settings = Settings()
    room_name = "setup-smoke-" + uuid.uuid4().hex
    async with api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key.get_secret_value(),
        api_secret=settings.livekit_api_secret.get_secret_value(),
    ) as client:
        created = False
        try:
            room = await client.room.create_room(
                api.CreateRoomRequest(name=room_name, empty_timeout=60, max_participants=2)
            )
            created = True
            assert room.name == room_name
            rooms = await client.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
            assert any(item.name == room_name for item in rooms.rooms)
            print("OK authenticated room create/list")
            await check_media(settings, room_name)
        finally:
            if created:
                await client.room.delete_room(api.DeleteRoomRequest(room=room_name))
                print("OK temporary room deleted")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=40))
