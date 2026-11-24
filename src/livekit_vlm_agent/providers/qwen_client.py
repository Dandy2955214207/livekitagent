"""Bounded M0 WebSocket client; no LiveKit dependency or implicit retries."""

import asyncio
import base64
from dataclasses import dataclass, field
import json
import time
from urllib.parse import urlencode
import uuid

from websockets.asyncio.client import connect

from ..config import Settings


class QwenProtocolError(RuntimeError):
    pass


@dataclass
class OutputGate:
    """Reject audio from interrupted responses and previous connections."""

    connection_id: str
    active_response: str | None = None
    cancelled: set[str] = field(default_factory=set)

    def begin(self, response_id: str) -> None:
        if response_id not in self.cancelled:
            self.active_response = response_id

    def cancel(self, response_id: str) -> None:
        self.cancelled.add(response_id)
        if self.active_response == response_id:
            self.active_response = None

    def accepts(self, connection_id: str, response_id: str) -> bool:
        return (
            connection_id == self.connection_id
            and response_id == self.active_response
            and response_id not in self.cancelled
        )


@dataclass
class Response:
    id: str
    created_at: float
    status: str = "in_progress"
    text: str = ""
    audio_bytes: int = 0
    dropped_audio_bytes: int = 0
    first_audio_at: float | None = None
    done_at: float | None = None
    cancelled_at: float | None = None
    audio: bytearray = field(default_factory=bytearray, repr=False)
    usage: dict = field(default_factory=dict)


@dataclass
class Event:
    seq: int
    at: float
    data: dict


