import test from 'node:test';
import assert from 'node:assert/strict';
import { WebSocket } from 'ws';
import { createRuntime, displayTasks } from '../src/runtime.mjs';
import { createGateway, providerRoute } from '../src/gateway.mjs';
import { LiveKitHost } from '../src/host.mjs';
import { fakeProvider, until } from './fake-provider.mjs';

const config = { callId: 'fixture-call', epoch: 1, apiKey: 'fixture-provider-key',
  model: 'qwen3.8-omni-flash-realtime', voice: 'Tina', maxGenerations: 5, maxMonitorGenerations: 2 };

test('untyped Bailian model access denial becomes an explicit failure without forwarding its body', async () => {
  const provider=await fakeProvider(), events=[];
  const runtime=await createRuntime(config,e=>(events.push(e),true),{gateway:{upstreamUrl:provider.url}});
  try {
    await runtime.start();
    provider.send(provider.peers[0],{code:'AccessDenied.Unpurchased',message:'private-provider-body'});
    await until(()=>events.some(e=>e.type==='fatal' && e.code==='model_access_denied' && e.status===403));
    assert.ok(!JSON.stringify(events).includes('private-provider-body'));
    assert.equal(runtime.gateway.stats.generations,0);
  } finally {await runtime.close();await provider.close();}
});

test('Aliyun routing uses workspace endpoint and excludes the yibuapi header', () => {
  const route = providerRoute({ provider: 'aliyun', apiKey: 'aliyun-fixture',
    endpoint: 'wss://fixture.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime' });
  assert.deepEqual(route.headers, { Authorization: 'Bearer aliyun-fixture' });
  assert.match(route.endpoint, /fixture.cn-beijing/);
  for (const endpoint of ['wss://yibuapi.com/v1/realtime', 'wss://evil.example/api-ws/v1/realtime',
    'wss://fixture.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?token=x']) {
    assert.throws(() => providerRoute({ provider: 'aliyun', endpoint, apiKey: 'fixture' }));
  }
});

test('official core can run on Aliyun routing with only the selected provider key', async () => {
  const provider = await fakeProvider();
  const runtime = await createRuntime({ ...config, provider: 'aliyun', apiKey: 'aliyun-fixture',
    endpoint: 'wss://fixture.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime' }, () => true,
    { gateway: { upstreamUrl: provider.url } });
  try {
    await runtime.start();
    assert.equal(provider.peers[0].headers.authorization, 'Bearer aliyun-fixture');
    assert.equal(provider.peers[0].headers['openai-beta'], undefined);
  } finally { await runtime.close(); await provider.close(); }
});

test('multibyte task summaries fit a reliable LiveKit packet', () => {
  const snapshot = displayTasks({ revision: 1, tasks: Array.from({ length: 32 }, (_value, i) => ({
    id: `task-${i}`, title: '观'.repeat(160), activity: '察'.repeat(300), output: '物'.repeat(500), status: 'monitoring' })) });
  assert.ok(Buffer.byteLength(JSON.stringify(snapshot), 'utf8') < 12000);
  assert.ok(snapshot.tasks.length > 0);
  assert.equal(snapshot.omitted, 32 - snapshot.tasks.length);
});

