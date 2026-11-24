import test from 'node:test';
import assert from 'node:assert/strict';
import { GuideTarget } from '../src/guide-target.mjs';
import { validateGuide, modelBox, normalizedBox } from '../src/guide-monitor.mjs';
import { SceneTasks } from '../src/scene-tasks.mjs';

const center = [.25,.15,.5,.7], edge = [.88,.02,.1,.18];
function fixture() {
  let time = 10000, clip = 0;
  const target = new GuideTarget(() => time);
  const observation = (patch = {}) => ({ state: 'matched', subject: '前景花瓶', relation: 'new', previous: 'absent',
    boxes: [center, center], summary: '瓶口收窄', clip: ++clip, at: time, ...patch });
  return { target, observation, tick(ms = 2100) { time += ms; } };
}
test('foreground wins over incidental edge objects; wording, angle and closeup do not reset identity', () => {
  const { target: t, observation: o, tick } = fixture();
  assert.equal(t.observe(o({ boxes: [edge, edge] })).accepted, false);
  assert.ok(t.observe(o()).accepted); const id = t.current.id;
  tick(); assert.equal(t.observe(o({ subject: '背景树', previous: 'visible' })).accepted, false);
  assert.equal(t.current.id, id);
  tick();
  assert.ok(t.observe(o({ subject: '陶制容器特写', relation: 'same', previous: 'visible', boxes: [[.1,.05,.8,.9],[.1,.05,.8,.9]] })).accepted);
  assert.equal(t.current.id, id); assert.equal(t.current.subject, '前景花瓶');
  t.move(true); t.move(false); tick();
  assert.equal(t.context().target, null);
  assert.ok(t.observe(o({subject:'下一件物品'})).accepted);
  assert.notEqual(t.current.id, id);
});
test('two neighboring exhibits need consistent new evidence; a clear camera switch can accept one fresh two-frame clip', () => {
  const { target: t, observation: o, tick } = fixture(); t.observe(o()); const id = t.current.id;
  tick(); assert.equal(t.observe(o({ subject: '第二件', previous: 'background' })).accepted, false);
  assert.equal(t.current.id, id);
  tick(); t.observe({ state: 'unknown', reason: 'occluded' });
  tick(); assert.equal(t.observe(o({ subject: '第二件', previous: 'background' })).accepted, false);
  tick(); assert.ok(t.observe(o({ subject: '第二件', previous: 'background' })).accepted);
  t.move(true); t.move(false); tick(); assert.ok(t.observe(o({ subject: '第三件' })).accepted);
});
test('a clear replacement in a stationary view can be confirmed in one two-frame observation', () => {
  const cases = [
    { label: 'both frames clear', previousFrames: ['absent', 'absent'], accepted: true, status: 'locked' },
    { label: 'no per-frame evidence', accepted: false, status: 'candidate' },
    { label: 'old object partly obscured', previousFrames: ['absent', 'uncertain'], accepted: false, status: 'ambiguous' },
    { label: 'old object visible in one frame', previousFrames: ['visible', 'absent'], accepted: false, status: 'ambiguous' },
    { label: 'new object shifts between frames', previousFrames: ['absent', 'absent'],
      boxes: [[.15, .2, .4, .55], [.38, .25, .4, .55]], accepted: false, status: 'candidate' },
    { label: 'old object still in background', previous: 'background', previousFrames: ['visible', 'visible'],
      accepted: false, status: 'candidate' },
  ];
  for (const item of cases) {
    const { target, observation, tick } = fixture();
    assert.ok(target.observe(observation({ subject: '吐司挂件' })).accepted);
    const oldId = target.current.id;
    tick();
    const clip = observation();
    const reply = validateGuide(JSON.stringify({
      clip: clip.clip, state: 'matched', subject: '白色罗技鼠标', relation: 'new', previous: item.previous ?? 'absent',
      previousFrames: item.previousFrames, boxes: (item.boxes ?? [center, center]).map(modelBox),
      summary: '白色鼠标，顶部有标志', evidence: [{ frame: 1, detail: '白色外壳' }, { frame: 2, detail: '鼠标滚轮' }],
    }), { id: clip.clip, endAt: clip.at, frames: 2 }, clip.at);
    const result = target.observe(reply);
    assert.equal(result.accepted, item.accepted, item.label);
    assert.equal(target.status, item.status, item.label);
    assert.equal(target.current.id !== oldId, item.accepted, item.label);
  }
});
test('two small edge boxes release an automatic subject even when the model recognizes it as the same object', () => {
  const { target: t, observation: o, tick } = fixture();
  t.observe(o());
  const id = t.current.id;
  const fragment = [.87,.02,.11,.27];
  const same = { relation: 'same', previous: 'visible' };
  tick();
  const full = [.75,.02,.24,.5];
  assert.ok(t.observe(o({ ...same, boxes: [full, fragment] })).accepted);
  assert.equal(t.current.id, id);
  tick();
  const result = t.observe(o({ ...same, boxes: [fragment, fragment] }));
  assert.equal(result.accepted, false);
  assert.equal(result.released, true);
  assert.equal(t.reason, 'target_at_edge');
  assert.equal(t.context().target, null);
  tick();
  assert.ok(t.observe(o({ subject: '新物品' })).accepted);
  assert.notEqual(t.current.id, id);
});

