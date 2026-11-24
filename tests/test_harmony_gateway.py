import asyncio
from io import BytesIO
import struct
import unittest

from aiohttp import WSServerHandshakeError, WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from livekit_vlm_agent.harmony_gateway import create_app


TOKEN = 'harmony-local-fixture-token'
HEADERS = {'Authorization': f'Bearer {TOKEN}'}


class HarmonyGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = TestClient(TestServer(create_app(TOKEN)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def connect(self, scenario='normal'):
        return await self.client.ws_connect(f'/v1/calls?scenario={scenario}', headers=HEADERS)

    async def ready(self, socket):
        await socket.send_json({'type': 'start', 'version': 1})
        self.assertEqual(await socket.receive_json(timeout=2), {'type': 'state', 'state': 'connecting'})
        result = await socket.receive_json(timeout=2)
        self.assertEqual(result['state'], 'ready')
        self.assertTrue(result['call_id'].startswith('mock-'))

    async def reply(self, socket, output_id):
        for role in ('user', 'assistant'):
            self.assertEqual((await socket.receive_json(timeout=2))['role'], role)
        for sequence in range(20):
            message = await socket.receive(timeout=2)
            self.assertEqual(message.type, WSMsgType.BINARY)
            self.assertEqual(len(message.data), 9 + 1920)
            self.assertEqual(struct.unpack_from('!BII', message.data), (0x11, output_id, sequence))
            self.assertTrue(any(message.data[9:]))
        self.assertEqual(await socket.receive_json(timeout=2),
                         {'type': 'audio_end', 'output_id': output_id, 'sequence': 20})

    async def send_audio(self, socket, count=10):
        for _ in range(count):
            await socket.send_bytes(b'\x01' + bytes(640))

    async def test_auth_and_health(self):
        response = await self.client.get('/health')
        self.assertEqual(await response.json(), {'status': 'ok', 'mode': 'mock'})
        for headers, path in (({}, '/v1/calls'), (HEADERS, '/v1/calls?scenario=missing')):
            with self.assertRaises(WSServerHandshakeError) as caught:
                await self.client.ws_connect(path, headers=headers)
            self.assertEqual(caught.exception.status, 400 if headers else 401)

    async def test_round_trip_and_hangup(self):
        async with await self.connect() as socket:
            await self.ready(socket)
            await self.send_audio(socket)
            await self.reply(socket, 1)
            await socket.send_json({'type': 'playback_started', 'output_id': 1})
            await socket.send_json({'type': 'playback_completed', 'output_id': 1, 'sequence': 20})
            await self.send_audio(socket)
            await self.reply(socket, 2)
            await socket.send_json({'type': 'end'})
            self.assertEqual(await socket.receive_json(timeout=2),
                             {'type': 'state', 'state': 'ended', 'reason': 'client_end'})

    async def test_interrupt_suppresses_ack_for_old_output(self):
        async with await self.connect('interrupt') as socket:
            await self.ready(socket)
            await self.send_audio(socket)
            await self.reply(socket, 1)
            await socket.send_json({'type': 'playback_started', 'output_id': 1})
            self.assertEqual(await socket.receive_json(timeout=2), {'type': 'audio_clear', 'through_id': 1})
            await self.reply(socket, 2)
            await socket.send_json({'type': 'playback_completed', 'output_id': 1, 'sequence': 20})
            await self.send_audio(socket)
            await socket.send_json({'type': 'ping', 'id': 42})
            self.assertEqual(await socket.receive_json(timeout=2), {'type': 'pong', 'id': 42})

    async def test_camera_and_microphone_state(self):
        async with await self.connect() as socket:
            await self.ready(socket)
            for kind in ('camera', 'microphone'):
                enabled = kind == 'camera'
                await socket.send_json({'type': f'set_{kind}', 'enabled': enabled})
                self.assertEqual(await socket.receive_json(timeout=2), {'type': f'{kind}_state', 'enabled': enabled})
            await socket.send_bytes(b'\x02malformed')
            error = await socket.receive_json(timeout=2)
            self.assertEqual((error['code'], error['fatal']), ('invalid_camera_frame', False))
            await asyncio.sleep(0.22)
            picture = BytesIO()
            Image.new('RGB', (320, 640), 'red').save(picture, 'JPEG')
            await socket.send_bytes(b'\x02' + picture.getvalue())
            self.assertEqual(await socket.receive_json(timeout=2), {'type': 'camera_status', 'has_frame': True})
            await self.send_audio(socket, count=1)
            error = await socket.receive_json(timeout=2)
            self.assertEqual((error['code'], error['fatal']), ('bad_message', True))

    async def test_wrong_version_pre_ready_media_and_backend_failure(self):
        cases = [
            ('normal', {'type': 'start', 'version': True}, 'unsupported_version'),
            ('normal', {'type': 'start', 'version': 2}, 'unsupported_version'),
            ('normal', b'\x01' + bytes(640), 'bad_message'),
            ('error', {'type': 'start', 'version': 1}, 'upstream_unavailable'),
        ]
        for scenario, message, code in cases:
            with self.subTest(code=code):
                async with await self.connect(scenario) as socket:
                    if isinstance(message, bytes):
                        await socket.send_bytes(message)
                    else:
                        await socket.send_json(message)
                    while (result := await socket.receive_json(timeout=2))['type'] == 'state' and result['state'] == 'connecting':
                        pass
                    self.assertEqual((result['code'], result['fatal']), (code, True))
                    self.assertEqual((await socket.receive_json(timeout=2))['state'], 'ended')

    async def test_disconnect_during_session_creation_still_deletes_session(self):
        creating, proceed, deleted = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def create(_):
            creating.set()
            await proceed.wait()
            return web.json_response({'session_id': 'delayed', 'access_token': 'fixture-session',
                                      'harness_mode': 'qwen_live_official'}, status=201)

        async def delete(request):
            self.assertEqual(request.headers['Authorization'], 'Bearer fixture-session')
            deleted.set()
            return web.json_response({'state': 'closed'})

        upstream = web.Application()
        upstream.router.add_post('/api/sessions', create)
        upstream.router.add_delete('/api/sessions/delayed', delete)
        async with TestServer(upstream) as server:
            live = create_app(TOKEN, mode='live', api_url=str(server.make_url('/')))
            async with TestClient(TestServer(live)) as client:
                socket = await client.ws_connect('/v1/calls', headers=HEADERS)
                await socket.send_json({'type': 'start', 'version': 1})
                await asyncio.wait_for(creating.wait(), 2)
                await socket.close()
                proceed.set()
                await asyncio.wait_for(deleted.wait(), 2)
