import test from 'node:test';
import assert from 'node:assert/strict';
import { validateEvidence, createEvidenceMonitor, EVIDENCE_PROMPT } from '../src/evidence-monitor.mjs';
import { IntentState } from '../src/intent-state.mjs';
import { loadOfficial } from '../src/official.mjs';
import { DeliveryWindow } from '../src/delivery-window.mjs';
import { fakeProvider, until } from './fake-provider.mjs';
import { frameKey } from '../src/test-trace.mjs';

const evidence = [{ frame: 1, detail: '桌面左侧有白色带柄杯' }, { frame: 2, detail: '桌面左侧白色带柄杯仍在' }];
const answer = (clip, state = 'matched', extra = {}) => ({ clip, state, basis: 'state', evidence, ...extra });
const jpeg = Buffer.from([255, 216, 255, 217]).toString('base64'); // Protocol fixture, not a semantic image test.

test('evidence requires current clip, two frames, freshness and known state', () => {
  const clip = { id: 3, endAt: 10000, frames: 2 };
  const check = (value, now = 11000) => validateEvidence(JSON.stringify(value), clip, undefined, now);
  assert.equal(check(answer(3)).state, 'matched');
  assert.equal(check(answer(3, 'not_matched')).state, 'not_matched');
  for (const value of [answer(2), answer(3, 'unknown'), answer(3, 'yes'), answer(3, 'matched', { evidence: [evidence[0]] }),
    answer(3, 'matched', { evidence: [evidence[1], evidence[0]] }), answer(3, 'matched', { evidence: [{ frame: 1, detail: '' }, evidence[1]] })]) {
    assert.equal(check(value).state, 'unknown');
  }
  assert.equal(check(answer(3), 15001).reason, 'stale_evidence');
  assert.equal(validateEvidence('Reply: 水杯出现了', clip, undefined, 11000).state, 'unknown');
});

test('transition requires an observed before, never a static endpoint or expired baseline', () => {
  const clip = { id: 3, endAt: 10000, frames: 2 };
  const before = { clip: 2, state: 'not_matched', at: 8000 };
  const value = answer(3, 'matched', { basis: 'transition', before: { clip: 2, detail: '杯子原本平放' } });
  const check = (value, prior = before) => validateEvidence(JSON.stringify(value), clip, prior, 11000, 'transition');
  assert.equal(check(value).state, 'matched');
  assert.equal(check({ ...value, before: { clip: 3, detail: '第一帧杯子平放' } }).state, 'matched');
  assert.equal(check(answer(3)).reason, 'missing_before');
  assert.equal(check(answer(3, 'matched', { basis: 'transition', evidence: [evidence[0], { frame: 2, detail: '杯子已倒下' }] })).state, 'matched');
  assert.equal(check(answer(3, 'matched', { basis: 'transition', evidence: [evidence[0], { ...evidence[0], frame: 2 }] })).state, 'unknown');
  assert.equal(check(value, { ...before, state: 'matched' }).reason, 'missing_before');
  assert.equal(check(value, { ...before, at: -10000 }).reason, 'missing_before');
});

