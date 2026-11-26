"""Own only this API's rooms; single active demo session with explicit dispatch."""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import hmac
import json
import secrets
import time
import uuid

from livekit import api

from .config import Settings
from .official.bridge import capabilities as official_capabilities


class SessionBusy(Exception):
    pass


@dataclass
class Call:
    id: str
    room: str
    identity: str
    access: str = field(repr=False)
    worker_access: str = field(repr=False)
    created_at: float = field(default_factory=time.monotonic)
    state: str = "connecting"
    error: str | None = None
    worker_seen: float = field(default_factory=time.monotonic)
    cleaned: bool = False

    def public(self):
        return {"session_id": self.id, "state": self.state, "error": self.error,
                "room_name": self.room, "identity": self.identity}


class SessionRegistry:
    def __init__(self, settings: Settings, client):
        self.settings, self.client = settings, client
        self.calls = {}
        self.lock = asyncio.Lock()

    async def recover(self):
        owner = self.settings.service_owner
        if owner:
            rooms = await self.client.room.list_rooms(api.ListRoomsRequest())
            for room in rooms.rooms:
                if room.metadata == owner and room.name.startswith("voice-"):
                    try:
                        await self.client.room.delete_room(api.DeleteRoomRequest(room=room.name))
                    except api.TwirpError as exc:
                        if exc.code != "not_found":
                            raise

    async def create(self, *, capture_id=None):
        async with self.lock:
            if any(not call.cleaned for call in self.calls.values()):
                raise SessionBusy("已有通话正在进行或清理，请先挂断。")
            call = Call(uuid.uuid4().hex, "voice-" + uuid.uuid4().hex,
                        "user-" + uuid.uuid4().hex, secrets.token_urlsafe(32), secrets.token_urlsafe(32))
            self.calls[call.id] = call
            try:
                await self.client.room.create_room(api.CreateRoomRequest(
                    name=call.room, metadata=self.settings.service_owner, empty_timeout=self.settings.join_timeout_seconds,
                    departure_timeout=10, max_participants=2))
                await self.client.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
                    room=call.room, agent_name=self.settings.agent_name,
                    metadata=json.dumps({"session_id": call.id, "identity": call.identity,
                                         "worker_access": call.worker_access,
                                         **({"capture_id": capture_id} if capture_id else {})})))
            except BaseException:
                call.state, call.error = "error", "无法创建房间或派发 Agent，请检查本地服务。"
                await asyncio.shield(self._delete_room(call))
                raise
            token = (api.AccessToken(self.settings.livekit_api_key.get_secret_value(),
                                    self.settings.livekit_api_secret.get_secret_value())
                .with_identity(call.identity).with_name("本机用户")
                .with_ttl(timedelta(seconds=60))
                .with_grants(api.VideoGrants(room_join=True, room=call.room, room_create=False,
                    room_admin=False, can_publish=True, can_subscribe=True,
                    can_publish_data=True, can_publish_sources=["microphone", "camera"]))
                .to_jwt())
            return {**call.public(), "access_token": call.access, "token": token,
                    "harness_mode": self.settings.harness_mode,
                    "official_harness_capabilities": official_capabilities(self.settings),
                    "livekit_url": self.settings.livekit_public_url or self.settings.livekit_url,
                    "expires_in": self.settings.session_ttl_seconds}

    def authorize(self, session_id, access, *, worker=False):
        call = self.calls.get(session_id)
        if call is None or not hmac.compare_digest(call.worker_access if worker else call.access, access):
            raise KeyError("Session not found")
        return call

    async def _delete_room(self, call):
        if call.cleaned:
            return
        try:
            await self.client.room.delete_room(api.DeleteRoomRequest(room=call.room))
        except api.TwirpError as exc:
            if exc.code != "not_found":
                raise
        call.cleaned = True

    async def end(self, call, *, error=None):
        async with self.lock:
            if error:
                call.state, call.error = "error", error
            elif call.state != "error":
                call.state = "closed"
            await self._delete_room(call)

    async def sweep(self):
        now = time.monotonic()
        for call in list(self.calls.values()):
            if call.cleaned:
                if now - call.created_at > self.settings.session_ttl_seconds + 300:
                    self.calls.pop(call.id, None)
                continue
            age = now - call.created_at
            error = None
            if age > self.settings.session_ttl_seconds:
                error = "通话已达到本机测试时限，请重新加入。"
            elif call.state == "connecting" and age > self.settings.join_timeout_seconds:
                error = "Agent 接通超时，请检查 Worker 是否已启动。"
            elif call.state == "ready" and now - call.worker_seen > 25:
                error = "Agent 已断开，请重新加入；上下文将重置。"
            if error or call.state in {"error", "closed"}:
                await self.end(call, error=error)
        # Retain a small number of terminal status entries for the browser.
        terminal = [call for call in self.calls.values() if call.cleaned]
        for call in terminal[:-20]:
            self.calls.pop(call.id, None)

    async def close(self):
        for call in list(self.calls.values()):
            await self.end(call)
