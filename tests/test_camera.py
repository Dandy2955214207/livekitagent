from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from livekit import rtc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.media.camera import CameraInput


class CameraTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscribes_only_target_camera_and_clears_on_mute_unpublish_close(self):
        room = SimpleNamespace(remote_participants={}, on=Mock(), off=Mock())
        session = SimpleNamespace(clear_video=Mock())
        camera = CameraInput(room, "chosen", session)
        chosen, other = SimpleNamespace(identity="chosen"), SimpleNamespace(identity="other")
        publication = SimpleNamespace(sid="cam", source=rtc.TrackSource.SOURCE_CAMERA,
                                      set_subscribed=Mock(), track=None, muted=False)
        screen = SimpleNamespace(source=rtc.TrackSource.SOURCE_SCREENSHARE, set_subscribed=Mock())
        camera.published(publication, other)
        camera.published(screen, chosen)
        publication.set_subscribed.assert_not_called()
        screen.set_subscribed.assert_not_called()
        camera.published(publication, chosen)
        publication.set_subscribed.assert_called_once_with(True)
        camera.current = "cam"
        camera.muted(chosen, publication)
        session.clear_video.assert_called_once()
        self.assertIsNone(camera.current)
        camera.current = "new-camera"
        camera.unpublished(publication, chosen)
        self.assertEqual(camera.current, "new-camera", "Late old-track event must not clear new camera")
        await camera.aclose()
        self.assertEqual(room.off.call_count, 6)
        self.assertIsNone(camera.current)


if __name__ == "__main__":
    unittest.main()
