import asyncio
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock

from aiohttp import CookieJar, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.harmony_gateway import create_app
from livekit_vlm_agent.invites import Invites
from livekit_vlm_agent.session import SessionRegistry


class DeploymentTests(unittest.IsolatedAsyncioTestCase):
    async def test_revocable_invites_apply_to_cookies_and_native_calls(self):
        with TemporaryDirectory() as root:
            store = Invites(root)
            code = b"unit-fixture-one-tester"
            digest = hashlib.sha256(code).hexdigest()
            record = Path(root, digest)
            record.write_text(str(int(time.time()) + 120))
            async with TestClient(TestServer(create_app("", invites=store)),
                                  cookie_jar=CookieJar(unsafe=True)) as client:
                origin = str(client.make_url("/")).rstrip("/")
                self.assertEqual((await client.post("/trial/access", data=code,
                                                    headers={"Origin": origin})).status, 204)
                self.assertEqual((await client.get("/trial/access")).status, 204)
                self.assertFalse(store.from_cookie(store.cookie(digest)[:-2] + "xx"))
                async with client.ws_connect("/v1/calls", headers={"Authorization": "Bearer " + code.decode()}) as ws:
                    await ws.send_json({"type": "start", "version": 1})
                    self.assertEqual((await ws.receive_json())["state"], "connecting")
                    record.unlink()
                    async with asyncio.timeout(7):
                        while (await ws.receive_json()).get("reason") != "access_expired":
                            pass
                self.assertEqual((await client.get("/trial/access")).status, 401)
                record.write_text("0")
                with self.assertRaises(WSServerHandshakeError):
                    await client.ws_connect("/v1/calls", headers={"Authorization": "Bearer " + code.decode()})

    async def test_startup_recovers_only_this_installations_rooms(self):
        rooms = [SimpleNamespace(name="voice-old", metadata="instance-a"),
                 SimpleNamespace(name="voice-foreign", metadata="instance-b"),
                 SimpleNamespace(name="other", metadata="instance-a")]
        service = SimpleNamespace(list_rooms=AsyncMock(return_value=SimpleNamespace(rooms=rooms)),
                                  delete_room=AsyncMock())
        registry = SessionRegistry(Settings(_env_file=None, service_owner="instance-a"), SimpleNamespace(room=service))
        await registry.recover()
        self.assertEqual(service.delete_room.await_count, 1)
        self.assertEqual(service.delete_room.await_args.args[0].room, "voice-old")