test('a voice-preferred target can remain visible at the edge', () => {
  const { target: t, observation: o, tick } = fixture();
  t.select({ hint: '前景花瓶' });
  t.observe(o());
  const id = t.current.id;
  tick();
  assert.ok(t.observe(o({ relation: 'same', previous: 'visible', boxes: [edge, edge] })).accepted);
  assert.equal(t.current.id, id);
});
test('explicit off-center point must lie inside both fresh boxes and expires back to automatic observation', () => {
  const { target: t, observation: o, tick } = fixture(); t.observe(o());
  t.select({ point: { x: .92, y: .1 } });
  assert.equal(t.observe(o()).accepted, false);
  assert.ok(t.observe(o({ subject: '边角小展品', previous: 'visible', boxes: [edge, edge] })).accepted);
  t.select({ point: { x: .5, y: .5 } }); tick(12001);
  assert.equal(t.observe(o()).accepted, false); assert.equal(t.reason, 'selection_expired');
  assert.equal(t.status, 'searching'); assert.equal(t.preferred, false); assert.equal(t.selection, undefined);
  t.select({ point: { x: .5, y: .5 } });
  t.move(true); t.move(false); assert.equal(t.selection, undefined);
});
test('a tap corrects one subject and normal selection can then choose a new foreground object', () => {
  const { target: t, observation: o, tick } = fixture();
  t.select({ point: { x: .92, y: .1 } });
  assert.ok(t.observe(o({ subject: '点选的花瓶', boxes: [edge, edge] })).accepted);
  assert.equal(t.context().selection, null);
  assert.equal(t.snapshot().preferred, false);

  const originalId = t.current.id;
  tick();
  assert.equal(t.observe(o({ subject: '新展示的物品', previous: 'background' })).accepted, false);
  assert.equal(t.status, 'candidate');
  assert.equal(t.current.id, originalId);
  tick();
  assert.ok(t.observe(o({ subject: '新展示的物品', previous: 'background' })).accepted);
  assert.equal(t.current.subject, '新展示的物品');
});

