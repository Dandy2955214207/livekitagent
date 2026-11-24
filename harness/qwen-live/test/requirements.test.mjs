import test from 'node:test';
import assert from 'node:assert/strict';
import { createRuntime } from '../src/runtime.mjs';
import { SceneTasks } from '../src/scene-tasks.mjs';
import { Latency } from '../src/latency.mjs';
import { fakeProvider, until } from './fake-provider.mjs';

test('completed action survives speech failure; replay and old task ID cannot advance a replacement lesson', async () => {
  let seq = 0; const tasks = [];
  const create = input => { const t = { taskId: `task-${++seq}`, ...input }; tasks.push(t); return t; };
  const scheduler = { createTimer: create, createPerceptionMonitor: create, cancelTaskById() {} };
  const scenes = new SceneTasks(() => scheduler, () => {});
  const lesson = { mode: 'teach', goal: '做手工', focus: '纸', depth: 'normal', steps: [
    { instruction: '对折', completion: '折叠完成' }, { instruction: '剪开', completion: '剪开完成' }] };
  scenes.start(lesson);
  scenes.changed(tasks[0], 'delivered'); await new Promise(r => setImmediate(r));
  const oldId = scenes.snapshot().id, d = { taskId: tasks[1].taskId, deliveryId: 'visual-proof' };
  scenes.onDelivery(d); scenes.onDelivery(d);
  assert.equal(scenes.snapshot().completedSteps, 1);
  scenes.changed(tasks[1], 'undelivered');
  assert.equal(scenes.snapshot().status, 'paused');
  scenes.control({ id: oldId, revision: scenes.snapshot().revision, action: 'resume' });
  assert.equal(scenes.snapshot().step, 2); assert.match(tasks[2].reminderText, /剪开/);
  scenes.changed(tasks[1], 'delivered'); await new Promise(r => setImmediate(r));
  assert.equal(scenes.snapshot().status, 'instructing');
  scenes.control({ id: oldId, revision: scenes.snapshot().revision, action: 'stop' });
  scenes.start(lesson);
  assert.throws(() => scenes.control({ id: oldId, revision: scenes.snapshot().revision, action: 'stop' }), /stale/);
  assert.equal(scenes.snapshot().status, 'instructing'); scenes.close();
});

