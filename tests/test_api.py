from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.api import create_app
from livekit_vlm_agent.config import Settings


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.backend = SimpleNamespace(room=SimpleNamespace(create_room=AsyncMock(), delete_room=AsyncMock()),
            agent_dispatch=SimpleNamespace(create_dispatch=AsyncMock()))
        context = AsyncMock()
        context.__aenter__.return_value = self.backend
        self.patch = patch("livekit_vlm_agent.api.api.LiveKitAPI", return_value=context)
        self.patch.start()
        self.app = create_app(Settings(_env_file=None, livekit_api_key=SecretStr("api-test"),
            livekit_api_secret=SecretStr("api-unit-test-secret-12345678901234567890")))
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.patch.stop()

    def test_cross_origin_and_form_requests_are_rejected_before_dispatch(self):
        self.assertEqual(self.client.post("/api/sessions", json={}, headers={"Origin": "http://other.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/sessions", content="form=1").status_code, 415)
        self.backend.agent_dispatch.create_dispatch.assert_not_awaited()

    def test_management_access_worker_access_and_repeated_hangup(self):
        response = self.client.post("/api/sessions", json={})
        self.assertEqual(response.status_code, 201)
        call = response.json()
        route = "/api/sessions/" + call["session_id"]
        headers = {"Authorization": "Bearer " + call["access_token"]}
        self.assertEqual(self.client.get(route).status_code, 404)
        self.assertEqual(self.client.post("/api/internal/sessions/" + call["session_id"],
            json={"state": "ready"}, headers=headers).status_code, 404)
        self.assertEqual(self.client.get(route, headers=headers).json()["state"], "connecting")
        self.assertEqual(self.client.delete(route, headers=headers).json()["state"], "closed")
        self.assertEqual(self.client.delete(route, headers=headers).status_code, 200)
        self.backend.room.delete_room.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
