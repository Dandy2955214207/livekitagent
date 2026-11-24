"""One owned Node child per call; credentials travel on private stdin only."""

import asyncio
import base64
from collections import deque
import json
import os
import shutil
import subprocess
from functools import partial

from ..config import PROJECT_ROOT
from ..media.video import LatestVideo, encode_jpeg

BRIDGE = PROJECT_ROOT / 'harness/qwen-live/src/bridge.mjs'


def capabilities(settings):
    return {'engine': 'qwen-live-harness', 'version': '1.0.0', 'experimental': True,
            'provider': settings.model_provider,
            'proactive_enabled': settings.qwen_live_proactive_enabled,
            'memory': 'call_visual' if settings.qwen_live_memory_enabled and settings.model_provider == 'aliyun' else 'disabled',
            'playback_receipt': 'browser_audio_context',
            'max_generations': settings.qwen_live_max_generations,
            'max_monitor_generations': settings.qwen_live_max_monitor_generations,
            'max_call_seconds': settings.qwen_live_max_call_seconds}


class OfficialBridge:
    def __init__(self, settings, call_id, on_event, *, capture=None):
        self.endpoint = settings.qwen_endpoint()
        self.settings, self.call_id, self.on_event = settings, call_id, on_event
        self.capture = capture
        self.epoch = 1
        self._wake = asyncio.Event()
        self.video = LatestVideo(settings, self._wake, encoder=partial(encode_jpeg, guide_grid=settings.qwen_live_proactive_enabled))
        self.video.sample_fps = 1  # Official Monitor's protocol cadence.
        self._audio = asyncio.Queue(maxsize=100)
        self._controls = deque(maxlen=16)
        self._ready = asyncio.Event()
        self.failed = False
        self.closed = False
        self.process = None
        self.tasks = []

    async def start(self):
        if self.settings.qwen_missing():
            raise RuntimeError('Missing provider configuration: ' + ', '.join(self.settings.qwen_missing()))
        node = shutil.which('node')
        if not node or not (BRIDGE.parents[1] / 'node_modules/qwen-live-harness/dist/build-info.json').exists():
            raise RuntimeError('Install the pinned core: npm.cmd ci --prefix harness/qwen-live --ignore-scripts')
        # Do not leak unrelated service keys into this child or its command line.
        env = {key: value for key, value in os.environ.items() if key.upper() in {
            'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA'}}
        self.process = await asyncio.create_subprocess_exec(node, str(BRIDGE), cwd=BRIDGE.parents[1],
            env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=512000,
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        await self._write({'type': 'start', 'apiKey': self.settings.realtime_api_key(),
            'provider': self.settings.model_provider, 'endpoint': self.endpoint,
            'model': self.settings.qwen_model, 'voice': self.settings.qwen_voice,
            'proactiveEnabled': self.settings.qwen_live_proactive_enabled,
            'memoryEnabled': self.settings.qwen_live_memory_enabled,
            'memoryVisionModel': self.settings.qwen_memory_vision_model,
            'memoryEmbeddingModel': self.settings.qwen_memory_embedding_model,
            'testCapture': self.capture is not None,
            'maxCallSeconds': self.settings.qwen_live_max_call_seconds,
            'maxGenerations': self.settings.qwen_live_max_generations,
            'maxMonitorGenerations': self.settings.qwen_live_max_monitor_generations})
        self.tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._stderr()),
                      asyncio.create_task(self._writer())]
        async with asyncio.timeout(40):
            await self._ready.wait()
        if self.failed:
            raise RuntimeError('Official core connection failed')

    async def _write(self, event):
        payload = {**event, 'callId': self.call_id, 'epoch': self.epoch}
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + '\n').encode())
        await self.process.stdin.drain()

    async def _failure(self, code):
        if self.failed or self.closed:
            return
        self.failed = True
        self._ready.set()
        await self.on_event({'type': 'fatal', 'code': code, 'callId': self.call_id, 'epoch': self.epoch})

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                event = json.loads(line)
                if event.get('callId') != self.call_id or event.get('epoch') != self.epoch:
                    continue
                if event['type'] == 'capture_burst':
                    if event.get('enabled') is True:
                        self.video.burst()
                    else:
                        self.video.stop_burst()
                    continue  # Private capture control, never forwarded to the phone.
                if event['type'] == 'ready':
                    self._ready.set()
                elif event['type'] == 'fatal':
                    self.failed = True
                    self._ready.set()
                await self.on_event(event)
            if not self.closed:
                await self._failure('official_child_exited')
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._failure('official_output_failed')

    async def _stderr(self):
        # Never forward upstream diagnostic bodies, credentials, or media to logs.
        while await self.process.stderr.read(4096):
            pass

    async def _writer(self):
        try:
            while not self.closed and not self.failed:
                await self._wake.wait()
                self._wake.clear()
                if self._controls:
                    await self._write(self._controls.popleft())
                elif not self._audio.empty():
                    await self._write({'type': 'audio', 'audio': base64.b64encode(self._audio.get_nowait()).decode()})
                frame = self.video.take()
                if frame:
                    await self._write({'type': 'image', 'image': frame.image, 'width': frame.width, 'height': frame.height,
                        'grid': frame.signature, **({'frameId': self.capture.frames + 1} if self.capture else {})})
                    if self.capture:
                        self.capture.frame(frame)
                    self.video.sent(frame, frame.encoded_at)
                if self._controls or not self._audio.empty():
                    self._wake.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._failure('official_input_failed')

    async def push_audio(self, frame):
        if self.closed or self.failed:
            return
        if frame.sample_rate != 16000 or frame.num_channels != 1:
            return await self._failure('unsupported_input_format')
        try:
            self._audio.put_nowait(bytes(frame.data))
            self._wake.set()
        except asyncio.QueueFull:
            await self._failure('official_input_backlog')

    def push_video(self, frame):
        if not self.closed and not self.failed:
            self.video.offer(frame)

    def clear_video(self):
        self.video.clear()
        if self.capture:
            self.capture.event('camera.clear')
        self.control({'type': 'camera_clear'})

    def control(self, payload):
        if self.closed or self.failed:
            return False
        if len(self._controls) >= 16:
            return False
        self._controls.append(payload)
        self._wake.set()
        return True

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        await self.video.aclose()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.process:
            try:
                if self.process.returncode is None:
                    async with asyncio.timeout(1):
                        await self._write({'type': 'stop'})
                    self.process.stdin.close()
                    await asyncio.wait_for(self.process.wait(), 4)
            except (OSError, asyncio.TimeoutError):
                if self.process.returncode is None:
                    self.process.kill()
                    await self.process.wait()
        self._controls.clear()
        while not self._audio.empty():
            self._audio.get_nowait()
