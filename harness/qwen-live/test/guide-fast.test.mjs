import test from 'node:test';
import assert from 'node:assert/strict';
import { createEvidenceMonitor } from '../src/evidence-monitor.mjs';
import { createRuntime } from '../src/runtime.mjs';
import { fakeProvider, until } from './fake-provider.mjs';
import { GuideOpening, waitsForPresentation } from '../src/guide-opening.mjs';
import { setTimeout as delay } from 'node:timers/promises';

const grid = (value, cells = 0) => {
  const pixels = Buffer.alloc(288, 210);
  pixels.fill(value, 36, 36 + 3 * cells);
  return pixels.toString('base64');
};
const jpeg = marker => Buffer.from([255, 216, marker, 255, 217]).toString('base64');

test('guide opening probes a still scene slowly and supersedes an old probe when a presentation changes', () => {
  const empty = grid(210);
  const opening = new GuideOpening(empty, 10000);
  opening.sample(empty, 10010);
  opening.sample(empty, 10300);
  assert.equal(opening.ready(10300), false);
  assert.equal(opening.ready(14000), true, 'an already positioned object can be checked after the initial wait');
  opening.requested(14000);
  assert.equal(opening.ready(16000), false, 'an empty fallback does not immediately repeat');
  assert.equal(opening.sample(grid(30, 20), 16250), true);
  assert.equal(opening.version, 1);
  assert.equal(opening.ready(16250), false);
  opening.sample(grid(90, 10), 16500);
  assert.equal(opening.ready(16500), false);
  opening.sample(grid(90, 10), 16750);
  assert.equal(opening.ready(16750), true);
  assert.equal(opening.version, 1, 'one in-flight probe only needs one invalidation');
  opening.finish();
  assert.equal(opening.ready(16750), true);
});

test('only a future presentation requests the initial guide hold', () => {
  assert.ok(waitsForPresentation('接下来，依次介绍镜头中展示的一些物品。'));
  assert.ok(waitsForPresentation('等我摆好，给我讲解这些展品。'));
  assert.equal(waitsForPresentation('介绍眼前这个，接下来我再展示其他物品。'), false);
  assert.equal(waitsForPresentation('持续讲解眼前摆好的几个物品。'), false);
});

for (const future of [true, false]) {
  test('real official guide startup ' + (future ? 'submits the settled object before any empty clip' : 'checks an already displayed object immediately'), async () => {
    const checks = [], chunks = new Map(), events = [];
    const provider = await fakeProvider({
      onRequest(event, peer) {
        if (event.type === 'input_image_buffer.append') {
          if (!chunks.has(peer)) chunks.set(peer, []);
          chunks.get(peer).push(event.image);
        }
      },
      monitorReply(event, peer) {
        const context = JSON.parse(event.response.instructions);
        checks.push(chunks.get(peer)?.slice() ?? []);
        return 'Reply: ' + JSON.stringify({ clip: context.clip, state: 'matched', subject: '吐司挂件',
          relation: 'new', previous: 'absent', boxes: [[200,150,800,850],[200,150,800,850]], summary: '吐司形状的挂件',
          evidence: [{ frame: 1, detail: '吐司形状' }, { frame: 2, detail: '挂绳清晰' }] });
      },
    });
    const identity = { callId: 'guide-opening', epoch: 1 };
    const runtime = await createRuntime({ ...identity, apiKey: 'fixture', model: 'qwen3.8-omni-flash-realtime', voice: 'Tina',
      maxGenerations: 20, maxMonitorGenerations: 10, proactiveEnabled: true },
    event => (events.push(event), true), { gateway: { upstreamUrl: provider.url } });
    const empty = { ...identity, type: 'image', width: 10, height: 10, image: jpeg(1), grid: grid(210) };
    const object = { ...empty, image: jpeg(2), grid: grid(90, 10) };
    try {
      await runtime.start();
      await runtime.handle(future ? empty : object);
      provider.turn(provider.peers[0], {
        text: future ? '接下来，依次介绍镜头中展示的一些物品。' : '持续讲解眼前摆好的这件物品。',
        tool: { name: 'start_scene_task', arguments: JSON.stringify({ mode: 'guide', goal: '介绍展品', depth: 'brief' }) },
      });
      await until(() => events.some(event => event.type === 'intent' && event.status === 'running'));
      if (future) {
        for (let i = 0; i < 3; i++) { await runtime.handle(empty); await delay(260); }
        assert.equal(checks.length, 0);
        await runtime.handle({ ...empty, image: jpeg(3), grid: grid(40, 18) });
        await delay(260);
        assert.equal(checks.length, 0);
      }
      const readyAt = Date.now();
      await runtime.handle(object);
      await delay(260);
      await runtime.handle(object);
      await until(() => runtime.scenes.snapshot().subject === '吐司挂件');
      assert.deepEqual(checks, [[object.image, object.image]]);
      assert.ok(Date.now() - readyAt < 1500, 'a settled object must not wait for the initial fallback');
    } finally { await runtime.close(); await provider.close(); }
  });
}

