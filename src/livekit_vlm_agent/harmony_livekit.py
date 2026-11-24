"""Connect one native WebSocket call to the existing LiveKit session service."""

import asyncio
import base64
import json
import logging

from aiohttp import ClientError
from livekit import rtc

from .harmony_protocol import GatewayError, INPUT_SAMPLES, audio_packet, decode_jpeg, valid_uint

LOGGER = logging.getLogger(__name__)


class LiveCall:
    def __init__(self, http, api_url, livekit_url, send, fail):
        self.http, self.api_url, self.livekit_url = http, api_url, livekit_url
        self.send, self.fail = send, fail
        self.room = rtc.Room()
        self.room.on('data_received', self.agent_message)
        self.room.on('disconnected', self.room_disconnected)
        self.room.on('participant_disconnected', self.participant_left)
        self.agent_reachable = asyncio.Event()
        self.call = self.creation = self.monitor = None
        self.audio_source = self.microphone = self.video_source = self.camera = None
        self.camera_enabled = self.has_frame = False
        self.closing = False

    def room_disconnected(self, *_):
        if not self.closing:
            self.fail('upstream_closed')

    def participant_left(self, participant):
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            self.room_disconnected()

    @property
    def session_url(self):
        return f'{self.api_url}/api/sessions/{self.call["session_id"]}'

    @property
    def authorization(self):
        return {'Authorization': 'Bearer ' + self.call['access_token']}

    async def create_session(self):
        async with self.http.post(f'{self.api_url}/api/sessions', json={}) as response:
            if response.status == 409:
                raise GatewayError('busy')
            response.raise_for_status()
            self.call = await response.json()
        if self.call['harness_mode'] != 'qwen_live_official':
            raise GatewayError('unsupported_mode')

    async def status(self):
        async with self.http.get(self.session_url, headers=self.authorization) as response:
            response.raise_for_status()
            return (await response.json())['state']

    async def monitor_status(self):
        try:
            while True:
                await asyncio.sleep(2)
                if await self.status() != 'ready':
                    break
        except (ClientError, ValueError, KeyError, TimeoutError):
            pass
        self.fail('upstream_closed')

    async def start(self):
        try:
            async with asyncio.timeout(45):
                # If the phone leaves during POST, retain the reply so close can DELETE it.
                self.creation = asyncio.create_task(self.create_session())
                await asyncio.shield(self.creation)
                await self.room.connect(self.livekit_url or self.call['livekit_url'], self.call['token'],
                                        rtc.RoomOptions(auto_subscribe=False, single_peer_connection=True))
                while (state := await self.status()) == 'connecting':
                    await asyncio.sleep(0.35)
                if state != 'ready':
                    raise GatewayError('upstream_closed')
                self.audio_source = rtc.AudioSource(16000, 1, queue_size_ms=120)
                self.microphone = rtc.LocalAudioTrack.create_audio_track('harmony-microphone', self.audio_source)
                await self.room.local_participant.publish_track(
                    self.microphone, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
                # The API's ready state can arrive before the data channel receives agent events.
                await self.agent_reachable.wait()
        except TimeoutError as exc:
            raise GatewayError('connect_timeout') from exc
        self.monitor = asyncio.create_task(self.monitor_status())
        return self.call['session_id']

    def agent_message(self, packet):
        if (not self.call or packet.topic not in ('agent.official', 'agent.video') or not packet.participant
                or packet.participant.kind != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
                or packet.kind != rtc.DataPacketKind.KIND_RELIABLE):
            return
        try:
            event = json.loads(packet.data)
            if not isinstance(event, dict):
                return
            self.agent_reachable.set()
            if packet.topic == 'agent.video':
                if self.camera_enabled and not self.has_frame and event.get('enabled') and event.get('latest'):
                    self.has_frame = True
                    self.send({'type': 'camera_status', 'has_frame': True})
            elif event.get('callId') == self.call['session_id'] and event.get('epoch') == 1:
                self.forward(event)
        except (ValueError, TypeError, KeyError):
            # Bad upstream media must not result in a false playback acknowledgement.
            self.fail('upstream_closed')

    def forward(self, event):
        kind = event.get('type')
        output_id = event.get('outputId')
        sequence = event.get('sequence')
        if kind in ('audio', 'audio_end'):
            if not valid_uint(output_id, 1) or not valid_uint(sequence):
                raise ValueError('invalid upstream audio header')
            if kind == 'audio_end':
                self.send({'type': 'audio_end', 'output_id': output_id, 'sequence': sequence})
            else:
                encoded = event['audio']
                if not isinstance(encoded, str) or len(encoded) > 6400:
                    raise ValueError('invalid upstream audio length')
                pcm = base64.b64decode(encoded, validate=True)
                if not pcm or len(pcm) % 2:
                    raise ValueError('invalid upstream PCM')
                self.send(audio_packet(output_id, sequence, pcm))
        elif kind == 'audio_clear' and valid_uint(event.get('throughId')):
            self.send({'type': 'audio_clear', 'through_id': event['throughId']})
        elif kind == 'transcript' and event.get('role') in ('user', 'assistant'):
            if isinstance(event.get('id'), str) and isinstance(event.get('text'), str):
                self.send({key: event[key] for key in ('type', 'role', 'id', 'text')})
        elif kind in ('caption', 'notice') and isinstance(event.get('text'), str):
            self.send({'type': kind, 'text': event['text']})
        elif kind in ('speech_started', 'speech_stopped'):
            self.send({'type': 'speech', 'active': kind == 'speech_started'})
        elif kind == 'fatal':
            self.fail('upstream_closed')

    async def audio(self, pcm):
        await self.audio_source.capture_frame(rtc.AudioFrame(pcm, 16000, 1, INPUT_SAMPLES))

    async def video(self, jpeg):
        rgb = await asyncio.to_thread(decode_jpeg, jpeg)
        self.video_source.capture_frame(rtc.VideoFrame(640, 640, rtc.VideoBufferType.RGB24, rgb))

    async def set_microphone(self, enabled):
        if enabled:
            self.microphone.unmute()
        else:
            self.audio_source.clear_queue()
            self.microphone.mute()

    async def set_camera(self, enabled):
        if self.camera:
            if enabled:
                self.camera.unmute()
            else:
                self.camera.mute()
        elif enabled:
            self.video_source = rtc.VideoSource(640, 640)
            self.camera = rtc.LocalVideoTrack.create_video_track('harmony-camera', self.video_source)
            await self.room.local_participant.publish_track(
                self.camera, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
        self.camera_enabled = enabled
        self.has_frame = False

    async def playback(self, kind, output_id, sequence):
        payload = {'type': kind, 'outputId': output_id, 'callId': self.call['session_id'], 'epoch': 1}
        if kind == 'playback_completed':
            payload['sequence'] = sequence
        await self.room.local_participant.publish_data(json.dumps(payload), reliable=True, topic='agent.official.control')

    async def delete_session(self):
        for _ in range(2):
            try:
                async with self.http.delete(self.session_url, headers=self.authorization) as response:
                    if response.status == 404:
                        return
                    response.raise_for_status()
                    return
            except (ClientError, TimeoutError):
                continue
        LOGGER.warning('Harmony session cleanup could not reach its upstream API')

    async def close(self):
        self.closing = True
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        if self.creation:
            await asyncio.gather(self.creation, return_exceptions=True)
        try:
            if self.call:
                await self.delete_session()
        finally:
            try:
                if self.room.isconnected():
                    await asyncio.wait_for(self.room.disconnect(), 3)
            except (TimeoutError, RuntimeError) as exc:
                LOGGER.warning('Harmony room disconnect failed: %s', type(exc).__name__)
            finally:
                for source in (self.audio_source, self.video_source):
                    if source:
                        await source.aclose()
