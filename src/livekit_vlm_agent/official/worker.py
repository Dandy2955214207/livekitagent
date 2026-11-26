"""Use LiveKit as the official core's Host; never run H2 or a second Omni loop."""

import asyncio
import json
import logging
import os

import aiohttp
from livekit import rtc
from livekit.agents import AutoSubscribe

from ..harmony_protocol import encode_agent_event
from ..media.camera import CameraInput
from ..mobile_test import MobileTests, WorkerCapture
from .bridge import OfficialBridge

logger = logging.getLogger(__name__)


def failure_message(event):
    code = str(event.get('code') or 'unknown')
    if code == 'model_access_denied':
        return '百炼拒绝了当前模型访问，请检查模型开通状态及账号/空间权限后重新加入。'
    if code == 'bridge_generation_budget':
        return '本次生成额度已用完，请重新加入。'
    if event.get('status') in (401, 403):
        return '模型服务鉴权失败，请检查配置后重新加入。'
    if code.startswith('realtime_recovery_'):
        return '模型连接未能恢复，请重新加入。'
    if code in ('audio_output_limit', 'playback_receipt_backlog', 'bridge_output_backlog'):
        return '语音播放未能跟上，本次通话已停止，请重新加入。'
    if event.get('stage') in ('websocket_close', 'websocket_transport'):
        return '模型连接已断开，请重新加入。'
    if event.get('stage') == 'provider':
        return '模型服务返回错误，本次通话未能继续，请重新加入。'
    return '通话发生异常，请重新加入。'