test('actual official core: thin turn scheduling, PCM, identified playback and subsequent turn', async () => {
  const provider = await fakeProvider(), events = [];
  const runtime = await createRuntime(config, (event) => (events.push(event), true), { gateway: { upstreamUrl: provider.url } });
  try {
    await runtime.start();
    assert.ok(events.find((event) => event.type === 'ready' && event.engine === 'qwen-live-harness'));
    const peer = provider.peers[0];
    assert.equal(peer.url, '/v1/realtime?model=qwen3.8-omni-flash-realtime');
    assert.equal(peer.headers.authorization, 'Bearer fixture-provider-key');
    assert.equal(peer.headers['openai-beta'], 'realtime=v1');
    assert.deepEqual(peer.session.turn_detection, { type: 'semantic_vad', create_response: false, interrupt_response: true });
    assert.equal(peer.session.max_response_output_tokens, 512);
    assert.equal(peer.responses, 0);
    await runtime.handle({ type: 'audio', callId: config.callId, epoch: 1, audio: Buffer.alloc(640).toString('base64') });
    await until(() => peer.audio > 0);
    provider.turn(peer);
    await until(() => events.some((event) => event.type === 'audio_end'));
    const audio = events.find((event) => event.type === 'audio');
    const end = events.find((event) => event.type === 'audio_end');
    assert.equal(Buffer.from(audio.audio, 'base64').length, 4800);
    assert.equal(peer.responses, 1);
    assert.equal(runtime.host.outputs.size, 1); // response.done never acknowledges playback.
    await runtime.handle({ type: 'playback_completed', callId: config.callId, epoch: 1, outputId: audio.outputId, sequence: end.sequence });
    assert.equal(runtime.host.outputs.size, 1);
    await runtime.handle({ type: 'playback_started', callId: config.callId, epoch: 1, outputId: audio.outputId });
    await runtime.handle({ type: 'playback_completed', callId: config.callId, epoch: 1, outputId: audio.outputId, sequence: end.sequence });
    assert.equal(runtime.host.outputs.size, 0);
    provider.turn(peer, { text: '第二个问题' });
    await until(() => peer.responses === 2);
    assert.equal(events.filter((event) => event.type === 'transcript' && event.role === 'user').length, 2);
    assert.equal(provider.peers.length, 1); // No Monitor connection without a task.
  } finally { await runtime.close(); await provider.close(); }
});

test('actual official tool lifecycle creates a monitor and UI stop cancels it', async () => {
  const provider = await fakeProvider(), events = [];
  const runtime = await createRuntime({ ...config, proactiveEnabled: true },
    (event) => (events.push(event), true), { gateway: { upstreamUrl: provider.url } });
  try {
    await runtime.start();
    const peer = provider.peers[0];
    assert.ok(peer.session.tools.some((tool) => tool.function?.name === 'create_proactive_monitor'));
    assert.match(peer.session.instructions, /repeat=true/);
    provider.turn(peer, { text: '看到钥匙时提醒我', tool: { name: 'create_proactive_monitor',
      arguments: JSON.stringify({ title: '钥匙观察', modalities: ['vision'], condition: '看到钥匙', trigger_response: '钥匙出现了', repeat: false }) } });
    await until(() => events.some((event) => event.type === 'tasks' && event.tasks.length));
    const task = events.filter((event) => event.type === 'tasks' && event.tasks.length).at(-1).tasks[0];
    await until(() => provider.peers.length === 2);
    assert.equal(provider.peers[1].session.modalities[0], 'text');
    assert.equal(provider.peers[1].responses, 0); // No frames, no observation generation.
    await until(() => events.some(e => e.type === 'intent' && e.status === 'running'));
    assert.equal(events.filter(e => e.type === 'tasks' && e.tasks.length).at(-1).tasks[0].canStop, true);
    const old = events.filter(e => e.type === 'intent').at(-1);
    await runtime.handle({ type: 'task_repeat', callId: config.callId, epoch: 1, taskId: task.id,
      generation: old.generation, repeat: true, requestId: 'repeat-1' });
    await until(() => events.some(e => e.type === 'intent' && e.repeat && e.status === 'running'));
    await runtime.handle({ type: 'task_repeat', callId: config.callId, epoch: 1, taskId: task.id,
      generation: old.generation, repeat: false, requestId: 'stale-update' });
    assert.equal(events.find(e => e.type === 'control_result' && e.requestId === 'stale-update').ok, false);
    await runtime.handle({ type: 'task_stop', callId: config.callId, epoch: 1, taskId: task.id, requestId: 'stop-1' });
    await until(() => events.some((event) => event.type === 'tasks' && event.tasks.some((row) => row.id === task.id && row.status === 'cancelled')));
  } finally { await runtime.close(); await provider.close(); }
});

