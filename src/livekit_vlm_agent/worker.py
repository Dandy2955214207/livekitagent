"""Explicitly dispatched AgentServer; one target participant per model session."""

import asyncio
import json
import logging

import aiohttp
from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, AutoSubscribe, JobContext, cli
from livekit.agents.voice import room_io

from .config import PROJECT_ROOT, Settings
from .coordination.direct import DirectState
from .providers.qwen_realtime import QwenRealtimeModel
from .providers.qwen_harness import QwenHarnessModel
from .providers.qwen_live import QwenLiveModel
from .media.camera import CameraInput

settings = Settings()
logger = logging.getLogger(__name__)
server = AgentServer(ws_url=settings.livekit_url,
    api_key=settings.livekit_api_key.get_secret_value(), api_secret=settings.livekit_api_secret.get_secret_value(),
    host="127.0.0.1", port=settings.worker_port, num_idle_processes=0,
    load_threshold=1.0, log_level="INFO", shutdown_process_timeout=10, session_end_timeout=15)


@server.rtc_session(agent_name=settings.agent_name)
async def entrypoint(ctx: JobContext):
    metadata = json.loads(ctx.job.metadata)
    if settings.harness_mode == 'qwen_live_official':
        from .official.worker import run_official_call
        return await run_official_call(ctx, settings, metadata)
    session_id, identity = metadata["session_id"], metadata["identity"]
    access = metadata["worker_access"]
    done = asyncio.Event()
    controlled = settings.harness_mode == "controlled_manual"
    direct = settings.harness_mode == "direct_background"
    model_class = (QwenHarnessModel if controlled else QwenLiveModel
                   if settings.harness_mode == "direct_background" else QwenRealtimeModel)
    model = model_class(settings)
    session = AgentSession(llm=model, vad=None, turn_handling={"turn_detection": "manual" if controlled else "realtime_llm"}, user_away_timeout=None)
    failure = None
    tasks = set()
    camera = None
    realtime = None

    async def report(state, error=None):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as http:
            async with http.post(f"http://127.0.0.1:{settings.api_port}/api/internal/sessions/{session_id}",
                headers={"Authorization": "Bearer " + access}, json={"state": state, "error": error}) as response:
                response.raise_for_status()
                return await response.json()

    async def publish(payload):
        if ctx.room.isconnected():
            await ctx.room.local_participant.publish_data(json.dumps(payload, ensure_ascii=False),
                reliable=True, topic="agent.status", destination_identities=[identity])

    async def publish_direct():
        if realtime and realtime.observer and ctx.room.isconnected():
            current = realtime.observer.snapshot()
            logger.info('background_metrics %s', json.dumps(current))
            payload = await realtime.direct_state.status(include_records=False)
            payload.pop('memory', None)
            payload['monitor'] = current
            await ctx.room.local_participant.publish_data(json.dumps(payload, ensure_ascii=False),
                reliable=True, topic='agent.direct', destination_identities=[identity])

    def schedule(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    @session.on("agent_state_changed")
    def state_changed(event):
        schedule(publish({"state": event.new_state}))

    @session.on("close")
    def closed(event):
        done.set()

    @session.on("error")
    def errored(event):
        nonlocal failure
        if not getattr(event.error, "recoverable", False):
            failure = str(getattr(event.error, "error", "实时模型连接异常，请重新加入；上下文将重置。"))
            done.set()

    @session.on("speech_created")
    def speech_created(event):
        if not controlled or not realtime or not realtime.speak_request:
            return
        segment_id = realtime.speak_request

        async def played():
            try:
                await event.speech_handle.wait_for_playout()
                realtime.playback_done(segment_id, interrupted=event.speech_handle.interrupted)
            except asyncio.CancelledError:
                raise
            except Exception:
                realtime.playback_done(segment_id, interrupted=True)

        schedule(played())

    @ctx.room.on("data_received")
    def data_received(packet):
        if (direct and realtime and realtime.direct_state and packet.topic == 'agent.direct.control'
                and packet.participant and packet.participant.identity == identity
                and packet.kind == rtc.DataPacketKind.KIND_RELIABLE and len(packet.data) <= 8192):
            async def apply_direct():
                try:
                    result = await realtime.direct_state.control(json.loads(packet.data))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    result = {'error': '本地操作未完成，请检查输入或稍后重试。'}
                await ctx.room.local_participant.publish_data(json.dumps(result, ensure_ascii=False),
                    reliable=True, topic='agent.direct', destination_identities=[identity])
            # Also bound scheduled work before entering the controller's own queue.
            if len(tasks) < 16:
                schedule(apply_direct())
            return
        if (controlled and realtime and packet.topic == "agent.control"
                and packet.participant and packet.participant.identity == identity
                and packet.kind == rtc.DataPacketKind.KIND_RELIABLE and len(packet.data) <= 4096):
            try:
                realtime.control(json.loads(packet.data))
            except (ValueError, TypeError):
                pass

    @ctx.room.on("participant_disconnected")
    def participant_left(participant):
        if participant.identity == identity:
            done.set()

    @ctx.room.on("disconnected")
    def room_disconnected(*args):
        done.set()

    async def cleanup():
        done.set()
        if camera:
            await camera.aclose()
        await session.aclose()
        await model.aclose()

    ctx.add_shutdown_callback(cleanup)
    heartbeat = None
    try:
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        await asyncio.wait_for(ctx.wait_for_participant(identity=identity), timeout=settings.join_timeout_seconds)
        await session.start(agent=Agent(instructions="请用中文简短自然地回答用户。"), room=ctx.room,
            room_options=room_io.RoomOptions(participant_identity=identity, text_input=False, video_input=False,
                audio_input=room_io.AudioInputOptions(sample_rate=16000, num_channels=1, frame_size_ms=20,
                    pre_connect_audio=False),
                audio_output=room_io.AudioOutputOptions(sample_rate=24000, num_channels=1),
                text_output=room_io.TextOutputOptions(sync_transcription=False),
                close_on_disconnect=True), record=False, session_host=False)
        if not model.sessions:
            raise RuntimeError("Realtime model session was not created")
        realtime = next(iter(model.sessions))
        await realtime.wait_ready()
        if direct:
            realtime.attach_direct_state(DirectState(session_id, memory_root=PROJECT_ROOT / '.runtime/memory'))
            logger.info('direct_background_config provider=%s model=%s observer=%s',
                        settings.model_provider, settings.qwen_model, bool(realtime.observer))
            if realtime.observer:
                realtime.observer.on_changed = lambda: schedule(publish_direct()) if len(tasks) < 16 else None
        if controlled:
            realtime.controller.state.session_id = session_id
            realtime.on_status = lambda payload: schedule(ctx.room.local_participant.publish_data(
                json.dumps(payload, ensure_ascii=False), reliable=True,
                topic="agent.task", destination_identities=[identity]))
            realtime.stop_playback = lambda: session.interrupt(force=True)
        camera = CameraInput(ctx.room, identity, realtime)
        await report("ready")
        await publish({"state": "listening", "ready": True})

        async def keep_alive():
            ticks = 0
            direct_snapshot = None
            while not done.is_set():
                await asyncio.sleep(1)
                video = realtime.video.snapshot()
                await ctx.room.local_participant.publish_data(json.dumps(video), reliable=True,
                    topic="agent.video", destination_identities=[identity])
                ticks += 1
                if direct and realtime.observer:
                    current = realtime.observer.snapshot()
                    if current != direct_snapshot:
                        direct_snapshot = current
                        await publish_direct()
                if ticks % 5 == 0:
                    logger.info("video_metrics %s", json.dumps(video))
                    result = await report("ready")
                    if result["state"] != "ready":
                        done.set()

        heartbeat = asyncio.create_task(keep_alive())
        waiter = asyncio.create_task(done.wait())
        finished, _ = await asyncio.wait([heartbeat, waiter], return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in finished:
            heartbeat.result()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
    except Exception as exc:
        logger.warning("Voice session ended with %s", type(exc).__name__)
        failure = failure or "Agent 未能接通或已断线，请重新加入；上下文将重置。"
    finally:
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await cleanup()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await report("error" if failure else "closed", failure)
        except Exception:
            pass
        ctx.shutdown(reason="voice session finished")


if __name__ == "__main__":
    cli.run_app(server)
