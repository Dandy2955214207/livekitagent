import asyncio
import json
from pathlib import Path
import sys
import unittest

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from livekit_vlm_agent.config import Settings
from livekit_vlm_agent.mobile_gateway import bootstrap_app, proxy_app


class MobileGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        backend = web.Application()

        async def echo(request):
            if request.headers.get("Upgrade", "").lower() == "websocket":
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.BINARY:
                        await ws.send_bytes(message.data)
                    elif message.type == aiohttp.WSMsgType.TEXT:
                        await ws.send_str(message.data)
                return ws
            return web.json_response({"path": request.path, "query": request.query_string,
                "authorization_present": bool(request.headers.get("Authorization"))})

        backend.router.add_route("*", "/{path:.*}", echo)
        self.backend = TestServer(backend)
        await self.backend.start_server()
        self.http = aiohttp.ClientSession()
        settings = Settings(_env_file=None, api_port=self.backend.port, mobile_probe_only=False)
        self.gateway = TestServer(proxy_app({}, settings, self.http, livekit_port=self.backend.port))
        await self.gateway.start_server()

    async def asyncTearDown(self):
        await self.gateway.close()
        await self.http.close()
        await self.backend.close()

    async def test_internal_callbacks_are_not_exposed_through_phone_gateway(self):
        async with self.http.post(self.gateway.make_url('/api/internal/sessions/example'), json={}) as response:
            self.assertEqual(response.status, 403)

    async def test_livekit_prefix_and_query_are_preserved_and_auth_reaches_backend(self):
        async with self.http.get(self.gateway.make_url('/livekit/rtc/validate?check=sample'),
            headers={"Authorization": "Bearer unit-test"}) as response:
            body = await response.json()
        self.assertEqual(body["path"], "/rtc/validate")
        self.assertEqual(body["query"], "check=sample")
        self.assertTrue(body["authorization_present"])

    async def test_bidirectional_binary_and_text_websocket_relay(self):
        async with self.http.ws_connect(self.gateway.make_url('/livekit/rtc')) as ws:
            await ws.send_bytes(bytes([1, 2, 3]))
            self.assertEqual((await ws.receive(timeout=2)).data, bytes([1, 2, 3]))
            await ws.send_str("protocol-test")
            self.assertEqual((await ws.receive(timeout=2)).data, "protocol-test")

    async def test_bootstrap_does_not_serve_private_keys_or_backend_api(self):
        server = TestServer(bootstrap_app({"origin": "https://example.test", "ca_sha256": "public-fingerprint"}))
        await server.start_server()
        try:
            for path in ("/server-key.pem", "/ca-key.pem", "/api/sessions", "/.env"):
                async with self.http.get(server.make_url(path)) as response:
                    self.assertEqual(response.status, 404)
        finally:
            await server.close()

    async def test_probe_mode_blocks_cloud_sessions_and_livekit(self):
        server = TestServer(proxy_app({}, Settings(_env_file=None, api_port=self.backend.port), self.http))
        await server.start_server()
        try:
            for path in ("/api/sessions", "/livekit/rtc", "/.env"):
                async with self.http.get(server.make_url(path)) as response:
                    self.assertEqual(response.status, 403)
        finally:
            await server.close()

    async def test_other_source_ip_cannot_use_the_phone_endpoint(self):
        server = TestServer(bootstrap_app({"origin":"https://example.test", "ca_sha256":"fingerprint",
            "computer_ip":"192.0.2.1", "phone_ip":"192.0.2.2"}))
        await server.start_server()
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(local_addr=("127.0.0.2",0))) as other:
                async with other.get(server.make_url('/')) as response:
                    self.assertEqual(response.status, 403)
        finally:
            await server.close()


if __name__ == '__main__':
    unittest.main()