test('gateway rejects unauthenticated clients and enforces call-wide budgets across connections', async () => {
  const provider = await fakeProvider(), limits = [];
  const gateway = await createGateway({ ...config, maxGenerations: 1 }, { upstreamUrl: provider.url, onLimit: (reason) => limits.push(reason) });
  let client;
  try {
    const denied = new WebSocket(gateway.endpoint);
    denied.on('error', () => {});
    await new Promise((resolve) => denied.once('unexpected-response', (_req, response) => { assert.equal(response.statusCode, 401); response.resume(); denied.terminate(); resolve(); }));
    client = new WebSocket(gateway.endpoint, { headers: { Authorization: `Bearer ${gateway.token}` } });
    client.on('error', () => {});
    await new Promise((resolve) => client.once('open', resolve));
    client.send(JSON.stringify({ type: 'session.update', session: { modalities: ['text'], max_response_output_tokens: 99999 } }));
    client.send(JSON.stringify({ type: 'response.create' }));
    await until(() => provider.peers[0]?.responses === 1);
    assert.equal(provider.peers[0].session.max_response_output_tokens, 256);
    client.send(JSON.stringify({ type: 'response.create' }));
    await until(() => limits.length === 1);
    assert.equal(gateway.stats.generations, 1);
    assert.equal(limits[0], 'bridge_generation_budget');
  } finally { client?.terminate(); await gateway.close(); await provider.close(); }
});

test('Host rejects old call/output receipts and an interrupted end cannot become delivered', () => {
  const events = [], calls = [];
  const host = new LiveKitHost((event) => (events.push(event), true), config);
  const session = { playbackStarted: () => calls.push('started'), playbackCompleted: () => calls.push('completed') };
  host.sendOutputAudio(1, Buffer.alloc(9600)); host.finishOutputAudio(1);
  assert.deepEqual(events.filter((event) => event.type === 'audio').map((event) => event.sequence), [0, 1]);
  assert.equal(host.receipt({ type: 'playback_started', callId: 'old', epoch: 1, outputId: 1 }, session), false);
  host.clearOutput(1);
  assert.equal(host.receipt({ type: 'playback_completed', callId: config.callId, epoch: 1, outputId: 1, sequence: 2 }, session), false);
  assert.deepEqual(calls, []);
});

test('an older output receipt cannot advance a newer official output', () => {
  const host = new LiveKitHost(() => true, config), calls = [];
  const session = { playbackStarted: () => calls.push('started'), playbackCompleted: () => calls.push('completed') };
  host.sendOutputAudio(1, Buffer.alloc(4800)); host.finishOutputAudio(1);
  host.sendOutputAudio(1, Buffer.alloc(4800)); host.finishOutputAudio(1);
  host.receipt({ type: 'playback_started', callId: config.callId, epoch: 1, outputId: 1 }, session);
  host.receipt({ type: 'playback_completed', callId: config.callId, epoch: 1, outputId: 1, sequence: 1 }, session);
  assert.deepEqual(calls, []);
  host.receipt({ type: 'playback_started', callId: config.callId, epoch: 1, outputId: 2 }, session);
  host.receipt({ type: 'playback_completed', callId: config.callId, epoch: 1, outputId: 2, sequence: 1 }, session);
  assert.deepEqual(calls, ['started', 'completed']);
});

