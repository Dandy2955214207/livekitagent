"""Local task/memory controls. Never writes to the foreground model's context."""

import asyncio
from collections import deque
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..memory.context import snapshot
from ..memory.store import MemoryStore
from ..observation.tasks import ObservationTasks


class DirectCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_id: str = Field(min_length=1, max_length=64)
    action: Literal['status', 'observation_create', 'observation_update', 'observation_pause',
                    'observation_resume', 'observation_cancel', 'memory_put', 'memory_correct',
                    'memory_delete', 'memory_clear', 'memory_enabled', 'memory_select', 'memory_query']
    content: str = Field(default='', max_length=1000)
    record_id: str = Field(default='', max_length=64)
    library_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    enabled: bool = True


class DirectState:
    def __init__(self, call_id, *, memory_root, observation_available=False):
        self.call_id, self.memory_root = call_id, memory_root
        self.tasks = ObservationTasks(call_id)
        self.memory = MemoryStore()
        self.recent = deque(maxlen=32)
        self._lock = asyncio.Lock()
        self._seen = deque(maxlen=32)
        self._closed = False
        self._pending_controls = 0
        self.observation_available = observation_available
        self.observation_budget = 10
        self.notice = '普通问答直接响应。观察协议待验；本地记忆尚未回注模型。'

    def record_turn(self, *, source_id, text, role):
        if self._closed or not self.memory.enabled or not text:
            return
        # Existing transcription only; no fact extraction or summary generation.
        self.recent.append({'source_id': source_id, 'text': text[:1000], 'role': role,
                            'status': {'user': 'user_reported', 'observation': 'historical_observation'}.get(role, 'generated_unconfirmed')})

    async def status(self, request_id=None, *, include_records=True):
        records = await asyncio.to_thread(self.memory.records) if include_records else ()
        task = self.tasks.task
        return {'request_id': request_id, 'notice': self.notice,
                'task': {'task_id': task.task_id, 'revision': task.revision, 'status': task.status,
                         'condition': task.condition[:200]} if task else None,
                'observation': 'experimental_text' if self.observation_available else 'protocol_pending',
                'observation_budget': self.observation_budget,
                'proactive_audio': 'unavailable',
                'events': [{'event_id': e.event_id, 'summary': e.observation_summary[:200],
                            'evidence': e.evidence_summary[:200], 'delivery': e.delivery,
                            'voice_played': False} for e in self.tasks.events],
                'memory': {'enabled': self.memory.enabled, 'library_id': self.memory.library_id,
                           'persistent': self.memory.persistent,
                           'remote_sync': 'not_connected', 'recent_count': len(self.recent),
                           'records': [{'memory_id': r.memory_id, 'content': r.content[:160]}
                                       for r in records if include_records and r.kind == 'fact' and r.validity == 'current']}}

    async def control(self, payload):
        command = DirectCommand.model_validate(payload)
        # Reject excess work before creating unbounded lock waiters.
        if self._pending_controls >= 8:
            raise ValueError('Control queue full')
        self._pending_controls += 1
        try:
            async with self._lock:
                if self._closed:
                    raise ValueError('Call closed')
                if command.request_id in self._seen:
                    return await self.status(command.request_id)
                result = await self._apply(command)
                self._seen.append(command.request_id)
                return result
        finally:
            self._pending_controls -= 1

    async def _apply(self, c):
        now = time.monotonic()
        if c.action == 'observation_create':
            self.tasks.create(request_id=c.request_id, condition=c.content, now=now,
                              paused=not self.observation_available)
            self.notice = (f'正在独立观察，匹配后显示文字结果；每通话最多{self.observation_budget}次后台生成，不自动播报。'
                           if self.observation_available else '目标已登记并暂停：云端观察协议待验证，当前不会自动提醒。')
        elif c.action.startswith('observation_'):
            action = c.action.removeprefix('observation_')
            if action == 'resume' and not self.observation_available:
                raise ValueError('Observation protocol not verified')
            self.tasks.change(action, now=now, condition=c.content)
            if action == 'update' and not self.observation_available:
                self.tasks.task = self.tasks.task.model_copy(update={'status': 'paused'})
            self.notice = '任务状态已更新；匹配结果仅显示文字，不播报。' if self.observation_available else '任务状态已更新；观察协议待验证。'
        elif c.action in {'memory_put', 'memory_correct'}:
            if c.action == 'memory_correct' and not c.record_id:
                raise ValueError('Correction requires a record ID')
            await asyncio.to_thread(self.memory.put, c.content, kind='fact', source='user_reported',
                source_ids=('ui:' + c.request_id,), call_id=self.call_id,
                supersedes=c.record_id if c.action == 'memory_correct' else None)
            self.notice = '已保存本地事实，尚未发送至模型。'
        elif c.action == 'memory_delete':
            await asyncio.to_thread(self.memory.delete, c.record_id)
            self.notice = '已删除本地记录；模型记忆回注尚未接通。'
        elif c.action == 'memory_clear':
            await asyncio.to_thread(self.memory.clear)
            self.recent.clear()
            self.notice = '当前本地库已清空。'
        elif c.action == 'memory_enabled':
            await asyncio.to_thread(self.memory.set_enabled, c.enabled)
            if not c.enabled:
                self.recent.clear()
            self.notice = '本地记忆已启用。' if c.enabled else '本地记忆已禁用，未删除库。'
        elif c.action == 'memory_select':
            selected = await asyncio.to_thread(MemoryStore, root=self.memory_root, library_id=c.library_id)
            selected.set_enabled(self.memory.enabled)
            await asyncio.to_thread(self.memory.close)
            self.memory = selected
            self.recent.clear()
            self.notice = '已选择本地持久库；使用同一库 ID 可恢复手动保存的事实。' if c.library_id else '已切换至本通话隔离记忆。'
        elif c.action == 'memory_query':
            result = await asyncio.to_thread(snapshot, self.memory, c.content)
            response = await self.status(c.request_id, include_records=False)
            response['retrieval'] = result
            return response
        return await self.status(c.request_id)

    async def aclose(self):
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self.tasks.close()
            self.recent.clear()
            self._seen.clear()
            await asyncio.to_thread(self.memory.close)
