"""Opt-in visual observation. Results are text until event speech is validated."""

import asyncio
import time

from .monitor import MonitorRunner, QwenMonitor


def visual_supported(settings):
    # Evidence applies only to the provider/endpoint/model tested on 2026-11-02.
    return bool(settings.background_observation_enabled and settings.model_provider == 'yibuapi'
                and settings.qwen_model == 'qwen3.8-omni-flash-realtime')


class BackgroundObserver:
    def __init__(self, session, state, *, evaluator=None, now=time.monotonic, autostart=True):
        self.session, self.state, self.now = session, state, now
        self.runner = MonitorRunner(state.tasks, evaluator or QwenMonitor(session.settings),
                                    on_result=self._result, now=now,
                                    refresh_frames=lambda: session.background_frames.read(
                                        camera_epoch=session.video.epoch, now=now()),
                                    max_per_call=getattr(getattr(session, 'settings', None), 'background_max_generations_per_call', 10))
        self.cursor = session.background_frames.sequence
        state.observation_budget = self.runner.max_per_call
        self.binding = None
        self.phase = 'idle'
        self.last_result = None
        self.on_changed = None
        self._closed = False
        self._loop = asyncio.create_task(self._run()) if autostart else None

    def _result(self, result):
        self.runner.counts['last_result_age_ms'] = round((self.now()-result.sampled_at_mono)*1000)
        event = self.state.tasks.accept(result, now=self.now())
        if (not self.state.tasks.task or result.revision != self.state.tasks.task.revision
                or result.camera_epoch != self.state.tasks.camera_epoch
                or result.monitor_epoch != self.state.tasks.monitor_epoch):
            return
        if self.now()-result.sampled_at_mono > self.state.tasks.max_age:
            self.runner.counts['stale_results'] += 1
            self.phase = 'stale_result'
            return
        self.last_result = result.answer.result
        if event:
            self.phase = 'matched_text'
            self.state.notice = '已观察到目标，结果显示在页面；未进行语音播报。'
            self.state.record_turn(source_id=result.observation_id,
                text='历史观察：' + result.answer.observation_summary, role='observation')
            if self.on_changed:
                self.on_changed()
        else:
            self.phase = 'unknown' if result.answer.result == 'unknown' else 'observing'

    async def tick(self):
        now = self.now()
        task = self.state.tasks.task
        binding = (task.task_id, task.revision, task.status, self.state.tasks.monitor_epoch,
                   self.session.video.enabled) if task else None
        if binding != self.binding:
            await self.runner.reset()
            self.binding = binding
            # New/revised task, resume, or camera epoch starts from newly received evidence.
            self.cursor = self.session.background_frames.sequence
        for event in tuple(self.state.tasks.events):
            if now >= event.expires_at_mono and event.delivery == 'queued':
                self.state.tasks.events.remove(event)
                self.state.tasks.events.append(event.model_copy(update={'delivery': 'expired'}))
        if not task or task.status != 'active':
            self.phase = 'matched_text' if task and task.status == 'completed' else 'idle'
            if task and task.status == 'paused' and self.runner.counts['call_budget_pauses']:
                self.phase = 'budget_paused'
                self.state.notice = '本通话后台观察预算已用尽，已暂停；普通问答可继续。'
            return
        if now >= task.expires_at:
            self.state.tasks.change('pause', now=now)  # task manager marks expired tasks failed
            await self.runner.reset()
            self.phase = 'expired'
            return
        if not self.session.video.enabled:
            self.phase = 'waiting_camera'
            return
        frames = self.session.background_frames.read(after=self.cursor,
            camera_epoch=self.session.video.epoch, now=now)
        if len(frames) >= 2:
            self.cursor = frames[-1].frame_id
            self.runner.submit(frames[-2:])
            self.phase = 'observing'
        elif self.phase not in {'observing', 'unknown', 'stale_result'}:
            self.phase = 'waiting_frames'

    async def _run(self):
        try:
            while True:
                await self.tick()
                await asyncio.sleep(.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.phase = 'unavailable'
            self.state.notice = '后台观察暂不可用，普通问答可继续。'
            if self.state.tasks.task and self.state.tasks.task.status == 'active':
                self.state.tasks.change('pause', now=self.now())
        finally:
            await self.runner.aclose()

    def snapshot(self):
        usage = getattr(self.runner.evaluator, 'usage', ())
        return {'phase': self.phase, 'last_result': self.last_result,
                'counts': dict(self.runner.counts), 'max_generations_per_call': self.runner.max_per_call,
                'reported_tokens': {key: sum(u.get(key, 0) for u in usage)
                                    for key in ('input_tokens', 'output_tokens', 'total_tokens')},
                'voice_enabled': False}

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        if self._loop:
            self._loop.cancel()
            await asyncio.gather(self._loop, return_exceptions=True)
        else:
            await self.runner.aclose()