test('Monitor generation limit leaves the foreground connection available', async () => {
  const provider = await fakeProvider(), diagnostics = [];
  const gateway = await createGateway({ ...config, maxMonitorGenerations: 1 },
    { upstreamUrl: provider.url, onDiagnostic: (event) => diagnostics.push(event) });
  const sockets = [];
  async function connect(modalities) {
    const socket = new WebSocket(gateway.endpoint, { headers: { Authorization: `Bearer ${gateway.token}` } });
    sockets.push(socket); socket.on('error', () => {});
    await new Promise((resolve) => socket.once('open', resolve));
    socket.send(JSON.stringify({ type: 'session.update', session: { modalities } }));
    return socket;
  }
  try {
    const foreground = await connect(['text', 'audio']);
    const monitor = await connect(['text']);
    monitor.send(JSON.stringify({ type: 'response.create' }));
    await until(() => gateway.stats.monitorGenerations === 1);
    monitor.send(JSON.stringify({ type: 'response.create' }));
    await until(() => diagnostics.some((event) => event.code === 'monitor_generation_budget'));
    foreground.send(JSON.stringify({ type: 'response.create' }));
    await until(() => provider.peers[0].responses === 1);
    assert.equal(foreground.readyState, WebSocket.OPEN);
    assert.equal(gateway.stats.generations, 2);
  } finally { for (const socket of sockets) socket.terminate(); await gateway.close(); await provider.close(); }
});

test('actual core routes grounded observation to speech and completes once only after browser acknowledgement', async () => {
  const provider = await fakeProvider({ monitorReply(event) {
    return 'Reply: ' + JSON.stringify({ clip: JSON.parse(event.response.instructions).clip,
      state: 'matched', basis: 'state', evidence: [{ frame: 1, detail: '桌面左侧白色水杯' }, { frame: 2, detail: '白色水杯仍在桌面左侧' }] });
  } });
  const events = [];
  const runtime = await createRuntime({ ...config, proactiveEnabled: true },
    event => (events.push(event), true), { gateway: { upstreamUrl: provider.url } });
  const acknowledge = async end => {
    await runtime.handle({ type: 'playback_started', callId: config.callId, epoch: 1, outputId: end.outputId });
    await runtime.handle({ type: 'playback_completed', callId: config.callId, epoch: 1, outputId: end.outputId, sequence: end.sequence });
  };
  try {
    await runtime.start();
    provider.turn(provider.peers[0], { text: '看到水杯提醒我', tool: { name: 'create_proactive_monitor',
      arguments: JSON.stringify({ title: '水杯提醒', modalities: ['vision'], condition: '[state] 水杯出现', trigger_response: '喝水', repeat: false }) } });
    await until(() => events.some(e => e.type === 'audio_end') && events.some(e => e.type === 'intent' && e.status === 'running'));
    await acknowledge(events.find(e => e.type === 'audio_end'));
    await runtime.handle({ type: 'audio', callId: config.callId, epoch: 1, audio: Buffer.alloc(640).toString('base64') });
    const frame = { type: 'image', callId: config.callId, epoch: 1, width: 10, height: 10,
      image: Buffer.from([255, 216, 255, 217]).toString('base64') };
    await runtime.handle(frame);
    await new Promise(resolve => setTimeout(resolve, 1050));
    await runtime.handle(frame);
    try { await until(() => events.filter(e => e.type === 'audio_end').length === 2); }
    catch (error) { throw new Error(JSON.stringify({ intents: events.filter(e => e.type === 'intent'),
      peers: provider.peers.map(p => ({ responses: p.responses, images: p.images })),
      events: events.filter(e => ['fatal', 'diagnostic', 'tool', 'audio_end', 'playback'].includes(e.type)),
      responses: provider.requests.filter(e => e.type === 'response.create') }), { cause: error }); }
    assert.ok(events.some(e => e.type === 'intent' && e.status === 'delivering' && e.triggerCount === 1));
    assert.ok(!events.some(e => e.type === 'intent' && e.deliveredCount === 1));
    await acknowledge(events.filter(e => e.type === 'audio_end').at(-1));
    await until(() => events.some(e => e.type === 'intent' && e.status === 'completed' && e.deliveredCount === 1));
  } finally { await runtime.close(); await provider.close(); }
});