test('official scheduler + real official monitor transport: recurring present/unknown/present does not rearm, absent does', async () => {
  const official = await loadOfficial();
  let clock = Date.now() + 1000, nextState = 'not_matched';
  const observations = [], deliveries = [], states = [], trace = [];
  const tracker = new IntentState(state => states.push(state), () => clock);
  const provider = await fakeProvider({ monitorReply(event, peer) {
    assert.equal(peer.session.instructions, EVIDENCE_PROMPT);
    const instruction = JSON.parse(event.response.instructions);
    return 'Reply: ' + JSON.stringify(answer(instruction.clip, nextState));
  } });
  const config = structuredClone(official.DEFAULT_PROACTIVE_CONFIG);
  config.scheduler.evalIntervalSec = .01; config.scheduler.repeat.cooldownSec = 1;
  config.scheduler.repeat.clearBufferOnResume = false; config.vision.windowSizeSec = 3;
  config.monitor.sessionRecycleEvals = 6;
  const scheduler = new official.ProactiveScheduler({ config, realtime: { endpoint: provider.url, apiKey: 'fixture', model: 'qwen3.8-omni-flash-realtime' },
    now: () => clock, onEvent: d => (deliveries.push(d), true),
    onTaskChanged: (task, notification) => tracker.changed(task, notification),
    debug: (event, details) => tracker.debug(event, details),
    createMonitor: (options, callbacks) => createEvidenceMonitor(official.DashScopeRealtimeMonitor, options, callbacks,
      { now: () => clock, onObservation: o => { observations.push(o); tracker.observed(o); },
        trace: (kind, data) => trace.push({ kind, data }) }),
  });
  try {
    const task = scheduler.createPerceptionMonitor({ title: '喝水提醒', condition: '[state] 白色带柄水杯出现在桌面',
      triggerResponse: '记得喝水', modalities: ['vision'], repeat: true });
    await until(() => scheduler.listTasks()[0].status === 'running');
    async function clip(state) {
      const count = observations.length; nextState = state;
      clock += 1100; scheduler.feedImage(jpeg);
      clock += 1000; scheduler.feedImage(jpeg);
      await until(() => observations.length > count);
    }
    await clip('not_matched'); assert.equal(deliveries.length, 0);
    await clip('matched'); assert.equal(deliveries.length, 1);
    scheduler.announcementStarted(deliveries[0]); scheduler.playbackStarted(deliveries[0]); scheduler.acknowledgeDelivery(deliveries[0]);
    assert.equal(scheduler.listTasks()[0].status, 'running');
    await clip('matched'); await clip('unknown'); await clip('matched');
    const rejected = trace.find(row => row.kind === 'vision.result' && row.data.observation.state === 'unknown').data;
    const sent = trace.find(row => row.kind === 'vision.request' && row.data.clipId === rejected.clipId).data;
    assert.deepEqual(rejected.frameKeys, [frameKey(jpeg), frameKey(jpeg)]);
    assert.deepEqual(sent.frameKeys, rejected.frameKeys);
    assert.match(rejected.raw, /unknown/);
    assert.equal(deliveries.length, 1);
    await clip('not_matched'); await clip('matched'); // Also passes through official session recycle.
    assert.equal(deliveries.length, 2);
    assert.ok(states.some(s => s.phase === 'rearmed_false'));
    assert.equal(states.at(-1).deliveredCount, 1);
    scheduler.announcementStarted(deliveries[1]); scheduler.playbackStarted(deliveries[1]); scheduler.acknowledgeDelivery(deliveries[1]);
    assert.equal(states.at(-1).deliveredCount, 2);
    assert.equal(scheduler.listTasks()[0].triggerCount, 2);
    scheduler.updateTask({ targetTitle: task.title, repeat: false });
    await until(() => scheduler.listTasks()[0].status === 'running');
    await clip('matched');
    assert.equal(deliveries.length, 3);
    assert.equal(scheduler.listTasks()[0].status, 'delivering');
    scheduler.announcementStarted(deliveries[2]); scheduler.playbackStarted(deliveries[2]); scheduler.acknowledgeDelivery(deliveries[2]);
    assert.equal(states.at(-1).status, 'completed');
    assert.equal(scheduler.listTasks().length, 0);
  } finally { scheduler.dispose(); await provider.close(); }
});

test('unknown and late results never become false/rearm; closed monitor ignores its outstanding result', () => {
  let callbacks, clock = 10000, requests = 0;
  class FixtureMonitor {
    constructor(_options, cb) { callbacks = cb; }
    start() {} close() {} resetPendingCapture() {} feedAudio() {} feedImage() {}
    requestEvaluation() { requests++; return true; }
  }
  const results = [], observed = [];
  let allowed = true;
  const monitor = createEvidenceMonitor(FixtureMonitor, { taskId: 'a', taskGeneration: 1, instruction: '[state] cup',
    monitorMode: 'event', modalities: ['vision'] }, { onResult: r => results.push(r) },
    { now: () => clock, allowEvaluation: () => allowed, onObservation: o => observed.push(o) });
  callbacks.onDebug('proactive.monitor_chunk_prepared', { chunkEndAt: 10000, imageFrames: 2 });
  callbacks.onResult({ summary: JSON.stringify(answer(1, 'unknown')) }, 1);
  callbacks.onResult({ summary: '' }, 1); // Legacy wait is not absence.
  clock += 6000;
  callbacks.onResult({ summary: JSON.stringify(answer(1)) }, 1);
  assert.equal(results.length, 0); assert.equal(observed.length, 3);
  allowed = false; assert.equal(monitor.requestEvaluation(), false); assert.equal(requests, 0);
  monitor.close();
  callbacks.onResult({ summary: JSON.stringify(answer(1)) }, 1);
  assert.equal(observed.length, 3);
});

test('queued visual delivery expires; actual playback start or camera reset cannot leave a stale pending notification', async () => {
  const expired = [], window = new DeliveryWindow((d, reason) => expired.push([d.deliveryId, reason]), 25);
  window.add({ deliveryId: 'queued' });
  window.add({ deliveryId: 'playing' }); window.remove({ deliveryId: 'playing' });
  await until(() => expired.length === 1);
  assert.deepEqual(expired, [['queued', 'evidence_delivery_expired']]);
  window.add({ deliveryId: 'camera' }); window.invalidateVisual();
  assert.deepEqual(expired[1], ['camera', 'visual_source_changed']);
  window.add({ deliveryId: 'closed' }); window.close();
  assert.equal(window.pending.size, 0);
});

