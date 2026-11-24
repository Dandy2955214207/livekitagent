"""Qwen's tested protocol mapped onto the locked LiveKit realtime interface."""

import asyncio
import base64
from dataclasses import dataclass, field
import json
import logging
import re
import time

from livekit import rtc
from livekit.agents import llm, utils
from livekit.agents.types import NOT_GIVEN
from livekit.agents.utils import is_given

from ..config import Settings
from ..media.video import LatestVideo
from .qwen_client import QwenClient

logger = logging.getLogger(__name__)


@dataclass
class Generation:
    messages: utils.aio.Chan = field(default_factory=utils.aio.Chan)
    text: utils.aio.Chan = field(default_factory=utils.aio.Chan)
    audio: utils.aio.Chan = field(default_factory=utils.aio.Chan)
    queued_bytes: int = 0
    message_id: str | None = None
    closed: bool = False

    def close(self):
        if not self.closed:
            self.closed = True
            for channel in (self.messages, self.text, self.audio):
                channel.close()


class QwenRealtimeModel(llm.RealtimeModel):
    def __init__(self, settings: Settings | None = None):
        super().__init__(capabilities=llm.RealtimeCapabilities(
            message_truncation=False, turn_detection=True, user_transcription=True,
            auto_tool_reply_generation=False, audio_output=True, manual_function_calls=False,
            can_disable_turn_detection=True, mutable_instructions=True,
        ))
        self.settings = settings or Settings()
        self.sessions = set()

    @property
    def model(self):
        return self.settings.qwen_model

    @property
    def provider(self):
        return "qwen"

    def session(self, *, turn_detection_disabled=False):
        session = QwenRealtimeSession(self, manual=turn_detection_disabled)
        self.sessions.add(session)
        return session

    async def aclose(self):
        await asyncio.gather(*(session.aclose() for session in list(self.sessions)))


