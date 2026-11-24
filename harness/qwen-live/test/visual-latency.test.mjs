import test from 'node:test';
import assert from 'node:assert/strict';
import { GuideMotion } from '../src/guide-motion.mjs';
import { StepChange } from '../src/step-change.mjs';
import { createRuntime } from '../src/runtime.mjs';
import { fakeProvider, until } from './fake-provider.mjs';
import { setTimeout as delay } from 'node:timers/promises';

const grid = (value, count = 96) => {
  const bytes = Buffer.alloc(288, 20);
  bytes.fill(value, 0, count * 3);
  return bytes.toString('base64');
};

test('guide tolerates local movement and requires 250 ms since the last broad change', () => {
  let clock = 10000;
  const motion = new GuideMotion(() => clock);
  motion.sample(grid(20));
  assert.equal(motion.sample(grid(180, 5)), undefined);
  assert.equal(motion.sample(grid(180)), 'moving');
  clock += 100;
  assert.equal(motion.sample(grid(180)), undefined);
  clock += 150;
  assert.equal(motion.sample(grid(180)), 'stable');
  assert.equal(motion.sample(grid(20)), 'moving');
});

test('step changes settle before requesting an early check and ignore global exposure', () => {
  const changes = new StepChange();
  changes.sample(grid(20), 0);
  changes.sample(grid(60), 1000);
  changes.sample(grid(60), 2000);
  assert.equal(changes.readyAt, undefined);
  changes.consume();
  changes.sample(grid(180, 4), 3000);
  assert.equal(changes.readyAt, undefined);
  changes.sample(grid(180, 4), 4000);
  assert.equal(changes.readyAt, 4000);
  changes.consume();
  changes.sample(grid(180, 4), 5000);
  assert.equal(changes.readyAt, undefined);
});

test('runtime teaching uses changed frames for an early check and advances after two positive clips', async () => {
  const requests = [], events = [];
  const provider = await fakeProvider({ monitorReply(event) {
    const context = JSON.parse(event.response.instructions);
    requests.push({at:Date.now(), clip:context.clip});
    return 'Reply: ' + JSON.stringify({clip:context.clip, state:requests.length === 1 ? 'unknown' : 'matched', basis:'state',
      evidence:[{frame:1,detail:'闭合圆形轮廓'},{frame:2,detail:'完整闭合圆形'}]});
  }});
  const identity = {callId:'step-change-test', epoch:1};
  const runtime = await createRuntime({...identity, apiKey:'fixture', model:'qwen3.8-omni-flash-realtime', voice:'Tina',
    maxGenerations:20, maxMonitorGenerations:10, proactiveEnabled:true}, event => { events.push(event); return true; },
    {gateway:{upstreamUrl:provider.url}});
  try {
    await runtime.start();
    runtime.scenes.start({mode:'teach', goal:'画圆', focus:'纸上的圆', depth:'brief', steps:[
      {instruction:'画一个圆',completion:'出现闭合圆形'}, {instruction:'圆内画点',completion:'圆内出现黑点'},
    ]});
    await until(() => events.some(event => event.type === 'audio_end'));
    const end = events.find(event => event.type === 'audio_end');
    await runtime.handle({...identity,type:'playback_started',outputId:end.outputId});
    await runtime.handle({...identity,type:'playback_completed',outputId:end.outputId,sequence:end.sequence});
    await until(() => runtime.scenes.snapshot().status === 'waiting_step');
    await until(() => events.some(event => event.type === 'intent' && event.status === 'running'));
    const frame = {...identity,type:'image',image:Buffer.from([255,216,255,217]).toString('base64'),width:10,height:10};
    await runtime.handle({...frame,grid:grid(20)});
    await delay(1050);
    await runtime.handle({...frame,grid:grid(20)});
    await until(() => requests.length === 1);
    for (let i = 0; i < 6 && runtime.scenes.snapshot().completedSteps === 0; i++) {
      await delay(1050);
      await runtime.handle({...frame,grid:grid(180,4)});
    }
    await until(() => runtime.scenes.snapshot().completedSteps === 1);
    assert.equal(requests.length, 3);
    assert.ok(requests[1].at - requests[0].at < 3500, 'changed, settled frames should bypass the 4 second retry');
    assert.deepEqual(requests.map(request => request.clip), [1,2,3]);
  } finally { await runtime.close(); await provider.close(); }
});
