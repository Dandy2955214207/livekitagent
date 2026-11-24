"""Version 1 WebSocket API for the HarmonyOS client."""

import argparse
import asyncio
import hmac
import json
import logging
import math
import os
import ssl
import struct
import uuid

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from .harmony_protocol import CAMERA, INPUT_SAMPLES, JPEG_LIMIT, MIC, GatewayError, audio_packet, decode_jpeg, valid_uint

PATH = '/v1/calls'
JSON_LIMIT = 4096
SEND_TIMEOUT = 5
LOGGER = logging.getLogger(__name__)

ERRORS = {
    'bad_message': '消息格式或发送时机不符合协议。',
    'unsupported_version': '网关不支持此协议版本。',
    'start_timeout': '等待开始通话超时。',
    'busy': '已有通话正在进行或清理，请稍后重试。',
    'unsupported_mode': '会话服务需要启用官方 Harness 模式。',
    'connect_timeout': 'Agent 接通超时，请重新开始通话。',
    'upstream_unavailable': '无法接通通话服务，请稍后重试。',
    'upstream_closed': '通话服务已结束或断开，请重新开始通话。',
    'slow_client': '终端接收过慢，本次通话已结束。',
    'invalid_camera_frame': '画面不符合 JPEG 格式或分辨率要求。',
}