class QwenRealtimeSession(llm.RealtimeSession):
    def __init__(self, model, *, manual=False):
        super().__init__(model)
        self.settings = model.settings
        self._manual = manual
        self._chat = llm.ChatContext()
        self._tools = llm.ToolContext([])
        self._instructions = "请用中文简短自然地回答，每次尽量不超过两句话。"
        self._ready = asyncio.Event()
        self._closed = False
        self._failed = False
        self._provider_interrupt = False
        self._input = asyncio.Queue(maxsize=100)
        self._wake = asyncio.Event()
        self._audio_sent = False
        self.video = LatestVideo(self.settings, self._wake)
        self._generations = {}
        self._pending_reply = None
        self._client = QwenClient(self.settings, streaming=True, on_event=self._on_event)
        self._main = asyncio.create_task(self._run())

    @property
    def chat_ctx(self):
        return self._chat

    @property
    def tools(self):
        return self._tools

    async def _run(self):
        try:
            async with self._client:
                await self._client.configure(vad=not self._manual, instructions=self._instructions)
                self._ready.set()
                while True:
                    await self._wake.wait()
                    self._wake.clear()
                    # One ordered writer: alternate an audio/control item with at most
                    # one latest image. JPEG encoding never holds this writer.
                    if not self._input.empty():
                        kind, payload = self._input.get_nowait()
                        if kind == "audio":
                            await self._client.send({"type": "input_audio_buffer.append",
                                "audio": base64.b64encode(payload).decode("ascii")})
                            self._audio_sent = True
                        else:
                            await self._client.send(payload)
                    if self._audio_sent:
                        frame = self.video.take()
                        if frame is not None:
                            started = time.monotonic()
                            await self._client.send({"type": "input_image_buffer.append", "image": frame.image})
                            self.video.sent(frame, started)
                    if not self._input.empty():
                        self._wake.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            self._fail(f"HTTP_{status}" if status else type(exc).__name__)
        finally:
            self._ready.set()
            await self.video.aclose()
            for generation in self._generations.values():
                generation.close()

    async def wait_ready(self):
        async with asyncio.timeout(20):
            await self._ready.wait()
        if self._failed or self._closed:
            raise llm.RealtimeError("模型连接未就绪，请重新加入通话。")

    def _fail(self, reason):
        if not self._failed and not self._closed:
            self._failed = True
            message = ("Qwen 云端内容检查未通过（data_inspection_failed），本次连接已结束。"
                       if reason == "data_inspection_failed" else
                       f"实时模型连接失败（{reason}），请重新加入通话。")
            self.emit("error", llm.RealtimeModelError(
                type="realtime_model_error", timestamp=time.time(), label=self.realtime_model.label,
                error=llm.RealtimeError(message), recoverable=False,
            ))
            if asyncio.current_task() is not self._main:
                self._main.cancel()

    def _enqueue(self, kind, payload):
        if self._closed or self._failed:
            return
        try:
            self._input.put_nowait((kind, payload))
            self._wake.set()
        except asyncio.QueueFull:
            self._fail("input_backlog")

    async def update_instructions(self, instructions):
        self._instructions = instructions
        await self.wait_ready()
        cursor = self._client.cursor
        self._enqueue("event", {"type": "session.update", "session": {"instructions": instructions}})
        await self._client.wait_type("session.updated", after=cursor, timeout=12)

    async def update_chat_ctx(self, chat_ctx):
        messages = [item for item in chat_ctx.items if item.type == "message" and item.role in {"user", "assistant"}]
        if messages and self._chat.items != chat_ctx.items:
            raise llm.RealtimeError("首版不支持重放或修改远端历史；请新建会话。")

    async def update_tools(self, tools):
        if tools:
            raise llm.RealtimeError("M1 不支持工具调用。")

    def update_options(self, *, tool_choice=NOT_GIVEN):
        if is_given(tool_choice) and tool_choice not in (None, "none", "auto"):
            raise llm.RealtimeError("M1 不支持工具调用。")

    def push_audio(self, frame):
        # RoomIO uses its stateful resampler to deliver 16 kHz mono 20 ms frames.
        if frame.sample_rate != 16000 or frame.num_channels != 1:
            self._fail("unsupported_input_format")
            return
        self._enqueue("audio", bytes(frame.data))

    def push_video(self, frame):
        if not self._closed and not self._failed:
            self.video.offer(frame)

    def clear_video(self):
        self.video.clear()

    def commit_audio(self):
        self._enqueue("event", {"type": "input_audio_buffer.commit"})

    def clear_audio(self):
        self._enqueue("event", {"type": "input_audio_buffer.clear"})

    def generate_reply(self, *, instructions=NOT_GIVEN, tool_choice=NOT_GIVEN, tools=NOT_GIVEN):
        future = asyncio.get_running_loop().create_future()
        if is_given(instructions) or (is_given(tools) and tools):
            future.set_exception(llm.RealtimeError("M1 仅支持当前语音会话的回答。"))
            return future
        if self._pending_reply is not None:
            future.set_exception(llm.RealtimeError("已有待创建的回答。"))
            return future
        self._pending_reply = future
        self._enqueue("event", {"type": "response.create"})
        return future

    def interrupt(self):
        rid = self._client.gate.active_response
        if rid is None:
            return
        self._client.gate.cancel(rid)
        generation = self._generations.get(rid)
        if generation:
            generation.close()
        state = self._client.responses.get(rid)
        if state and state.done_at is None and not self._provider_interrupt:
            self._enqueue("event", {"type": "response.cancel"})

    def truncate(self, **kwargs):
        raise llm.RealtimeError("未验证远端精确历史裁剪，能力声明已关闭。")

    async def _audio_stream(self, generation):
        async for frame in generation.audio:
            generation.queued_bytes -= len(frame.data) * 2
            yield frame

    async def _empty(self):
        if False:
            yield None

    def _on_event(self, event, pcm):
        if self._closed:
            return
        kind = event.get("type")
        rid = event.get("response_id")
        generation = self._generations.get(rid)
        if kind == "response.created":
            rid = event["response"]["id"]
            generation = Generation()
            self._generations[rid] = generation
            user_initiated = self._pending_reply is not None
            created = llm.GenerationCreatedEvent(message_stream=generation.messages,
                function_stream=self._empty(), user_initiated=user_initiated, response_id=rid)
            if self._pending_reply is not None:
                if not self._pending_reply.done():
                    self._pending_reply.set_result(created)
                self._pending_reply = None
            self.emit("generation_created", created)
            for old_id, old in list(self._generations.items()):
                if old_id != rid and old.closed:
                    self._generations.pop(old_id)
        elif kind == "response.output_item.added" and generation and event.get("item", {}).get("type") == "message":
            generation.message_id = event["item"]["id"]
            modalities = asyncio.get_running_loop().create_future()
            modalities.set_result(["text", "audio"])
            generation.messages.send_nowait(llm.MessageGeneration(
                message_id=generation.message_id, text_stream=generation.text,
                audio_stream=self._audio_stream(generation), modalities=modalities))
        elif kind in {"response.audio_transcript.delta", "response.text.delta"} and generation and not generation.closed:
            generation.text.send_nowait(event.get("delta", ""))
        elif kind == "response.audio.delta" and pcm and generation and not generation.closed:
            if generation.queued_bytes + len(pcm) > self.settings.output_buffer_max_ms * 48:
                self.interrupt()
                self._fail("output_backlog")
                return
            generation.queued_bytes += len(pcm)
            for offset in range(0, len(pcm), 960):
                chunk = pcm[offset:offset + 960]
                generation.audio.send_nowait(rtc.AudioFrame(chunk, 24000, 1, len(chunk) // 2))
        elif kind == "response.done":
            rid = event["response"]["id"]
            generation = self._generations.get(rid)
            if generation:
                generation.close()
                state = self._client.responses.get(rid)
                if state and state.text and generation.message_id:
                    self._chat.add_message(id=generation.message_id, role="assistant", content=state.text)
        elif kind == "input_audio_buffer.speech_started":
            self._provider_interrupt = True
            try:
                self.emit("input_speech_started", llm.InputSpeechStartedEvent())
            finally:
                self._provider_interrupt = False
        elif kind == "input_audio_buffer.speech_stopped":
            logger.info("turn_video_metrics %s", json.dumps(self.video.snapshot()))
            self.emit("input_speech_stopped", llm.InputSpeechStoppedEvent(user_transcription_enabled=True))
        elif kind == "conversation.item.input_audio_transcription.completed":
            text, item_id = event.get("transcript", ""), event["item_id"]
            if text:
                self._chat.add_message(id=item_id, role="user", content=text)
            self.emit("input_audio_transcription_completed", llm.InputTranscriptionCompleted(
                item_id=item_id, transcript=text, is_final=True))
        elif kind == "error":
            details = {key: event.get("error", {}).get(key) for key in ("code", "type", "message", "param")}
            safe = json.dumps(details, ensure_ascii=False)
            for secret in (self.settings.qwen_api_key, self.settings.yibuapi_api_key, self.settings.livekit_api_key, self.settings.livekit_api_secret):
                value = secret.get_secret_value()
                if value:
                    safe = safe.replace(value, "[REDACTED]")
            safe = re.sub(r"[A-Za-z0-9+/=]{120,}", "[MEDIA_REDACTED]", safe)
            logger.warning("qwen_error %s", safe[:800])
            # A server-side auto-cancel can race our explicit cancellation.
            code = event.get("error", {}).get("code", "provider_error")
            if code not in {"response_cancel_not_active", "response_not_active"}:
                self._fail(str(code))
        elif kind == "client.closed" and not self._closed:
            self._fail(event.get("error_type") or "connection_closed")

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        self._main.cancel()
        await asyncio.gather(self._main, return_exceptions=True)
        await self._client.aclose()
        for generation in self._generations.values():
            generation.close()
        if self._pending_reply is not None and not self._pending_reply.done():
            self._pending_reply.cancel()
        self.realtime_model.sessions.discard(self)