test('official runtime supports lesson + observer + timer; scoped edits, pause/resume, recovery and stale commands stay isolated', async () => {
  const provider = await fakeProvider(), events = [];
  const config = { callId: 'multi', apiKey: 'fixture', model: 'qwen3.8-omni-flash-realtime', voice: 'Tina',
    maxGenerations: 40, maxMonitorGenerations: 10, proactiveEnabled: true };
  const runtime = await createRuntime(config, e => (events.push(e), true), { gateway: { upstreamUrl: provider.url } });
  const acked = new Set();
  const ack = setInterval(() => {
    for (const e of events.filter(e => e.type === 'audio_end')) {
      if (acked.has(e.outputId)) continue; acked.add(e.outputId);
      for (const type of ['playback_started', 'playback_completed']) void runtime.handle({ ...e, type, browserBufferMs: 40 });
    }
  }, 10);
  const turn = async (name, args, text = '执行当前明确请求') => {
    const before = events.filter(e => e.type === 'audio_end').length;
    provider.turn(provider.peers[0], { text, ...(name ? { tool: { name, arguments: JSON.stringify(args) } } : {}) });
    await until(() => events.filter(e => e.type === 'audio_end').length > before);
    await until(() => runtime.host.outputs.size === 0);
  };
  const aux = () => runtime.requirements.snapshot().auxiliary;
  try {
    await runtime.start();
    await turn('start_scene_task', { mode: 'teach', goal: '教我做手工', depth: 'normal' });
    await until(() => runtime.scenes.snapshot().status === 'waiting_step');
    const primary = runtime.scenes.snapshot();
    await turn('create_proactive_monitor', { title: '水杯提醒', modalities: ['vision'], condition: '[state] 水杯出现', trigger_response: '喝水', repeat: true });
    await until(() => aux()[0]?.status === 'running');
    await turn('create_proactive_timer', { title: '稍后休息', duration_sec: 120, reminder_text: '休息一下' });
    assert.equal(aux().length, 2);
    await turn(undefined, undefined, '这一步为什么要这样做？');
    assert.deepEqual(runtime.scenes.snapshot(), primary);
    await turn('create_proactive_timer', { title: '超出容量', duration_sec: 100, reminder_text: '第三项' });
    assert.equal(aux().length, 2);
    const monitor = aux().find(r => r.kind === 'monitor');
    await turn('control_requirement', { id: monitor.id, revision: monitor.revision, action: 'pause' });
    assert.equal(aux().find(r => r.id === monitor.id).status, 'paused');
    const paused = aux().find(r => r.id === monitor.id);
    await turn('control_requirement', { id: paused.id, revision: paused.revision, action: 'update', condition: '[state] 红色水杯出现' });
    assert.equal(aux().find(r => r.id === monitor.id).status, 'paused');
    assert.equal(runtime.scenes.snapshot().status, 'waiting_step');
    await turn('control_requirement', { id: monitor.id, revision: monitor.revision, action: 'stop' });
    assert.equal(aux().find(r => r.id === monitor.id).status, 'paused'); // Stale revision rejected.
    const updated = aux().find(r => r.id === monitor.id);
    await turn('control_requirement', { id: updated.id, revision: updated.revision, action: 'resume' });
    await until(() => aux().find(r => r.id === monitor.id).status === 'running');
    assert.notEqual(aux().find(r => r.id === monitor.id).taskId, monitor.taskId);
    assert.equal(aux().find(r => r.id === monitor.id).condition, '[state] 红色水杯出现');
    const timer = aux().find(r => r.kind === 'timer');
    await turn('control_requirement', { id: timer.id, revision: timer.revision, action: 'stop' });
    assert.equal(aux().find(r => r.id === timer.id).status, 'cancelled');
    assert.equal(aux().find(r => r.id === monitor.id).status, 'running');
    const beforeRecovery = runtime.requirements.snapshot();
    provider.send(provider.peers[0], { type: 'error', error: { code: 'COMMON_ERROR', type: 'server_error', status: 500,
      message: '<50002> InternalError.Algo.ModelServingError: synthetic failure' } });
    await until(() => events.some(e => e.type === 'recovery' && e.phase === 'completed'));
    assert.deepEqual(runtime.requirements.snapshot(), beforeRecovery);
    assert.equal(runtime.gateway.stats.planGenerations, 1);
    assert.ok(events.some(e => e.type === 'latency' && e.metric === 'trigger_to_playback_ack' && e.kind === 'conversation'));
    runtime.requirements.scheduler.createTimer({ title: '预算后仍可休息', durationSec: 60, reminderText: '休息' });
    runtime.scenes.pauseForBudget(); runtime.requirements.pauseForBudget();
    const budgetMonitor = aux().find(r => r.id === monitor.id);
    assert.equal(budgetMonitor.reason, 'budget');
    assert.throws(() => runtime.requirements.control({ id: budgetMonitor.id, revision: budgetMonitor.revision, action: 'resume' }), /budget/);
    assert.equal(aux().find(r => r.title === '预算后仍可休息').status, 'running');
    assert.equal(runtime.scenes.snapshot().completedSteps, 0);
    assert.ok(!events.some(e => e.type === 'fatal'));
  } finally { clearInterval(ack); await runtime.close(); await provider.close(); }
});

test('latency pairs each output with its trigger and drops retired notifications', () => {
  let now = 1000; const events = [], latency = new Latency(e => events.push(e), () => now);
  latency.userStopped(); now += 100; const a = { id: 1 }; latency.output(a);
  now += 50; latency.started(a, 40);
  assert.equal(events.find(e => e.metric === 'trigger_to_playback_ack').ms, 150);
  latency.observed({ taskId: 'task', state: 'matched', at: now - 200, evaluationMs: 100 });
  const d = { taskId: 'task', deliveryId: 'proof' }; latency.queued(d); now += 300; latency.preparing(d);
  now += 200; const b = { id: 2 }; latency.output(b); now += 50; latency.started(b);
  assert.equal(events.find(e => e.metric === 'latest_evidence_to_playback_ack').ms, 750);
  latency.queued(d); latency.preparing(d); latency.retired(d);
  const c = { id: 3 }; latency.output(c); assert.equal(c.trace, undefined);
});
