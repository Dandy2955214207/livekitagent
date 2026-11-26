"""Drive the real native gateway against the isolated Session API and Worker."""

import asyncio
from collections import deque
from io import BytesIO
import json
import math
import struct

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from livekit_vlm_agent.harmony_gateway import create_app

UPSTREAM = 'http://127.0.0.1:8002'
TOKEN = 'harmony-rtc-fixture-token'


async def until(socket, check, pending=None):
    async with asyncio.timeout(12):
        while True:
            msg = await socket.receive()
            if msg.type == WSMsgType.BINARY:
                if check(msg.data):
                    return msg.data
                if pending is not None:
                    pending.append(msg.data)
                continue
            if msg.type != WSMsgType.TEXT:
                raise AssertionError(f'Unexpected socket state: {msg.type}')
            data = json.loads(msg.data)
            if check(data):
                return data
            if data.get('fatal'):
                raise AssertionError(data)
            if pending is not None:
                pending.append(data)


async def ready(socket):
    await socket.send_json({'type': 'start', 'version': 1})
    async with asyncio.timeout(50):
        while True:
            msg = await socket.receive_json()
            assert not msg.get('fatal'), msg
            if msg.get('state') == 'ready':
                return msg['call_id']


async def unused(client):
    async with asyncio.timeout(12):
        while True:
            async with client.session.get(f'{UPSTREAM}/api/health') as response:
                if (await response.json())['active_sessions'] == 0:
                    return
            await asyncio.sleep(0.1)


async def send_media(socket, data, count, interval):
    for _ in range(count):
        await socket.send_bytes(data)
        await asyncio.sleep(interval)


async def verify_call(socket):
    await ready(socket)
    pending = deque()
    await socket.send_json({'type': 'set_camera', 'enabled': True})
    await until(socket, lambda data: isinstance(data, dict) and data.get('type') == 'camera_state', pending)
    jpeg = BytesIO()
    Image.new('RGB', (360, 240), '#dd44dd').save(jpeg, 'JPEG')
    pcm = struct.pack('<320h', *(int(4500 * math.sin(n * 0.17)) for n in range(320)))
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(send_media(socket, b'\x02' + jpeg.getvalue(), 10, 0.26))
        tasks.create_task(send_media(socket, b'\x01' + pcm, 24, 0.02))
        await verify_reply(socket, pending)
    print('OK native JPEG and input PCM reached the Worker through real LiveKit tracks', flush=True)
    print('OK assistant PCM and transcript, and Worker acceptance of playback receipt', flush=True)


async def verify_reply(socket, pending):
    output_id = None
    sequence = 0
    transcript = receipt = saw_camera = saw_audio = False
    while not (transcript and receipt and saw_camera and saw_audio):
        try:
            data = pending.popleft() if pending else await until(socket, lambda _: True)
        except TimeoutError as exc:
            raise AssertionError(f'Missing RTC response: {sequence=}, {transcript=}, {receipt=}, {saw_camera=}, {saw_audio=}') from exc
        if isinstance(data, bytes):
            kind, received_id, index = struct.unpack_from('!BII', data)
            assert kind == 0x11 and index == sequence and len(data) > 9
            if output_id is None:
                output_id = received_id
                await socket.send_json({'type': 'playback_started', 'output_id': output_id})
            assert received_id == output_id
            sequence += 1
        elif data['type'] == 'transcript':
            transcript |= data['role'] == 'assistant' and bool(data['text'])
        elif data['type'] == 'audio_end':
            assert output_id and data['output_id'] == output_id and data['sequence'] == sequence
            await socket.send_json({'type': 'playback_completed', 'output_id': output_id, 'sequence': sequence})
        elif data['type'] == 'notice':
            receipt |= data['text'] == 'fixture_playback_completed'
            saw_audio |= data['text'] == 'fixture_input_audio'
        elif data['type'] == 'camera_status':
            saw_camera |= data['has_frame']
        elif data.get('fatal'):
            raise AssertionError(data)


async def main():
    async with TestClient(TestServer(create_app(TOKEN, mode='live', api_url=UPSTREAM))) as client:
        headers = {'Authorization': f'Bearer {TOKEN}'}
        async with client.session.ws_connect(client.make_url('/v1/calls'), headers=headers) as socket:
            await verify_call(socket)
            async with client.session.ws_connect(client.make_url('/v1/calls'), headers=headers) as other:
                await other.send_json({'type': 'start', 'version': 1})
                error = await until(other, lambda data: isinstance(data, dict) and data.get('type') == 'error')
                assert error['code'] == 'busy', error
            await socket.send_json({'type': 'end'})
            closed = await until(socket, lambda data: isinstance(data, dict) and data.get('state') == 'ended')
            assert closed['reason'] == 'client_end'
        await unused(client)
        async with client.session.ws_connect(client.make_url('/v1/calls'), headers=headers) as socket:
            await ready(socket)
        await unused(client)
        print('OK busy call rejected, and both hangup and lost sockets release sessions', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