async def run_official_call(ctx, settings, metadata):
    call_id, identity = metadata['session_id'], metadata['identity']
    trial_event_key = os.getenv('HARMONY_TRIAL_EVENT_KEY', '').encode('ascii')
    done = asyncio.Event()
    bridge = camera = heartbeat = None
    audio_tasks = set()
    audio_sid = None
    failure = None
    cleaned = False
    capture = None
    if metadata.get('capture_id'):
        try:
            capture = WorkerCapture(MobileTests().directory(metadata['capture_id']))
            capture.event('call.worker_start', {'sessionId': call_id, 'provider': settings.model_provider,
                'model': settings.qwen_model, 'memoryModel': settings.qwen_memory_vision_model})
        except KeyError:
            logger.warning('Phone test directory unavailable for this call')

    async def report(state, error=None):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as http:
            async with http.post(f'http://127.0.0.1:{settings.api_port}/api/internal/sessions/{call_id}',
                headers={'Authorization': 'Bearer ' + metadata['worker_access']},
                json={'state': state, 'error': error}) as response:
                response.raise_for_status()
                return await response.json()

    async def publish(event, topic='agent.official'):
        if ctx.room.isconnected():
            wire = (encode_agent_event({**event, 'callId': call_id}, trial_event_key, topic)
                    if trial_event_key and topic in ('agent.official', 'agent.video')
                    else json.dumps(event, ensure_ascii=False))
            await ctx.room.local_participant.publish_data(wire, reliable=True,
                topic=topic, destination_identities=[identity])

    async def event_received(event):
        nonlocal failure
        if event['type'] == 'test_trace':
            if capture:
                capture.event(event['kind'], {'nodeSeq': event['seq'], 'nodeAt': event['at'], **event['data']})
            return
        if capture and event['type'] in {'ready', 'recovery', 'fatal', 'diagnostic', 'speech_started',
                'speech_stopped', 'scene', 'intent', 'requirements', 'control_result', 'latency', 'usage', 'tasks'}:
            capture.event('official.' + event['type'], {key: value for key, value in event.items()
                if key not in {'type', 'callId', 'epoch'}})
        if event['type'] == 'fatal':
            if failure:
                return
            failure = event['message'] = failure_message(event)
            logger.warning('official_terminal %s', json.dumps({k: event.get(k) for k in
                ('code', 'stage', 'status', 'pendingOutputs', 'outputBytes')}))
            logger.warning('official_call_failed code=%s', str(event.get('code', 'unknown'))[:80])
            done.set()
        elif event['type'] in ('recovery', 'diagnostic'):
            logger.info('official_%s %s', event['type'], json.dumps({k: event.get(k) for k in
                ('phase', 'source', 'stage', 'code', 'kind', 'status', 'closeCode')}))
        elif event['type'] == 'usage':
            logger.info('official_usage %s', json.dumps({key: event.get(key) for key in
                ('connections', 'generations', 'monitorGenerations', 'inputTokens', 'outputTokens')}))
        elif event['type'] == 'ready':
            logger.info('official_ready %s', json.dumps({'callId': call_id, 'memory': event.get('memory')}))
        elif event['type'] == 'memory':
            logger.info('official_memory %s', json.dumps({'callId': call_id, **{key: event[key] for key in
                ('phase', 'status', 'code', 'records', 'availableRecords', 'search', 'gaps', 'pending', 'submitted',
                 'reviewStatus', 'frames', 'firstFrame', 'lastFrame', 'ms', 'summary', 'recall', 'embedding',
                 'summaryTokens', 'recallTokens', 'embeddingTokens', 'scope', 'sources', 'candidates', 'truncated') if key in event}}))
            return
        elif event['type'] == 'latency':
            logger.info('official_latency %s', json.dumps({key: event.get(key) for key in
                ('callId', 'metric', 'ms', 'kind', 'traceId', 'taskId', 'outputId')}))
            return  # Diagnostic metadata stays local; it need not compete with phone audio.
        elif event['type'] == 'intent':
            # Diagnose missed/repeated interventions without retaining images,
            # user instructions, or the observer's scene descriptions.
            logger.info('official_intent %s', json.dumps({key: event.get(key) for key in
                ('taskId', 'generation', 'repeat', 'kind', 'status', 'phase', 'observation', 'reason',
                 'triggerCount', 'deliveredCount', 'evaluationMs', 'evidenceAgeMs')}))
        elif event['type'] == 'scene':
            logger.info('official_scene %s', json.dumps({key: event.get(key) for key in
                ('id', 'revision', 'mode', 'status', 'step', 'total', 'segments', 'reason', 'completedSteps', 'progress', 'delivery')}))
            if event.get('mode') == 'guide' and event.get('target'):
                logger.info('official_guide_target %s', json.dumps({key: event['target'].get(key) for key in
                    ('id', 'status', 'view', 'needsSelection', 'reason')}))
        elif event['type'] == 'requirements':
            logger.info('official_requirements %s', json.dumps([{key: row.get(key) for key in
                ('id', 'revision', 'kind', 'status', 'progress', 'delivery', 'deliveredCount')}
                for row in event.get('auxiliary', [])]))
        await publish(event)

    async def read_audio(track, sid):
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1, frame_size_ms=20, capacity=100)
        try:
            async for event in stream:
                if sid != audio_sid or done.is_set():
                    break
                await bridge.push_audio(event.frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            done.set()
        finally:
            await stream.aclose()

    @ctx.room.on('track_subscribed')
    def subscribed(track, publication, participant):
        nonlocal audio_sid
        if not bridge or participant.identity != identity or publication.source != rtc.TrackSource.SOURCE_MICROPHONE:
            return
        if audio_sid == publication.sid:
            return
        audio_sid = publication.sid
        for task in audio_tasks:
            task.cancel()
        task = asyncio.create_task(read_audio(track, audio_sid))
        audio_tasks.add(task)
        task.add_done_callback(audio_tasks.discard)

    @ctx.room.on('track_muted')
    def muted(participant, publication):
        if bridge and participant.identity == identity and publication.source == rtc.TrackSource.SOURCE_MICROPHONE:
            bridge.control({'type': 'input_mute', 'muted': True})

    @ctx.room.on('track_unmuted')
    def unmuted(participant, publication):
        if bridge and participant.identity == identity and publication.source == rtc.TrackSource.SOURCE_MICROPHONE:
            bridge.control({'type': 'input_mute', 'muted': False})

    @ctx.room.on('data_received')
    def data_received(packet):
        if (not bridge or packet.topic != 'agent.official.control' or not packet.participant
                or packet.participant.identity != identity or packet.kind != rtc.DataPacketKind.KIND_RELIABLE
                or len(packet.data) > 4096):
            return
        try:
            payload = json.loads(packet.data)
            if payload.get('callId') != call_id or payload.get('epoch') != 1:
                return
            allowed = {'playback_started', 'playback_completed', 'playback_blocked', 'task_stop', 'task_repeat', 'scene_control', 'requirement_control'}
            if payload.get('type') in allowed:
                if capture:
                    capture.event('phone.control_received', {key: payload[key] for key in
                        ('type', 'outputId', 'action', 'id', 'requestId', 'point', 'view') if key in payload})
                bridge.control(payload)
        except (ValueError, TypeError, AttributeError):
            pass

    @ctx.room.on('participant_disconnected')
    def participant_left(participant):
        if participant.identity == identity:
            done.set()

    @ctx.room.on('disconnected')
    def disconnected(*_):
        done.set()

    async def cleanup():
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        done.set()
        for task in list(audio_tasks):
            task.cancel()
        await asyncio.gather(*audio_tasks, return_exceptions=True)
        if camera:
            await camera.aclose()
        if bridge:
            await bridge.aclose()
        if capture:
            capture.event('call.worker_finished', {'error': failure})
            await capture.close()

    ctx.add_shutdown_callback(cleanup)
    try:
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        if capture:
            capture.event('room.worker_connected')
        await asyncio.wait_for(ctx.wait_for_participant(identity=identity), settings.join_timeout_seconds)
        bridge = OfficialBridge(settings, call_id, event_received, capture=capture)
        await bridge.start()
        camera = CameraInput(ctx.room, identity, bridge)
        for participant in ctx.room.remote_participants.values():
            for publication in participant.track_publications.values():
                if publication.track:
                    subscribed(publication.track, publication, participant)
        await report('ready')
        await publish({'state': 'listening', 'ready': True}, 'agent.status')

        async def keep_alive():
            while not done.is_set():
                await asyncio.sleep(1)
                video = bridge.video.snapshot()
                if capture and video.get('enabled'):
                    capture.event('camera.sampling', video)
                await publish(video, 'agent.video')
                current = await report('ready')
                if current['state'] != 'ready':
                    done.set()

        heartbeat = asyncio.create_task(keep_alive())
        waiter = asyncio.create_task(done.wait())
        deadline = asyncio.create_task(asyncio.sleep(settings.qwen_live_max_call_seconds))
        try:
            finished, _ = await asyncio.wait([heartbeat, waiter, deadline], return_when=asyncio.FIRST_COMPLETED)
            if deadline in finished:
                failure = '已达到官方 Harness 实验通话时限，请重新加入。'
            if heartbeat in finished:
                heartbeat.result()
        finally:
            for task in (waiter, deadline):
                task.cancel()
            await asyncio.gather(waiter, deadline, return_exceptions=True)
    except Exception as exc:
        if capture:
            capture.event('call.worker_error', {'code': type(exc).__name__})
        failure = failure or '官方 Harness 未能接通，请检查依赖和模型服务连接。'
    finally:
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await cleanup()
        try:
            await report('error' if failure else 'closed', failure)
        except Exception:
            pass
        ctx.shutdown(reason='official harness call finished')