test('teaching confirmation needs a second fresh clip; uncertain evidence clears the candidate', () => {
  let cb, clock = 10000; const accepted = [];
  class FixtureMonitor { constructor(_o, c) { cb = c; } close() {} }
  const monitor = createEvidenceMonitor(FixtureMonitor, { taskId: 'step', taskGeneration: 1, instruction: '[state] 圆画好',
    monitorMode: 'event', modalities: ['vision'] }, { onResult: result => accepted.push(result) }, { now: () => clock, confirmations: 2 });
  let id = 0;
  function clip(state) {
    clock += 2100; cb.onDebug('proactive.monitor_chunk_prepared', { chunkEndAt: clock, imageFrames: 2 });
    cb.onResult({ summary: JSON.stringify(answer(++id, state)) }, 1);
  }
  clip('matched'); assert.equal(accepted.length, 0);
  clip('unknown'); clip('matched'); assert.equal(accepted.length, 0);
  clip('matched'); assert.equal(accepted.length, 1); assert.equal(accepted[0].triggered, true); monitor.close();
});

test('a candidate enables an early fresh recheck; uncertainty returns to idle cadence', () => {
  let cb, clock = 10000;
  let fresh = true;
  class Inner { constructor(o,c){ cb=c; } requestEvaluation(){ const result = fresh; fresh = false; return result; } }
  const m = createEvidenceMonitor(Inner, { taskId: 'step', instruction: '[state] done', monitorMode: 'event', modalities: ['vision'] },
    {onResult(){}}, { now:()=>clock, intervalMs:4000, confirmations:2 });
  assert.ok(m.requestEvaluation());
  cb.onDebug('proactive.monitor_chunk_prepared',{chunkEndAt:clock,imageFrames:2});
  cb.onResult({summary:JSON.stringify(answer(1))},1);
  clock+=500; assert.equal(m.requestEvaluation(),false); // Official fresh-chunk gate still applies.
  fresh = true; assert.ok(m.requestEvaluation()); // No extra fixed wait once it is ready.
  cb.onDebug('proactive.monitor_chunk_prepared',{chunkEndAt:clock,imageFrames:2});
  cb.onResult({summary:JSON.stringify(answer(2,'unknown'))},1);
  fresh = true; clock+=2000; assert.equal(m.requestEvaluation(),false); clock+=2000; assert.ok(m.requestEvaluation());
});

test('old in-flight guide result cannot speak after movement and settling', () => {
  let cb, view=1, blocked=false, deliveries=0;
  class Inner {constructor(o,c){cb=c;} feedImage(){return true;} resetPendingCapture(){}}
  const m=createEvidenceMonitor(Inner,{taskId:'guide',instruction:'guide',modalities:['vision']},{onResult(){deliveries++;}},
    {protocol:{view:()=>view,blocked:()=>blocked,validate:()=>({state:'matched'}),accept:()=>true}});
  cb.onDebug('proactive.monitor_chunk_prepared',{chunkEndAt:Date.now(),imageFrames:2});
  view++; blocked=true; assert.equal(m.feedImage('moving',1),false);
  view++; blocked=false; assert.equal(m.feedImage('stable',2),true);
  cb.onResult({summary:'late'},1); assert.equal(deliveries,0);
});

test('a settled step change checks early once, preserves confirmation and respects budget', () => {
  let cb, clock = 10000, allowed = true;
  const results = [];
  class Inner {
    constructor(o, c) { cb = c; }
    requestEvaluation() { return true; }
  }
  const m = createEvidenceMonitor(Inner, {taskId:'step', instruction:'[state] circle', monitorMode:'event', modalities:['vision']},
    {onResult: value => results.push(value)}, {now:()=>clock, intervalMs:4000, confirmations:2, watchChanges:true, allowEvaluation:()=>allowed});
  const before = Buffer.alloc(288, 20), after = Buffer.from(before);
  after.fill(180, 0, 12);
  m.feedGrid(before.toString('base64'));
  assert.ok(m.requestEvaluation());
  const finish = (clip, state) => {
    cb.onDebug('proactive.monitor_chunk_prepared', {chunkEndAt:clock, imageFrames:2});
    cb.onResult({summary:JSON.stringify(answer(clip,state))},1);
  };
  finish(1, 'unknown');
  clock += 1000; m.feedGrid(after.toString('base64'));
  assert.equal(m.requestEvaluation(), false);
  clock += 1000; m.feedGrid(after.toString('base64'));
  allowed = false; assert.equal(m.requestEvaluation(), false);
  allowed = true; assert.ok(m.requestEvaluation());
  assert.equal(m.requestEvaluation(), false);
  finish(2, 'unknown');
  clock += 2000; m.feedGrid(after.toString('base64'));
  assert.equal(m.requestEvaluation(), false, 'unchanged scene must return to the regular interval');
  clock += 2000; assert.ok(m.requestEvaluation());
  finish(3, 'matched'); assert.equal(results.length, 0);
  clock += 2000; assert.ok(m.requestEvaluation());
  finish(4, 'matched'); assert.equal(results.length, 1);
});
