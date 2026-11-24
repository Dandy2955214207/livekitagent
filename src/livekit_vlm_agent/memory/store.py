"""Bounded SQLite text records; opt-in library, atomic corrections, no cloud calls."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    library_id: str
    kind: str
    content: str
    source: str
    source_ids: tuple[str, ...]
    call_id: str
    task_id: str | None
    observed_at: float
    updated_at: float
    validity: str = 'current'
    supersedes: str | None = None
    expires_at: float | None = None


class MemoryStore:
    def __init__(self, *, root=None, library_id=None, max_records=48):
        if max_records < 16:
            raise ValueError('Record budget must accommodate important facts')
        self.library_id = library_id or 'isolated'
        self.persistent = library_id is not None
        self.enabled = True
        self.max_records = max_records
        self._lock = threading.RLock()
        path = ':memory:'
        if library_id is not None:
            if root is None or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', library_id):
                raise ValueError('Invalid library ID')
            base = Path(root).resolve()
            folder = base / library_id
            folder.mkdir(parents=True, exist_ok=True)
            resolved = folder.resolve()
            if resolved.parent != base or (resolved / 'records.sqlite3').resolve().parent != resolved:
                raise ValueError('Invalid library path')
            path = str(resolved / 'records.sqlite3')
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=1)
        self.db.execute('CREATE TABLE IF NOT EXISTS memory (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        self.db.commit()

    def records(self):
        with self._lock:
            if not self.enabled:
                return ()
            return tuple(self._decode(row[0]) for row in self.db.execute('SELECT data FROM memory'))

    @staticmethod
    def _decode(raw):
        data = json.loads(raw)
        data['source_ids'] = tuple(data['source_ids'])
        return MemoryRecord(**data)

    def put(self, content, *, kind, source, source_ids, call_id, task_id=None,
            supersedes=None, expires_at=None):
        if (kind not in {'fact', 'observation', 'played', 'dialogue', 'task'}
                or source not in {'user_reported', 'model_observation', 'application_event'}
                or not isinstance(content, str) or not 1 <= len(content.strip()) <= 1000
                or len(source_ids) > 16 or any(not isinstance(v, str) or len(v) > 128 for v in source_ids)
                or (kind == 'fact' and source != 'user_reported')):
            raise ValueError('Invalid memory record')
        with self._lock, self.db:
            if not self.enabled:
                raise ValueError('Memory disabled')
            records = self.records()
            old = next((r for r in records if r.memory_id == supersedes), None)
            if supersedes and (old is None or old.validity != 'current' or old.kind != 'fact'
                               or kind != 'fact'):
                raise ValueError('Correction requires a current fact')
            if kind == 'fact' and not old and sum(r.kind == 'fact' and r.validity == 'current'
                                                for r in records) >= 16:
                raise ValueError('Important fact limit reached')
            now = time.time()
            record = MemoryRecord(uuid.uuid4().hex, self.library_id, kind, content.strip(), source,
                tuple(source_ids), call_id, task_id, now, now,
                'historical' if kind == 'observation' else 'current', supersedes, expires_at)
            if old:
                data = asdict(old)
                data.update(validity='superseded', updated_at=now)
                self.db.execute('UPDATE memory SET data=? WHERE id=?', (json.dumps(data), old.memory_id))
            self.db.execute('INSERT INTO memory VALUES (?, ?)', (record.memory_id, json.dumps(asdict(record))))
            # Important current facts survive bounded history pruning.
            rows = sorted((*self.records(),), key=lambda r: r.updated_at)
            expendable = [r for r in rows if not (r.kind == 'fact' and r.validity == 'current')]
            excess = max(0, len(rows)-self.max_records, len(expendable)-32)
            for item in expendable[:excess]:
                self.db.execute('DELETE FROM memory WHERE id=?', (item.memory_id,))
            return record

    def delete(self, memory_id):
        with self._lock, self.db:
            self.db.execute('DELETE FROM memory WHERE id=?', (memory_id,))

    def clear(self):
        with self._lock, self.db:
            self.db.execute('DELETE FROM memory')

    def set_enabled(self, enabled):
        with self._lock:
            self.enabled = bool(enabled)

    def close(self):
        with self._lock:
            self.db.close()