test('a voice-preferred target survives occlusion, background, replay and movement, then clears on two fresh absence clips', () => {
  const { target: t, observation: o, tick } = fixture();
  t.select({ hint: '前景花瓶' }); assert.ok(t.observe(o()).accepted);
  const id = t.current.id;
  const absent = () => o({ state: 'unknown', reason: 'no_subject', previousAbsent: true });
  tick(260); const first = absent(); t.observe(first);
  assert.equal(t.status, 'confirming_exit'); assert.equal(t.snapshot().box, undefined);
  tick(260); t.observe(first); assert.equal(t.current.id, id);
  t.observe(o({ state: 'unknown', reason: 'occluded', previousAbsent: false }));
  tick(260); t.observe(absent()); assert.equal(t.current.id, id);
  tick(260); t.observe(o({ subject: '旁边的物品', previous: 'background' }));
  assert.equal(t.current.id, id); assert.equal(t.preferred, true);
  tick(260); t.observe(absent());
  t.move(true); t.move(false);
  tick(260); t.observe(absent()); assert.equal(t.current.id, id);
  tick(260); assert.ok(t.observe(o({ relation: 'same', previous: 'visible' })).accepted);
  assert.equal(t.current.id, id);
  tick(260); t.observe(absent());
  tick(260); assert.ok(t.observe(absent()).released);
  assert.equal(t.context().target, null); assert.equal(t.preferred, false);
  tick(260); assert.ok(t.observe(o({ subject: '新物品' })).accepted);
  assert.notEqual(t.current.id, id);
});

test('absence must be explicit in both frames; the second clip can also supply the next subject', () => {
  const { target: t, observation: o, tick } = fixture();
  t.select({ hint: '前景花瓶' }); assert.ok(t.observe(o()).accepted);
  const observe = (response, age = 0) => {
    tick(260); const frame = o();
    return validateGuide(JSON.stringify({ clip: frame.clip, ...response }),
      { id: frame.clip, endAt: frame.at, frames: 2 }, frame.at + age);
  };
  const empty = { state: 'unknown', reason: 'no_subject', previousFrames: ['absent', 'absent'] };
  for (const input of [{ ...empty, previousFrames: ['absent', 'uncertain'] }, { ...empty, reason: 'occluded' }])
    assert.equal(observe(input).previousAbsent, false);
  assert.equal(observe(empty, 5001).reason, 'stale_evidence');
  assert.equal(t.observe(observe(empty)).accepted, false);
  const matched = { state: 'matched', subject: '新物品', relation: 'new', previous: 'absent', summary: '新讲解',
    previousFrames: empty.previousFrames, boxes: [modelBox(center), modelBox(center)],
    evidence: [{ frame: 1, detail: '新物品' }, { frame: 2, detail: '新物品' }] };
  assert.ok(t.observe(observe(matched)).released);
  assert.equal(t.current.subject, '新物品'); assert.equal(t.preferred, false);
});