class QwenClient:
    """Single ordered writer, independent receiver and explicitly gated output.

    Event history is limited to 20,000 events. The M0 runner bounds duration and
    response length; a future production bridge will consume events continuously.
    """

    def __init__(self, settings: Settings, *, capture_audio: bool = False,
                 streaming: bool = False, on_event=None):
        self.settings = settings
        self.capture_audio = capture_audio
        self.streaming = streaming
        self.on_event = on_event
        self._sequence = 0
        self.connection_id = uuid.uuid4().hex
        self.gate = OutputGate(self.connection_id)
        self.responses: dict[str, Response] = {}
        self.events: list[Event] = []
        self.trace: list[dict] = []
        self.started = time.monotonic()
        self.ws = None
        self._reader = None
        self._write_lock = asyncio.Lock()
        self._changed = asyncio.Condition()
        self._reader_error: str | None = None

    @property
    def cursor(self) -> int:
        return self._sequence

    def _summary(self, direction: str, data: dict) -> dict:
        summary = {
            "direction": direction,
            "type": data.get("type"),
            "at_ms": round((time.monotonic() - self.started) * 1000, 2),
        }
        for key in ("response_id", "item_id", "call_id", "byte_count", "audio_start_ms", "audio_end_ms"):
            if key in data:
                summary[key] = data[key]
        if "response" in data:
            summary["response_id"] = data["response"].get("id")
            summary["status"] = data["response"].get("status")
        item = data.get("item", {})
        if item.get("type") in {"function_call", "function_call_output"}:
            summary["call_id"] = item.get("call_id")
            summary["item_id"] = item.get("id")
        if "session" in data:
            session = data["session"]
            summary["session_fields"] = sorted(session)
            summary["modalities"] = session.get("modalities")
            summary["max_tokens"] = session.get("max_tokens")
            if "tools" in session:
                summary["tool_names"] = [tool.get("name") or tool.get("function", {}).get("name")
                                         for tool in (session["tools"] or [])]
        if data.get("type") == "error":
            summary["error_code"] = data.get("error", {}).get("code")
        return summary

    async def __aenter__(self):
        missing = self.settings.qwen_missing()
        if missing:
            raise QwenProtocolError("Missing settings: " + ", ".join(missing))
        endpoint = self.settings.qwen_endpoint() + "?" + urlencode({"model": self.settings.qwen_model})
        self.ws = await connect(
            endpoint,
            additional_headers=self.settings.realtime_headers(),
            open_timeout=12, close_timeout=3, max_size=2 * 1024 * 1024,
            max_queue=32, proxy=None,
        )
        self._reader = asyncio.create_task(self._receive())
        try:
            await self.wait_type("session.created", timeout=12)
        except BaseException:
            await self.aclose()
            raise
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        if self.ws is not None:
            await self.ws.close()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)

    async def _receive(self):
        try:
            async for raw in self.ws:
                data = json.loads(raw)
                now = time.monotonic()
                kind = data.get("type")
                delivered_pcm = None
                rid = data.get("response_id")
                if kind == "response.created":
                    rid = data["response"]["id"]
                    self.responses[rid] = Response(rid, now)
                    self.gate.begin(rid)
                state = self.responses.get(rid)
                if kind == "response.audio.delta":
                    pcm = base64.b64decode(data.pop("delta"), validate=True)
                    data["byte_count"] = len(pcm)
                    if state is not None:
                        if self.gate.accepts(self.connection_id, rid):
                            state.first_audio_at = state.first_audio_at or now
                            state.audio_bytes += len(pcm)
                            delivered_pcm = pcm
                            if self.capture_audio:
                                if len(state.audio) + len(pcm) > 4 * 1024 * 1024:
                                    raise QwenProtocolError("Response audio exceeded M0 memory limit")
                                state.audio.extend(pcm)
                        else:
                            state.dropped_audio_bytes += len(pcm)
                elif state is not None and kind in {"response.audio_transcript.delta", "response.text.delta"}:
                    state.text += data.get("delta", "")
                elif kind == "response.done":
                    rid = data["response"]["id"]
                    state = self.responses.get(rid)
                    if state:
                        state.status = data["response"].get("status", "unknown")
                        state.done_at = now
                        state.usage = data["response"].get("usage", {})
                if self.on_event is not None:
                    self.on_event(data, delivered_pcm)
                if not self.streaming and len(self.events) >= 20000:
                    raise QwenProtocolError("M0 event history limit reached")
                if not self.streaming:
                    self.trace.append(self._summary("recv", data))
                async with self._changed:
                    self._sequence += 1
                    self.events.append(Event(self._sequence, now, data))
                    if self.streaming:
                        del self.events[:-256]
                        if len(self.responses) > 32:
                            for old_id, old in list(self.responses.items()):
                                if len(self.responses) <= 32:
                                    break
                                if old.done_at is not None and old_id != self.gate.active_response:
                                    self.responses.pop(old_id)
                                    self.gate.cancelled.discard(old_id)
                    self._changed.notify_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._reader_error = type(exc).__name__
        finally:
            if self.streaming and self.on_event is not None:
                self.on_event({"type": "client.closed", "error_type": self._reader_error}, None)
            async with self._changed:
                self._reader_error = self._reader_error or "Connection closed"
                self._changed.notify_all()

    async def send(self, data: dict) -> None:
        async with self._write_lock:
            await self.ws.send(json.dumps({"event_id": "evt_" + uuid.uuid4().hex, **data}))
            summary = self._summary("send", data)
            for field_name in ("audio", "image"):
                if field_name in data:
                    summary["base64_chars"] = len(data[field_name])
            if not self.streaming:
                self.trace.append(summary)

    async def wait(self, predicate, *, after: int = 0, timeout: float = 25) -> Event:
        index = after
        async with asyncio.timeout(timeout):
            async with self._changed:
                while True:
                    for event in self.events:
                        if event.seq <= index:
                            continue
                        if event.data.get("type") == "error":
                            error = event.data.get("error", {})
                            secret = self.settings.realtime_api_key()
                            message = str(error.get("message", "Rejected")).replace(secret, "[REDACTED]")
                            raise QwenProtocolError(str(error.get("code")) + ": " + message[:300])
                        if predicate(event):
                            return event
                    index = self.cursor
                    if self._reader_error:
                        raise QwenProtocolError(self._reader_error)
                    await self._changed.wait()

    async def wait_type(self, kind: str, **kwargs) -> Event:
        return await self.wait(lambda event: event.data.get("type") == kind, **kwargs)

    async def configure(self, *, vad: bool | str = False, instructions: str | None = None,
                        modalities: list[str] | None = None, tools: list[dict] | None = None,
                        max_tokens: int = 256, wait_for_ack: bool = True,
                        instructions_only: bool = False, create_response: bool | None = None,
                        interrupt_response: bool | None = None):
        """Opt-in H0 configuration; defaults preserve the legacy voice path."""
        if modalities is not None and modalities not in (["text"], ["text", "audio"]):
            raise ValueError("Unsupported output modalities")
        if not 1 <= max_tokens <= 4096:
            raise ValueError("Output budget must be within 1..4096")
        cursor = self.cursor
        session = {
            "modalities": modalities if modalities is not None else ["text", "audio"],
            "instructions": instructions or "请用中文简短回答用户问题，除非用户明确要求更长的回答。",
            "max_tokens": max_tokens,
            "turn_detection": {"type": self.settings.qwen_turn_detection if vad is True else vad} if vad else None,
            "audio": {
                "input": {"format": {"type": "pcm", "sample_rate": 16000,
                    "sample_format": "s16le", "channels": 1,
                    "packing": "interleaved", "channel_layout": "mono"}},
                "output": {"voice": self.settings.qwen_voice,
                    "format": {"type": "pcm", "sample_rate": 24000}},
            },
        }
        if tools is not None:
            session["tools"] = tools
        for key, value in (("create_response", create_response), ("interrupt_response", interrupt_response)):
            if value is not None:
                if not vad or not isinstance(value, bool):
                    raise ValueError('Response scheduling options require VAD and bool values')
                session['turn_detection'][key] = value
        if instructions_only:
            # Harness initializes the fixed tools/audio once on this connection.
            session = {key: session[key] for key in ("instructions", "max_tokens")}
        await self.send({"type": "session.update", "session": session})
        if not wait_for_ack:
            return cursor  # Ordered caller must validate session.updated before exposing output.
        return await self.wait_type("session.updated", after=cursor, timeout=12)

    async def stream_pcm(self, pcm: bytes, *, image: bytes | None = None, realtime: bool = True,
                         on_image_sent=None):
        if len(pcm) % 2:
            raise ValueError("s16le PCM requires complete sample boundaries")
        if image is not None and 4 * ((len(image) + 2) // 3) > 256 * 1024:
            raise ValueError("Image exceeds provider Base64 size limit")
        chunk_bytes = 640  # 20 ms, 16 kHz, 16-bit, mono
        started = time.monotonic()
        for offset in range(0, len(pcm), chunk_bytes):
            await self.send({"type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm[offset:offset + chunk_bytes]).decode("ascii")})
            if image is not None and offset == 0:
                image_started = time.monotonic()
                await self.send({"type": "input_image_buffer.append",
                    "image": base64.b64encode(image).decode("ascii")})
                if on_image_sent:
                    on_image_sent(image_started)
            if realtime:
                target = started + min(offset + chunk_bytes, len(pcm)) / 32000
                await asyncio.sleep(max(0, target - time.monotonic()))

    async def commit_and_create(self) -> float:
        cursor = self.cursor
        await self.send({"type": "input_audio_buffer.commit"})
        await self.wait_type("input_audio_buffer.committed", after=cursor)
        await self.send({"type": "response.create"})
        return time.monotonic()

    async def cancel(self, response_id: str) -> float:
        state = self.responses[response_id]
        if state.done_at is not None:
            raise QwenProtocolError("Response already finished before cancellation")
        # Close the local output gate before yielding to the network writer.
        self.gate.cancel(response_id)
        state.cancelled_at = time.monotonic()
        await self.send({"type": "response.cancel"})
        return state.cancelled_at

    async def wait_done(self, response_id: str, *, after: int = 0, timeout: float = 30):
        await self.wait(lambda event: event.data.get("type") == "response.done"
            and event.data.get("response", {}).get("id") == response_id,
            after=after, timeout=timeout)
        return self.responses[response_id]