class Connection:
    def __init__(self, ws, make_backend):
        self.ws = ws
        self.outgoing = asyncio.Queue(128)
        self.stopped = asyncio.get_running_loop().create_future()
        self.backend = make_backend(self.send, self.fail)
        self.startup = None
        self.phase = 'new'
        self.microphone = True
        self.camera = False
        self.last_frame = float('-inf')

    def stop(self, reason, error=None):
        if not self.stopped.done():
            self.stopped.set_result((reason, error))

    def fail(self, code):
        self.stop('error', code)

    def send(self, message):
        if self.stopped.done():
            return
        try:
            self.outgoing.put_nowait(message)
        except asyncio.QueueFull:
            self.fail('slow_client')

    async def write(self):
        try:
            while True:
                message = await self.outgoing.get()
                send = self.ws.send_bytes if isinstance(message, bytes) else self.ws.send_json
                async with asyncio.timeout(SEND_TIMEOUT):
                    await send(message)
        except TimeoutError:
            self.fail('slow_client')
        except (ConnectionError, RuntimeError):
            self.stop('disconnect')

    async def start(self):
        try:
            call_id = await self.backend.start()
            self.phase = 'ready'
            self.send({'type': 'state', 'state': 'ready', 'call_id': call_id})
        except GatewayError as exc:
            self.fail(exc.code)
        except Exception as exc:
            LOGGER.warning('Harmony call start failed: %s', type(exc).__name__)
            self.fail('upstream_unavailable')

    async def control(self, message):
        if len(message.encode('utf-8')) > JSON_LIMIT:
            raise GatewayError('bad_message')
        data = json.loads(message)
        if not isinstance(data, dict):
            raise GatewayError('bad_message')
        kind = data.get('type')
        if kind == 'end':
            self.stop('client_end')
            return
        if kind == 'ping':
            if not valid_uint(data.get('id')):
                raise GatewayError('bad_message')
            self.send({'type': 'pong', 'id': data['id']})
            return
        if self.phase == 'new' and kind == 'start':
            if type(data.get('version')) is not int or data['version'] != 1:
                raise GatewayError('unsupported_version')
            self.phase = 'connecting'
            self.send({'type': 'state', 'state': 'connecting'})
            self.startup = asyncio.create_task(self.start())
            return
        if self.phase != 'ready':
            raise GatewayError('bad_message')
        if kind in ('set_microphone', 'set_camera'):
            enabled = data.get('enabled')
            if type(enabled) is not bool:
                raise GatewayError('bad_message')
            if kind == 'set_microphone':
                if enabled != self.microphone:
                    await self.backend.set_microphone(enabled)
                    self.microphone = enabled
            elif enabled != self.camera:
                await self.backend.set_camera(enabled)
                self.camera = enabled
                self.last_frame = float('-inf')
            self.send({'type': kind.removeprefix('set_') + '_state', 'enabled': enabled})
            return
        if kind in ('playback_started', 'playback_completed', 'playback_blocked'):
            if not valid_uint(data.get('output_id'), 1):
                raise GatewayError('bad_message')
            sequence = data.get('sequence')
            if kind == 'playback_completed' and not valid_uint(sequence):
                raise GatewayError('bad_message')
            await self.backend.playback(kind, data['output_id'], sequence)
            return
        raise GatewayError('bad_message')

    async def media(self, message):
        if self.phase != 'ready' or len(message) < 2:
            raise GatewayError('bad_message')
        kind, data = message[0], message[1:]
        if kind == MIC and len(data) == INPUT_SAMPLES * 2 and self.microphone:
            await self.backend.audio(data)
        elif kind == CAMERA and len(data) <= JPEG_LIMIT and self.camera:
            now = asyncio.get_running_loop().time()
            if now - self.last_frame < 0.2:
                return  # Avoid queueing stale frames, with margin for 4 FPS timing jitter.
            self.last_frame = now
            try:
                await self.backend.video(data)
            except GatewayError as exc:
                if exc.code != 'invalid_camera_frame':
                    raise
                self.send({'type': 'error', 'code': exc.code, 'message': ERRORS[exc.code], 'fatal': False})
        else:
            raise GatewayError('bad_message')

    async def read(self):
        start_deadline = asyncio.get_running_loop().time() + 10
        try:
            while not self.stopped.done():
                timeout = start_deadline - asyncio.get_running_loop().time() if self.phase == 'new' else None
                async with asyncio.timeout(timeout):
                    message = await self.ws.receive()
                if message.type == WSMsgType.TEXT:
                    await self.control(message.data)
                elif message.type == WSMsgType.BINARY:
                    await self.media(message.data)
                elif message.type == WSMsgType.ERROR:
                    self.fail('bad_message')
                else:
                    self.stop('disconnect')
        except TimeoutError:
            self.fail('start_timeout' if self.phase == 'new' else 'upstream_closed')
        except GatewayError as exc:
            self.fail(exc.code)
        except (ValueError, RecursionError):
            self.fail('bad_message')
        except Exception as exc:
            LOGGER.warning('Harmony call I/O failed: %s', type(exc).__name__)
            self.fail('upstream_closed')

    async def cleanup(self, reader, writer):
        tasks = [task for task in (reader, writer, self.startup) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self.backend.close()
        except Exception as exc:
            LOGGER.warning('Harmony call cleanup failed: %s', type(exc).__name__)

    async def run(self):
        reader, writer = asyncio.create_task(self.read()), asyncio.create_task(self.write())
        try:
            reason, error = await self.stopped
        finally:
            # aiohttp can cancel the request as soon as its socket closes.
            cleanup = asyncio.create_task(self.cleanup(reader, writer))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
        if self.ws.closed or reason == 'disconnect':
            return
        try:
            async with asyncio.timeout(SEND_TIMEOUT):
                if error:
                    await self.ws.send_json({'type': 'error', 'code': error, 'message': ERRORS[error], 'fatal': True})
                await self.ws.send_json({'type': 'state', 'state': 'ended', 'reason': reason})
                await self.ws.close()
        except (TimeoutError, ConnectionError, RuntimeError):
            pass


class MockCall:
    def __init__(self, send, scenario):
        self.send, self.scenario = send, scenario
        self.frames = self.output_id = 0
        self.pending = None
        self.has_frame = False

    async def start(self):
        if self.scenario == 'error':
            raise GatewayError('upstream_unavailable')
        return 'mock-' + uuid.uuid4().hex

    def reply(self):
        self.output_id += 1
        output = self.output_id
        self.pending = output
        self.send({'type': 'transcript', 'role': 'user', 'id': f'user-{output}', 'text': '模拟提问。'})
        self.send({'type': 'transcript', 'role': 'assistant', 'id': f'ai-{output}', 'text': '这是模拟网关的固定回复。'})
        pcm = struct.pack('<960h', *(int(2500 * math.sin(2 * math.pi * 440 * n / 24000)) for n in range(960)))
        for sequence in range(20):
            self.send(audio_packet(output, sequence, pcm))
        self.send({'type': 'audio_end', 'output_id': output, 'sequence': 20})

    async def audio(self, _):
        if self.pending is None:
            self.frames += 1
            if self.frames == 10:
                self.frames = 0
                self.reply()

    async def playback(self, kind, output_id, sequence):
        if output_id != self.pending:
            return
        if kind == 'playback_started' and self.scenario == 'interrupt' and output_id == 1:
            self.send({'type': 'audio_clear', 'through_id': output_id})
            self.reply()
        elif kind == 'playback_blocked' or (kind == 'playback_completed' and sequence == 20):
            self.pending = None

    async def video(self, data):
        await asyncio.to_thread(decode_jpeg, data)
        if not self.has_frame:
            self.has_frame = True
            self.send({'type': 'camera_status', 'has_frame': True})

    async def set_microphone(self, enabled):
        self.frames = 0

    async def set_camera(self, enabled):
        self.has_frame = False

    async def close(self):
        pass


def create_app(token, *, mode='mock', api_url='http://127.0.0.1:8000', livekit_url=None):
    if mode not in ('live', 'mock'):
        raise ValueError('Unknown Harmony gateway mode')
    if len(token) < 16 or not token.isascii() or any(ch.isspace() for ch in token):
        raise ValueError('HARMONY_GATEWAY_TOKEN must be at least 16 non-whitespace ASCII characters')

    app = web.Application()
    http = None
    connections = set()
    if mode == 'live':
        from .harmony_livekit import LiveCall

    async def http_context(_):
        nonlocal http
        async with ClientSession(timeout=ClientTimeout(total=5)) as session:
            http = session
            yield

    async def health(_):
        return web.json_response({'status': 'ok', 'mode': mode})

    async def calls(request):
        supplied = request.headers.get('Authorization', '').encode('utf-8')
        if not hmac.compare_digest(supplied, ('Bearer ' + token).encode('ascii')):
            return web.json_response({'error': 'unauthorized'}, status=401, headers={'WWW-Authenticate': 'Bearer'})
        scenario = request.query.get('scenario')
        if (scenario is not None and mode != 'mock') or scenario not in (None, 'normal', 'interrupt', 'error'):
            return web.json_response({'error': 'invalid_scenario'}, status=400)
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=JPEG_LIMIT + 1, compress=False)
        await ws.prepare(request)
        def make_backend(send, fail):
            if mode == 'live':
                return LiveCall(http, api_url.rstrip('/'), livekit_url, send, fail)
            return MockCall(send, scenario)

        connection = Connection(ws, make_backend)
        task = asyncio.current_task()
        connections.add((connection, task))
        try:
            await connection.run()
        finally:
            connections.discard((connection, task))
        return ws

    async def shutdown(_):
        active = list(connections)
        for connection, _ in active:
            connection.stop('server_shutdown')
        await asyncio.gather(*(task for _, task in active), return_exceptions=True)

    if mode == 'live':
        app.cleanup_ctx.append(http_context)
    app.on_shutdown.append(shutdown)
    app.router.add_get('/health', health)
    app.router.add_get(PATH, calls)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('mock', 'live'), default='mock')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8095)
    parser.add_argument('--api-url', default=os.getenv('HARMONY_GATEWAY_API_URL', 'http://127.0.0.1:8000'))
    parser.add_argument('--livekit-url', default=os.getenv('HARMONY_GATEWAY_LIVEKIT_URL'))
    parser.add_argument('--certfile')
    parser.add_argument('--keyfile')
    args = parser.parse_args()
    if bool(args.certfile) != bool(args.keyfile):
        parser.error('--certfile and --keyfile must be used together')
    ssl_context = None
    if args.certfile:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_context.load_cert_chain(args.certfile, args.keyfile)
    try:
        app = create_app(os.getenv('HARMONY_GATEWAY_TOKEN', ''), mode=args.mode,
                         api_url=args.api_url, livekit_url=args.livekit_url)
    except ValueError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO)
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_context, access_log=None,
                shutdown_timeout=20)


if __name__ == '__main__':
    main()