test('guide uses newest pair, never overlaps evaluations, and rejects pre-selection results on a reused connection', () => {
  let now = 10000, view = 0, inner, accepted = 0;
  class Monitor {
    frames = []; evaluations = [];
    constructor(options, callbacks) { inner = this; this.callbacks = callbacks; }
    start() {} close() {} resetPendingCapture() { this.frames = []; }
    feedImage(image, at) { this.frames.push({image,at}); return true; }
    requestEvaluation() {
      this.evaluations.push(this.frames.map(f=>f.image));
      this.callbacks.onDebug('proactive.monitor_chunk_prepared', {chunkEndAt:now,imageFrames:2});
      return true;
    }
    finish() { this.callbacks.onResult({summary:'ok'}); }
  }
  const m = createEvidenceMonitor(Monitor,{modalities:['vision']},{onResult(){}}, {
    now:()=>now, intervalMs:2500, protocol:{view:()=>view,context:()=>({}),awaitingSubject:()=>true,
      validate:()=>({state:'matched'}),accept:()=>++accepted} });
  m.feedImage('a',now); now+=250;m.feedImage('b',now); assert.ok(m.requestEvaluation());
  view++;now+=250;m.feedImage('c',now);now+=250;m.feedImage('d',now);
  assert.equal(m.requestEvaluation(),false);inner.finish();assert.equal(accepted,0);
  now+=250;m.feedImage('e',now);assert.ok(m.requestEvaluation());
  assert.deepEqual(inner.evaluations,[['a','b'],['d','e']]);inner.finish();assert.equal(accepted,1);
  assert.equal(m.requestEvaluation(),false);m.close();
});