test('confirmed exit retires the subject and its speech while preserving the guide task', () => {
  let now = 10000;
  const retired = [];
  const scheduler = { createLiveNarration: () => ({ taskId: 'guide' }), cancelTaskById() {},
    undeliverDelivery: delivery => retired.push(delivery.deliveryId) };
  const scenes = new SceneTasks(() => scheduler, () => {}, { now: () => now });
  const { id, revision } = scenes.start({ mode: 'guide', goal: '展品', focus: '展品', depth: 'normal', steps: [] });
  scenes.control({ id, revision, action: 'target', targetHint: '物品 A' });
  const seen = { state: 'matched', relation: 'new', previous: 'absent', subject: 'A', summary: 'A 的讲解', boxes: [center, center], at: now };
  assert.ok(scenes.acceptGuide('guide', seen));
  const delivery = { taskId: 'guide', deliveryId: 'old-speech' };
  scenes.onDelivery(delivery);
  scenes.playbackStarted(delivery);
  const absent = { state: 'unknown', reason: 'no_subject', previousAbsent: true };
  now += 260; assert.equal(scenes.acceptGuide('guide', { ...absent, at: now, clip: 2 }), false);
  assert.deepEqual(retired, ['old-speech']);
  now += 260; assert.equal(scenes.acceptGuide('guide', { ...absent, at: now, clip: 3 }), false);
  scenes.acknowledged(delivery);
  assert.equal(scenes.snapshot().id, id);
  assert.equal(scenes.snapshot().status, 'watching');
  assert.equal(scenes.snapshot().segments, 0);
  assert.equal(scenes.monitorContext('guide').target, null);
  now += 260; assert.ok(scenes.acceptGuide('guide', { ...seen, subject: 'B', summary: 'B 的讲解', at: now }));
  scenes.close();
});
test('ambiguous observations ask only after a second distinct fresh observation; stale or malformed boxes cannot speak', () => {
  const { target: t, observation: o, tick } = fixture();
  assert.equal(t.observe(o({ state: 'unknown', reason: 'ambiguous' })).ask, false);
  assert.equal(t.observe(o({ state: 'unknown', reason: 'ambiguous' })).ask, false);
  tick(); assert.equal(t.observe(o({ state: 'unknown', reason: 'ambiguous' })).ask, true);
  const value = { ...o(), boxes:[modelBox(center),modelBox(center)], evidence: [{frame:1,detail:'瓶子'},{frame:2,detail:'同一瓶子'}] };
  const clip = { id: value.clip, endAt: value.at, frames: 2 };
  for (const boxes of [undefined, [[0,0,1,1],[0,0,1,1]], [[.1,.1,-.2,.3], center], [[.9,.1,.3,.3],center]])
    assert.equal(validateGuide(JSON.stringify({...value,boxes}),clip,value.at).state,'unknown');
  assert.equal(validateGuide(JSON.stringify(value),clip,value.at+5001).state,'unknown');
  assert.deepEqual(normalizedBox([354,180,609,862]),[.354,.18,.255,.682]);
  assert.equal(validateGuide(JSON.stringify(value),clip,value.at).state,'matched');
});
test('guide starts fresh after a camera cut; clarification is bounded and tap controls cannot be invented by tools', () => {
  let time = 10000, seq = 0; const tasks = [], retired = [];
  const create = input => { const task = { ...input, taskId: `t-${++seq}` }; tasks.push(task); return task; };
  const scheduler = { createLiveNarration: create, createTimer: create, cancelTaskById() {}, undeliverDelivery: d => retired.push(d.deliveryId) };
  const scenes = new SceneTasks(() => scheduler, () => {}, { now: () => time });
  scenes.start({ mode:'guide', goal:'展品', focus:'展品', depth:'brief', steps:[] }); const id = tasks[0].taskId;
  const o = { state:'matched',subject:'A',summary:'内容',relation:'new',previous:'absent',boxes:[center,center],at:time };
  assert.ok(scenes.acceptGuide(id,o)); scenes.onDelivery({taskId:id,deliveryId:'a'}); scenes.acknowledged({taskId:id,deliveryId:'a'});
  scenes.guideMotion(true); scenes.guideMotion(false); time += 2100;
  assert.equal(scenes.monitorContext(id).target, null);
  assert.equal(scenes.monitorContext(id).subject, '');
  assert.deepEqual(scenes.monitorContext(id).alreadySpoken, []);
  assert.ok(scenes.acceptGuide(id,{...o,at:time,subject:'B'}));
  assert.equal(scenes.snapshot().segments,0);
  for (let i=0;i<5;i++) { time += 2100; scenes.acceptGuide(id,{state:'unknown',reason:'ambiguous',at:time}); }
  assert.equal(tasks.filter(t=>t.durationSec).length,1);
  const question=tasks.find(t=>t.durationSec);
  const delivery={taskId:question.taskId,deliveryId:'question'};
  scenes.onDelivery(delivery);scenes.acknowledged(delivery);scenes.changed(question,'delivered');
  assert.equal(scenes.snapshot().status,'watching');assert.equal(scenes.snapshot().segments,0);
  const command = { id:scenes.state.id,revision:scenes.state.revision,action:'target',view:scenes.target.view,point:{x:.5,y:.5} };
  assert.throws(()=>scenes.control(command), /invalid_target/);
  scenes.control(command,{trustedUser:true});
  assert.equal(scenes.monitorContext(id).focus,'展品');
  assert.ok(scenes.current(id)); assert.equal(tasks.filter(t=>!t.durationSec).length,1);
  assert.equal(scenes.state.view,3); scenes.close();
});
