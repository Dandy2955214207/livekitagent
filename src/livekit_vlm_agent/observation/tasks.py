"""Task and event validity. No object recognition rules and no model calls."""

from collections import deque
import uuid

from .contracts import ObservationTask, ProactiveEvent


class ObservationTasks:
    def __init__(self, call_id, *, call_epoch=1, max_age=5):
        self.call_id, self.call_epoch, self.max_age = call_id, call_epoch, max_age
        self.task = None
        self.events = deque(maxlen=4)
        self.monitor_epoch = 0
        self.camera_epoch = 0
        self.armed = True
        self.last_match = float('-inf')
        self.last_sample = float('-inf')
        self._requests = deque(maxlen=32)

    def create(self, *, request_id, condition, now, paused=False, repeat='once', ttl=600):
        if not request_id or not condition.strip() or not 1 <= ttl <= 3600:
            raise ValueError('Invalid task request')
        if request_id in self._requests:
            return self.task
        if self.task and self.task.status in {'active', 'paused'}:
            raise ValueError('An observation task already exists')
        self.events.clear()
        self._requests.append(request_id)
        self.task = ObservationTask(task_id=uuid.uuid4().hex, revision=1, call_id=self.call_id,
            source_user_item_id='ui:' + request_id, goal=condition, condition=condition,
            status='paused' if paused else 'active', repeat=repeat, created_at=now, expires_at=now+ttl)
        self._reset()
        return self.task

    def _reset(self):
        self.monitor_epoch += 1
        self.armed = True
        self.last_match = self.last_sample = float('-inf')

    def change(self, action, *, now, condition=None):
        if not self.task:
            raise ValueError('No task')
        if action == 'cancel':
            status = 'cancelled'
        elif self.task.status not in {'active', 'paused'}:
            raise ValueError('Task is terminal')
        elif now >= self.task.expires_at:
            status = 'failed'
        elif action == 'pause':
            status = 'paused'
        elif action in {'resume', 'update'}:
            status = 'active'
        else:
            raise ValueError('Invalid task action')
        update = {'status': status, 'revision': self.task.revision + 1}
        if action == 'update':
            if not condition or not condition.strip() or len(condition) > 1000:
                raise ValueError('Invalid condition')
            update.update(condition=condition, goal=condition)
        self.task = self.task.model_copy(update=update)
        self.events.clear()
        self._reset()
        return self.task

    def camera_changed(self, epoch):
        self.camera_epoch = epoch
        self.events.clear()
        self._reset()

    def accept(self, result, *, now):
        task = self.task
        if (not task or task.status != 'active' or now >= task.expires_at
                or result.task_id != task.task_id or result.revision != task.revision
                or result.call_epoch != self.call_epoch or result.monitor_epoch != self.monitor_epoch
                or result.camera_epoch != self.camera_epoch
                or not 0 <= now - result.sampled_at_mono <= self.max_age
                or not result.sampled_at_mono <= result.evaluated_at_mono <= now
                or result.sampled_at_mono <= self.last_sample):
            return None
        self.last_sample = result.sampled_at_mono
        if result.answer.result == 'not_matched':
            self.armed = True
            return None
        if result.answer.result != 'matched' or not self.armed:
            return None
        if (now - self.last_match) * 1000 < task.cooldown_ms:
            return None
        self.armed = False
        self.last_match = now
        event = ProactiveEvent(event_id=uuid.uuid4().hex, observation_id=result.observation_id,
            task_id=task.task_id, revision=task.revision, call_epoch=self.call_epoch,
            created_at_mono=now, expires_at_mono=result.sampled_at_mono+self.max_age,
            dedupe_key=f'{task.task_id}:{task.revision}:{result.observation_id}',
            observation_summary=result.answer.observation_summary,
            evidence_summary=result.answer.evidence_summary)
        # Coalesce older queued reports of this task instead of accumulating reminders.
        self.events.clear()
        self.events.append(event)
        if task.repeat == 'once':
            self.task = task.model_copy(update={'status': 'completed'})
        return event

    def deliverable(self, event, *, now):
        task = self.task
        return bool(task and event in self.events and task.status in {'active', 'completed'}
                    and task.revision == event.revision and task.task_id == event.task_id
                    and event.call_epoch == self.call_epoch and now < event.expires_at_mono
                    and event.delivery == 'queued')

    def close(self):
        if self.task:
            self.task = self.task.model_copy(update={'status': 'cancelled'})
        self.events.clear()
        self._requests.clear()
        self._reset()
