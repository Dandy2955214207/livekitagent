import { GuideMotion } from '../src/guide-motion.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { SceneTasks } from '../src/scene-tasks.mjs';
import { sceneVoice } from '../src/scene-voice.mjs';
import { validateGuide, modelBox } from '../src/guide-monitor.mjs';
import { createRuntime } from '../src/runtime.mjs';
import { fakeProvider, until } from './fake-provider.mjs';

const lesson = { mode: 'teach', goal: '画小猪', focus: '纸上的小猪简笔画', depth: 'normal', steps: [
  { instruction: '先画一个大圆作为头部', completion: '纸上出现闭合的大圆轮廓' },
  { instruction: '在大圆内画一个椭圆鼻子', completion: '大圆内部出现完整的椭圆鼻子' },
] };
function fixture() {
  let seq = 0; const tasks = [], events = [], cancelled = [], retired = [];
  const create = input => { const task = { ...input, taskId: `task-${++seq}` }; tasks.push(task); return task; };
  const scheduler = { createTimer: create, createPerceptionMonitor: create, createLiveNarration: create,
    cancelTaskById: id => cancelled.push(id), undeliverDelivery: d => retired.push(d.deliveryId) };
  const scenes = new SceneTasks(() => scheduler, e => events.push(e));
  return { scenes, tasks, events, cancelled, retired };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const guideObservation = (subject, summary, extra = {}) => ({ state: 'matched', subject, summary, at: Date.now(),
  relation: 'new', previous: 'absent', boxes: [[.2,.15,.6,.7],[.2,.15,.6,.7]], ...extra });

test('teaching retains parent, only starts checks after instruction delivery, and completes only last step', async () => {
  const { scenes, tasks } = fixture(); scenes.start(lesson);
  assert.equal(scenes.snapshot().status, 'instructing'); assert.equal(tasks.length, 1);
  scenes.changed({ ...tasks[0], status: 'completed' }, undefined); await settle();
  assert.equal(tasks.length, 1);
  scenes.changed({ ...tasks[0], status: 'completed' }, 'delivered'); await settle();
  assert.equal(scenes.snapshot().status, 'waiting_step'); assert.equal(tasks[1].repeat, false);
  const d = { taskId: tasks[1].taskId, deliveryId: 'd1' }; scenes.onDelivery(d);
  assert.equal(scenes.snapshot().step, 2); assert.equal(scenes.snapshot().completedSteps, 1); assert.equal(scenes.snapshot().status, 'advancing');
  scenes.changed({ ...tasks[1], status: 'completed' }, 'delivered'); await settle();
  assert.equal(scenes.snapshot().step, 2); assert.equal(scenes.snapshot().active, true);
  scenes.onDelivery({ taskId: tasks[2].taskId, deliveryId: 'd2' });
  scenes.changed({ ...tasks[2], status: 'completed' }, 'delivered'); await settle();
  assert.equal(scenes.snapshot().status, 'completed'); scenes.close();
});
test('pause cancels old child; stale receipts and old commands cannot advance resumed teaching', async () => {
  const { scenes, tasks, cancelled } = fixture(); scenes.start(lesson);
  const rev = scenes.snapshot().revision;
  scenes.control({ action: 'pause', revision: rev });
  assert.equal(cancelled.length, 1);
  scenes.changed({ ...tasks[0], status: 'completed' }, 'delivered'); await settle();
  assert.equal(scenes.snapshot().status, 'paused');
  assert.throws(() => scenes.control({ action: 'resume', revision: rev }), /stale/);
  scenes.control({ action: 'resume', revision: scenes.snapshot().revision });
  assert.equal(scenes.snapshot().step, 1); assert.equal(scenes.snapshot().status, 'instructing'); scenes.close();
});
test('model may not invent user completion; unknown/ordinary questions do not move the step', async () => {
  const { scenes, tasks } = fixture(); scenes.start(lesson);
  scenes.changed({ ...tasks[0], status: 'completed' }, 'delivered'); await settle();
  assert.throws(() => scenes.control({ action: 'confirm_step', revision: scenes.snapshot().revision }, { transcript: '这个圆怎么画？' }), /explicit_user/);
  for (const transcript of ['我还没画好了，别进行下一步', '这样算完成了吗？'])
    assert.throws(() => scenes.control({ action: 'confirm_step', revision: scenes.snapshot().revision }, { transcript }), /explicit_user/);
  assert.equal(scenes.snapshot().step, 1);
  scenes.control({ action: 'confirm_step', revision: scenes.snapshot().revision }, { transcript: '这步完成了' });
  assert.equal(scenes.snapshot().step, 2); assert.equal(scenes.snapshot().status, 'instructing'); scenes.close();
});

test('exhausted budget cannot restart teaching children', () => {
  const { scenes, tasks } = fixture(); scenes.start(lesson); scenes.pauseForBudget();
  assert.throws(() => scenes.control({ action: 'repeat_step', revision: scenes.snapshot().revision }), /budget/);
  assert.equal(tasks.length, 1); scenes.close();
});

test('a spoken confirmation applies only to the step active when the user started speaking', async () => {
  const { scenes, tasks } = fixture();
  scenes.start(lesson);
  scenes.changed({ ...tasks[0], status: 'completed' }, 'delivered');
  await settle();
  const first = scenes.snapshot();
  const outputs = [];
  const voice = sceneVoice(scenes, { onOutputAudioDelta: event => outputs.push(event) });
  voice.callbacks.onSpeechStarted({ itemId: 'user-1' });
  const monitor = tasks[1];
  scenes.onDelivery({ taskId: monitor.taskId, deliveryId: 'first-confirmed-visually' });
  scenes.changed({ ...monitor, status: 'completed' }, 'delivered');
  await settle();
  const second = scenes.snapshot();
  assert.equal(second.status, 'waiting_step');
  assert.equal(second.completedSteps, 1);

  voice.callbacks.onResponseCreated({ responseId: 'reply-1', inputItemId: 'user-1', authority: 'direct' });
  voice.callbacks.onOutputAudioDelta({ responseId: 'reply-1', audio: new Uint8Array(2) });
  voice.callbacks.onInputTranscriptDone({ itemId: 'user-1', text: '这一步画好了。' });
  assert.equal(voice.confirmation({ inputItemId: 'user-1' }, { id: first.id, revision: second.revision, action: 'confirm_step' }).completedSteps, 1);
  voice.callbacks.onResponseDone({ responseId: 'reply-1', status: 'completed' });
  assert.equal(outputs.length, 0);
  assert.equal(scenes.snapshot().completedSteps, 1);
  scenes.close();
});

test('a scene receipt leaves an independent tool result audible in the same turn', () => {
  const inputItemId = 'user-1';
  const played = [];
  const voice = sceneVoice({ snapshot: () => ({ active: false }) }, {
    onFunctionCall(event) {
      if (event.name === 'start_scene_task') voice.delegate(event, { ok: true, mode: 'guide' });
    },
    onOutputAudioDelta(event) { played.push(event); },
  });
  const cb = voice.callbacks;
  cb.onInputTranscriptDone({ itemId: inputItemId, text: '介绍接下来展示的物品，也查一下我的提醒任务' });
  cb.onFunctionCall({ name: 'start_scene_task', inputItemId });
  cb.onFunctionCall({ name: 'list_requirements', inputItemId });
  cb.onResponseCreated({ responseId: 'tool-results', inputItemId, authority: 'tool_continuation' });
  cb.onOutputAudioDelta({ responseId: 'tool-results', audio: new Uint8Array(2) });
  assert.equal(played.length, 1);
});

test('cancelled planning and malformed plans cannot install a teaching child', async () => {
  const { scenes, tasks } = fixture(); let resolve;
  scenes.beginTeaching(lesson, { plan: () => new Promise(r => { resolve = r; }) }, '画小猪');
  assert.equal(scenes.snapshot().status, 'planning');
  scenes.control({ action: 'stop', revision: scenes.snapshot().revision });
  resolve(lesson.steps); await settle(); assert.equal(tasks.length, 0);
  scenes.beginTeaching(lesson, { plan: async () => [{ instruction: '缺少完成条件' }] }, '画小猪');
  await settle(); assert.equal(scenes.snapshot().status, 'failed'); assert.equal(tasks.length, 0); scenes.close();
});
test('guide depth caps delivered segments, excludes queued repeats, and retires old subject speech', async () => {
  const { scenes, tasks, retired } = fixture(); scenes.start({ mode: 'guide', goal: '介绍植物', focus: '镜头主体植物', depth: 'brief', steps: [] });
  const id = tasks[0].taskId, a = guideObservation('左边的银杏', '扇形叶片是可观察的特征');
  assert.equal(scenes.acceptGuide(id, a), true);
  scenes.onDelivery({ taskId: id, deliveryId: 'a1' });
  assert.equal(scenes.acceptGuide(id, { ...a, relation: 'same', previous: 'visible' }), false);
  assert.equal(scenes.acceptGuide(id, guideObservation('右边的竹子', '叶片狭长')), false);
  await new Promise(r => setTimeout(r, 2));
  assert.equal(scenes.acceptGuide(id, guideObservation('右边的竹子', '叶片狭长')), true);
  assert.deepEqual(retired, ['a1']); assert.equal(scenes.snapshot().segments, 0);
  scenes.acknowledged({ taskId: id, deliveryId: 'a1' }); assert.equal(scenes.snapshot().segments, 0);
  scenes.onDelivery({ taskId: id, deliveryId: 'b1' }); scenes.acknowledged({ taskId: id, deliveryId: 'b1' });
  assert.equal(scenes.snapshot().segments, 1);
  assert.equal(scenes.acceptGuide(id, guideObservation('右边的竹子', '第二个特点', { relation: 'same', previous: 'visible' })), false); scenes.close();
});
test('guide cancels pending narration when both frames show only an edge fragment', () => {
  const { scenes, tasks, retired } = fixture();
  scenes.start({ mode: 'guide', goal: '物品', focus: '眼前物品', depth: 'normal', steps: [] });
  const taskId = tasks[0].taskId;
  const delivery = { taskId, deliveryId: 'old-narration' };
  assert.ok(scenes.acceptGuide(taskId, guideObservation('旧物品', '待播内容')));
  scenes.onDelivery(delivery);

  const fragment = [.87, .02, .11, .27];
  const edgeObservation = guideObservation('旧物品', '另一条内容', {
    relation: 'same', previous: 'visible', boxes: [fragment, fragment],
  });
  assert.equal(scenes.acceptGuide(taskId, edgeObservation), false);
  assert.deepEqual(retired, ['old-narration']);
  scenes.acknowledged(delivery);
  assert.equal(scenes.snapshot().segments, 0);
  assert.equal(scenes.monitorContext(taskId).target, null);
  assert.ok(scenes.acceptGuide(taskId, guideObservation('新物品', '从新证据讲解')));
  scenes.close();
});
test('guide rejects unknown, stale, wrong clip or unsupported single-frame prose', () => {
  const good = { ...guideObservation('白色杯子', '杯口呈圆形'), clip: 1, evidence: [{ frame: 1, detail: '白色带柄杯' }, { frame: 2, detail: '同一白色杯' }] };
  good.boxes = good.boxes.map(modelBox);
  const clip = { id: 1, frames: 2, endAt: 10000 };
  assert.equal(validateGuide(JSON.stringify(good), clip, 11000).state, 'matched');
  for (const value of [{ ...good, clip: 0 }, { ...good, state: 'unknown' }, { ...good, evidence: [good.evidence[0]] }])
    assert.equal(validateGuide(JSON.stringify(value), clip, 11000).state, 'unknown');
  assert.equal(validateGuide(JSON.stringify(good), clip, 16000).state, 'unknown');
});
test('real official custom tool creates a persistent lesson and browser controls complete all steps', async () => {
  const p = await fakeProvider(), events = [];
  const config = { callId: 'scene-call', apiKey: 'fixture', model: 'qwen3.8-omni-flash-realtime', voice: 'Tina',
    maxGenerations: 20, maxMonitorGenerations: 10, proactiveEnabled: true };
  const runtime = await createRuntime(config, e => { events.push(e); return true; }, { gateway: { upstreamUrl: p.url } });
  const acknowledged = new Set();
  const timer = setInterval(() => {
    for (const e of events.filter(e => e.type === 'audio_end')) {
      if (acknowledged.has(e.outputId)) continue; acknowledged.add(e.outputId);
      void runtime.handle({ type: 'playback_started', callId: config.callId, epoch: 1, outputId: e.outputId });
      void runtime.handle({ type: 'playback_completed', callId: config.callId, epoch: 1, outputId: e.outputId, sequence: e.sequence });
    }
  }, 10);
  try {
    await runtime.start();
    assert.ok(p.peers[0].session.tools.some(t => t.function?.name === 'start_scene_task'));
    p.turn(p.peers[0], { text: '指导我画小猪', beforeTool: '好的，我来教你画小猪。', tool: {
      name: 'start_scene_task', arguments: JSON.stringify({ mode: 'teach', goal: lesson.goal, depth: 'normal' }),
    } });
    await until(() => runtime.scenes.snapshot().status === 'waiting_step', 7000);
    assert.equal(runtime.scenes.snapshot().step, 1);
    assert.equal(events.filter(e => e.type === 'audio_end').length, 1, 'only the scheduled first step should play');
    assert.equal(runtime.gateway.stats.planGenerations, 1);
    const before = runtime.scenes.snapshot();
    p.send(p.peers[0], { type: 'error', error: { code: 'COMMON_ERROR', type: 'server_error', status: 500,
      message: '<50002> InternalError.Algo.ModelServingError: synthetic failure' } });
    await until(() => events.some(e => e.type === 'recovery' && e.phase === 'completed'));
    assert.deepEqual(runtime.scenes.snapshot(), before);
    assert.equal(runtime.gateway.stats.planGenerations, 1);
    assert.ok(!events.some(e => e.type === 'fatal'));
    const foreground = () => p.peers.findLast(peer => peer.session.tools?.some(tool => tool.function?.name === 'start_scene_task'));
    p.turn(foreground(), { text: '这个圆怎么画？' });
    await until(() => events.filter(e => e.type === 'audio_end').length === 2);
    await until(() => acknowledged.size === 2);
    assert.equal(runtime.scenes.snapshot().completedSteps, 0);
    const step = runtime.scenes.snapshot();
    p.turn(foreground(), { text: '我画完了，可以继续。', beforeTool: '圆画完了，下面画鼻子。', tool: {
      name: 'control_scene_task', arguments: JSON.stringify({ action: 'confirm_step', id: step.id, revision: step.revision }),
    } });
    await until(() => runtime.scenes.snapshot().step === 2 && runtime.scenes.snapshot().status === 'waiting_step', 7000);
    assert.equal(events.filter(e => e.type === 'audio_end').length, 3, 'only the scheduled next step should play');
    assert.equal(runtime.scenes.snapshot().completedSteps, 1);
    await runtime.handle({ type: 'scene_control', callId: config.callId, epoch: 1, action: 'confirm_step', id: runtime.scenes.snapshot().id, revision: runtime.scenes.snapshot().revision });
    assert.equal(runtime.scenes.snapshot().progress, 'completed');
    await until(() => runtime.scenes.snapshot().status === 'completed');
  } finally { clearInterval(timer); await runtime.close(); await p.close(); }
});

test('official narration transport delivers focused guide evidence and accounts only actual playback', async () => {
  let sampleSubject = '白色杯子';
  const provider = await fakeProvider({ monitorReply(event) {
    const ctx = JSON.parse(event.response.instructions);
    assert.equal(ctx.focus, '只讲当前水杯');
    return 'Reply: ' + JSON.stringify({ ...guideObservation(sampleSubject, '杯口呈圆形',
      { relation: ctx.target?.subject === sampleSubject ? 'same' : 'new', previous: ctx.target?.subject === sampleSubject ? 'visible' : 'absent' }), clip: ctx.clip,
      boxes: [modelBox([.2,.15,.6,.7]),modelBox([.2,.15,.6,.7])], evidence: [{ frame: 1, detail: '白色带柄杯' }, { frame: 2, detail: '同一白色杯' }] });
  } });
  const config = { callId: 'guide-call', apiKey: 'fixture', model: 'qwen3.8-omni-flash-realtime', voice: 'Tina',
    maxGenerations: 12, maxMonitorGenerations: 6, proactiveEnabled: true };
  const events = [], runtime = await createRuntime(config, e => (events.push(e), true), { gateway: { upstreamUrl: provider.url } });
  const ack = async end => {
    await runtime.handle({ type: 'playback_started', callId: config.callId, epoch: 1, outputId: end.outputId });
    await runtime.handle({ type: 'playback_completed', callId: config.callId, epoch: 1, outputId: end.outputId, sequence: end.sequence });
  };
  try {
    await runtime.start();
    const foreground = provider.peers[0];
    provider.turn(foreground, { text: '持续讲解眼前这个杯子', beforeTool: '好的，我会介绍眼前的杯子。', tool: {
      name: 'start_scene_task', arguments: JSON.stringify({ mode: 'guide', goal: '只讲当前水杯', depth: 'brief' }),
    } });
    await until(() => foreground.responses >= 2 && events.some(e => e.type === 'intent' && e.status === 'running'));
    await runtime.handle({ type: 'audio', callId: config.callId, epoch: 1, audio: Buffer.alloc(640).toString('base64') });
    const frame = { grid: Buffer.alloc(288,20).toString('base64'), type: 'image', callId: config.callId, epoch: 1, width: 10, height: 10, image: Buffer.from([255, 216, 255, 217]).toString('base64') };
    await runtime.handle(frame); await new Promise(r => setTimeout(r, 1050)); await runtime.handle(frame);
    await until(() => events.some(e => e.type === 'latency' && e.metric === 'intervention_to_first_audio') &&
      events.some(e => e.type === 'audio_end'));
    assert.equal(events.filter(e => e.type === 'audio_end').length, 1, 'only the first grounded narration should play');
    assert.ok(!events.some(e => e.type === 'transcript' && e.role === 'assistant' && e.text.includes('我会介绍眼前')));
    assert.equal(runtime.scenes.snapshot().segments, 0);
    await ack(events.filter(e => e.type === 'audio_end').at(-1));
    assert.equal(runtime.scenes.snapshot().segments, 1); assert.equal(runtime.scenes.snapshot().status, 'watching');
    provider.turn(foreground, { text: '这个杯子是什么材质？' });
    await until(() => events.filter(e => e.type === 'audio_end').length === 2);
    await ack(events.filter(e => e.type === 'audio_end').at(-1));
    assert.equal(runtime.scenes.snapshot().segments, 1);
    const next = { ...frame, grid: Buffer.alloc(288,190).toString('base64') };
    sampleSubject = '黑色杯子';
    await runtime.handle(next); assert.ok(runtime.scenes.snapshot().moving);
    for (let i=0;i<3;i++) { await new Promise(r=>setTimeout(r,1050)); await runtime.handle(next); }
    await until(()=>events.filter(e=>e.type==='audio_end').length===3);
    const playing=events.filter(e=>e.type==='audio_end').at(-1);
    await runtime.handle({type:'playback_started',callId:config.callId,epoch:1,outputId:playing.outputId});
    await runtime.handle(frame);
    assert.ok(events.some(e=>e.type==='audio_clear' && e.throughId===playing.outputId));
    await ack(playing); assert.equal(runtime.scenes.snapshot().segments,0);
  } finally { await runtime.close(); await provider.close(); }
});

test('guide camera cut retires old narration before a new subject is identified', () => {
  const { scenes, tasks, retired } = fixture();
  scenes.start({mode:'guide',goal:'展品',focus:'展品',depth:'deep',steps:[]});
  let clock = 10000;
  const tracker=new GuideMotion(() => clock), grid=Buffer.alloc(288,20);
  const feed=()=>{const change=tracker.sample(grid.toString('base64'));if(change)scenes.guideMotion(change==='moving');};
  feed(); scenes.acceptGuide(tasks[0].taskId,guideObservation('A','old'));
  scenes.onDelivery({taskId:tasks[0].taskId,deliveryId:'old'});
  grid.fill(80,0,24);feed();assert.deepEqual(retired,[]);
  grid.fill(185);feed();assert.deepEqual(retired,['old']);assert.ok(scenes.snapshot().moving);
  assert.equal(scenes.acceptGuide(tasks[0].taskId,{subject:'A',summary:'late'}),false);
  clock += 250; feed();assert.equal(scenes.snapshot().moving,false);
  scenes.acknowledged({taskId:tasks[0].taskId,deliveryId:'old'});assert.equal(scenes.snapshot().segments,0);
  scenes.close();
});