test('real official core reuses short guide pairs for one-time tap correction; other observers retain normal feed', async () => {
  let selecting = false, moved = false, replaced = false, replacementChecks = 0;
  const provider = await fakeProvider({monitorReply(event) {
    const ctx=JSON.parse(event.response.instructions);
    if (!ctx.focus) return 'Reply: '+JSON.stringify({clip:ctx.clip,state:'unknown'});
    if (moved && !ctx.target) {
      assert.equal(ctx.subject, '');
      assert.deepEqual(ctx.alreadySpoken, []);
    }
    selecting=Boolean(ctx.selection?.point);
    if (replaced && !moved && ctx.target?.subject === '点选物品') replacementChecks++;
    const subject = moved ? '下一件物品' : replaced ? '手动换上的物品' : selecting ? '点选物品' : '主体';
    return 'Reply: '+JSON.stringify({clip:ctx.clip,state:'matched',subject,
      relation:'new',previous:moved?'background':'absent',boxes:[[200,150,800,850],[200,150,800,850]],
      previousFrames: replaced && !moved ? ['absent', 'absent'] : undefined,
      evidence:[{frame:1,detail:'清晰物品'},{frame:2,detail:'相同物品'}],summary:'主体的一个特点'});
  }});
  const config={callId:'fast-guide',apiKey:'fixture',model:'qwen3.8-omni-flash-realtime',voice:'Tina',
    maxGenerations:20,maxMonitorGenerations:10,proactiveEnabled:true};
  const events=[];
  const rt=await createRuntime(config,e=>(events.push(e),true),{gateway:{upstreamUrl:provider.url}});
  const frame={type:'image',callId:config.callId,epoch:1,image:Buffer.from([255,216,255,217]).toString('base64'),
    grid:Buffer.alloc(288,20).toString('base64'),width:10,height:10};
  const pair=async()=>{await rt.handle(frame);await new Promise(r=>setTimeout(r,260));await rt.handle(frame);};
  try {
    await rt.start();
    rt.scenes.start({mode:'guide',goal:'持续介绍',focus:'物品',depth:'brief',steps:[]});
    const scheduler=rt.session.active.proactive;
    const auxiliary=scheduler.createPerceptionMonitor({title:'辅助提醒',modalities:['vision'],repeat:true,condition:'出现水杯',triggerResponse:'喝水'});
    await until(()=>provider.peers.length===3 && provider.peers[1].session);
    assert.ok(events.some(e=>e.type==='capture_burst' && e.enabled));
    const started=Date.now();await pair();await until(()=>rt.scenes.target.status==='locked');
    assert.ok(Date.now()-started<1000,'two fresh burst frames must not wait for the 1 FPS scheduler');
    assert.equal(scheduler.evidence.get(auxiliary.taskId).vision.length,1,'burst must not shorten auxiliary evidence spacing');
    const connections=rt.gateway.stats.connections, child=[...rt.scenes.children.keys()][0];
    const s=rt.scenes.snapshot();
    await rt.handle({type:'scene_control',callId:config.callId,epoch:1,id:s.id,revision:s.revision,
      action:'target',view:s.target.view,point:{x:.5,y:.5}});
    assert.ok(rt.scenes.current(child));
    assert.equal(rt.session.active.injector.quietGapMs,150);
    await pair();await until(()=>rt.scenes.target.status==='locked');
    assert.ok(selecting);assert.equal(rt.gateway.stats.connections,connections);
    assert.equal(rt.scenes.snapshot().subject, '点选物品');
    const view = rt.scenes.snapshot().target.view;
    replaced = true;
    for (let i = 0; i < 18 && rt.scenes.snapshot().subject !== '手动换上的物品'; i++) await pair();
    assert.equal(rt.scenes.snapshot().subject, '手动换上的物品');
    assert.equal(replacementChecks, 1);
    assert.equal(rt.scenes.snapshot().target.view, view, 'replacement should work with the same camera view');
    assert.equal(rt.scenes.snapshot().moving, false);
    const movedFrame = { ...frame, grid: Buffer.alloc(288, 180).toString('base64') };
    moved = true;
    await rt.handle(movedFrame);
    assert.equal(rt.scenes.snapshot().moving, true);
    assert.equal(events.filter(e=>e.type==='capture_burst').at(-1).enabled, true);
    const cutAt = Date.now();
    for (let i = 0; i < 26 && rt.scenes.snapshot().subject !== '下一件物品'; i++) {
      await rt.handle(movedFrame);
      await new Promise(r=>setTimeout(r,260));
    }
    assert.equal(rt.scenes.snapshot().subject,'下一件物品');
    assert.ok(Date.now() - cutAt < 1500, 'a settled cut should use the short capture window');
    assert.equal(rt.scenes.target.preferred, false);
    assert.equal(rt.scenes.snapshot().status,'watching');
    assert.ok(rt.scenes.current(child)); assert.equal(rt.gateway.stats.connections,connections);
    rt.scenes.control({action:'pause',revision:rt.scenes.state.revision});
    assert.equal(rt.session.active.injector.quietGapMs,800);
    assert.equal(events.filter(e=>e.type==='capture_burst').at(-1).enabled,false);
  } finally {await rt.close();await provider.close();}
});
