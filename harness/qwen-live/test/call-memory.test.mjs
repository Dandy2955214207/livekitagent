import test from 'node:test';
import assert from 'node:assert/strict';
import { CallMemory } from '../src/call-memory.mjs';
import { createRuntime } from '../src/runtime.mjs';
import { fakeProvider, until } from './fake-provider.mjs';

const event = (frame, text = frame.image) => ({ first: frame.id, last: frame.id, text, certainty: 'observed' });

function fixture(observer, trace) {
  let now = 0;
  const statuses = [];
  const memory = new CallMemory({ close() {}, ...observer }, { now: () => now, trace, onStatus: status => statuses.push(status) });
  return { memory, statuses, add(count, name = index => '帧' + index) {
    for (let i = 0; i < count; i++) {
      now += 1000;
      memory.feed(name(memory.sequence + 1));
    }
  }, advance(ms) { now += ms; memory.maintainWindow(); } };
}

test('the full window overlaps sealed 1–10 and 11–20 batches without resubmitting frames', async () => {
  const batches = [], releases = [];
  const { memory, add } = fixture({ observe(frames) {
    batches.push(frames.map(frame => frame.id));
    return new Promise(resolve => releases.push(resolve));
  } });
  try {
    add(29);
    assert.equal(batches.length, 0);
    add(1);
    assert.deepEqual(batches[0], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    assert.equal(memory.frames[0].id, 1);
    add(5);
    assert.equal(memory.frames[0].id, 6);
    assert.equal(memory.frames.at(-1).id, 35);
    releases[0]([]);
    await until(() => !memory.activeBatch);
    add(5);
    assert.deepEqual(batches[1], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]);
    assert.equal(memory.frames[0].id, 11);
    assert.equal(memory.frames.length, 30);
    releases[1]([]);
    await until(() => !memory.activeBatch);
  } finally { memory.close(); }
});

test('trace distinguishes a pending summary from an archived record excluded or missed by a query', async () => {
  const trace = [];
  const collect = (kind, data) => trace.push({ kind, data });
  let finish;
  const { memory, add, advance } = fixture({
    observe: frames => new Promise(resolve => { finish = () => resolve([event(frames[0], '红杯')]); }),
  }, collect);
  try {
    add(3);
    advance(31000);
    memory.captureQuestion('pending');
    const pending = trace.find(row => row.kind === 'memory.question_snapshot').data;
    assert.deepEqual(pending.pendingFrameIds, [1, 2, 3]);
    assert.deepEqual(pending.availableIds, []);
    finish();
    await until(() => !memory.activeBatch);
    const archived = trace.find(row => row.kind === 'memory.summary_written').data.records[0];
    assert.equal(archived.text, '红杯');
    assert.equal(archived.firstFrame, 1);
    memory.captureQuestion('no-match');
    const miss = await memory.query({ query: '蓝笔' }, { turnId: 'no-match', traceQuery: collect });
    assert.equal(miss.status, 'no_matching_history');
    const search = () => trace.filter(row => row.kind === 'memory.text_search').at(-1).data;
    assert.deepEqual(search().stored, [archived.id]);
    assert.deepEqual(search().eligible, [archived.id]);
    assert.deepEqual(search().ranked, []);
    await memory.query({ query: '红杯', startSeconds: 10 }, { turnId: 'no-match', traceQuery: collect });
    assert.deepEqual(search().eligible, []);
    const hit = await memory.query({ query: '红杯' }, { turnId: 'no-match', traceQuery: collect });
    assert.equal(hit.records[0].text, '红杯');
    assert.deepEqual(search().selected, [archived.id]);
  } finally { memory.close(); }
});

test('recall sends all 30 question-time frames once while the same frames are being summarized', async () => {
  let reviewed, finishSummary;
  const { memory, add } = fixture({
    observe: () => new Promise(resolve => { finishSummary = resolve; }),
    async review(frames, task) { reviewed = { frames, task }; return [event(frames[6], '黑笔')]; },
  });
  try {
    add(30);
    memory.captureQuestion();
    add(4);
    const result = await memory.query({ question: '除了鼠标刚才还展示了什么？', period: 'recent' });
    assert.equal(reviewed.frames.length, 30);
    assert.equal(reviewed.frames[0].id, 1);
    assert.equal(reviewed.frames.at(-1).id, 30);
    assert.equal(reviewed.task.endSeconds, 30);
    assert.equal(memory.frames.at(-1).id, 34);
    assert.equal(result.pending, true);
    assert.equal(result.reviewStatus, 'observed');
    assert.deepEqual(result.records.map(row => [row.text, row.source, row.firstFrame]), [['黑笔', 'window', 7]]);
    finishSummary([]);
    await until(() => !memory.activeBatch);
  } finally { memory.close(); }
});

test('expired images are released, partial batches archive once, and text retrieves synonyms', async () => {
  const calls = [];
  const { memory, add, advance } = fixture({
    async observe(frames) { calls.push(frames.map(frame => frame.id)); return frames.map(frame => event(frame)); },
    async embed(texts, type) {
      return texts.map(text => type === 'query' || text.includes('签字笔') ? [1, 0] : [0, 1]);
    },
  });
  try {
    add(12, index => index === 4 ? '手持黑色签字笔' : '白色鼠标');
    memory.cameraCleared();
    advance(31000);
    await until(() => !memory.activeBatch && !memory.indexing);
    assert.deepEqual(calls.map(batch => batch.length), [10, 2]);
    assert.equal(memory.frames.length, 0);
    const result = await memory.query({ query: '写字用的东西', period: 'beginning' });
    assert.equal(result.search, 'hybrid');
    assert.equal(result.frames, 0);
    assert.deepEqual(result.records.map(row => row.text), ['手持黑色签字笔']);
    assert.equal('vector' in result.records[0], false);
  } finally { memory.close(); }
});

test('mixed recall merges older text with the current window and falls back when review fails', async () => {
  let fail = false;
  const { memory, add } = fixture({
    async observe(frames) { return [event(frames[0], '先展示白鼠标')]; },
    async review(frames) {
      if (fail) throw new Error('memory_http_429');
      return [event(frames.at(-1), '随后展示黑笔')];
    },
  });
  try {
    add(40);
    await until(() => !memory.activeBatch);
    const result = await memory.query({ period: 'all' });
    assert.deepEqual(result.records.map(row => row.source), ['summary', 'window']);
    fail = true;
    const fallback = await memory.query({ period: 'recent', query: '白鼠标' });
    assert.equal(fallback.reviewStatus, 'memory_http_429');
    assert.ok(fallback.records.every(row => row.source === 'summary'));
    assert.ok(fallback.records.length > 0);
  } finally { memory.close(); }
});

test('recent visual recall includes summaries before the frame window within the requested minute', async () => {
  const { memory, add } = fixture({
    async observe(frames) { return frames.filter(frame => frame.id === 39 || frame.id === 54 || frame.id === 75).map(frame => event(frame)); },
    async review(frames) { return [event(frames.find(frame => frame.id === 75))]; },
  });
  try {
    const items = new Map([[39, '更早的水杯'], [54, '白色罗技鼠标'], [75, '黑色键盘']]);
    for (let i = 0; i < 10; i++) {
      add(10, id => items.get(id) ?? '桌面');
      await until(() => !memory.activeBatch);
    }
    const result = await memory.query({ question: '刚才依次展示了哪些物品？', period: 'recent', order: 'chronological' });
    assert.deepEqual(result.range, { start: 40, end: 100, anchor: undefined });
    assert.deepEqual(result.records.map(row => [row.source, row.text]), [
      ['summary', '白色罗技鼠标'], ['window', '黑色键盘'],
    ]);
    assert.equal(result.frames, 30);
  } finally { memory.close(); }
});

test('slow summaries keep a bounded queue and close discards late results', async () => {
  let finish;
  const { memory, add, statuses } = fixture({ observe: () => new Promise(resolve => { finish = resolve; }) });
  add(100);
  assert.equal(memory.batches.length, 3);
  assert.equal(memory.frames.length, 30);
  assert.ok(statuses.some(status => status.code === 'observer_backlog'));
  const frame = memory.activeBatch[0];
  memory.close();
  finish([event(frame)]);
  await until(() => !memory.activeBatch);
  assert.equal(memory.records.length, 0);
  assert.equal(memory.batches.length, 0);
});

test('task scopes survive frame expiry and follow-up retrieves the same task with confirmed playback only', async () => {
  let reviews = 0;
  const { memory, add, advance } = fixture({
    async observe(frames) { return [event(frames[0], '桌面布置')]; },
    async review() { reviews++; return []; },
  });
  const t = memory.timeline;
  const guide = { id: 'guide-1', mode: 'guide', goal: '桌面物品讲解', status: 'watching' };
  const announce = (id, subject, complete) => {
    t.queue(id, { id: guide.id, kind: 'guide', label: guide.goal, subject });
    const output = { id, responseId: id };
    t.response({ responseId: id, authority: 'proactive' });
    t.linkDelivery(id, id);
    t.outputCreated(output);
    t.assistantText({ responseId: id, source: 'audio_transcript', text: subject + '的介绍' });
    t.playback(output, 'started');
    advance(1000);
    t.playback(output, complete ? 'completed' : 'interrupted');
  };
  try {
    add(3);
    t.task(guide);
    announce('first', '黑笔', true);
    announce('second', '白键盘', false);
    announce('third', '水杯', true);
    announce('fourth', '黑笔', true);
    t.queue('never-started', { id: guide.id, kind: 'guide', label: guide.goal, subject: '鼠标' });
    t.stopDelivery('never-started');
    t.task({ ...guide, status: 'paused' });
    advance(100000);
    t.task({ id: 'teach-1', mode: 'teach', status: 'waiting_step', goal: '画小猪', step: 2, completedSteps: 1 });
    t.userText('ask', '刚开始教学吗？');
    const played = await memory.query({
      scope: { type: 'task', kind: 'guide', pick: 'latest' }, sources: ['playback'], order: 'chronological',
    });
    assert.deepEqual(played.records.map(row => row.subject), ['黑笔', '水杯', '黑笔']);
    assert.ok(played.records.every(row => row.state === 'completed' && row.text.endsWith('的介绍')));
    assert.equal(played.range.end, 7);
    const interrupted = await memory.query({ scope: { type: 'previous' }, playbackState: 'interrupted' });
    assert.equal(interrupted.range.anchor.id, guide.id);
    assert.deepEqual(interrupted.records.map(row => [row.subject, row.state, row.text]), [['白键盘', 'interrupted', undefined]]);
    const discarded = await memory.query({ scope: { type: 'previous' }, playbackState: 'discarded' });
    assert.deepEqual(discarded.records.map(row => row.subject), ['鼠标']);
    const afterStart = await memory.query({
      scope: { type: 'task', kind: 'guide', relation: 'after', boundary: 'start' }, sources: ['playback'], order: 'chronological',
    });
    assert.deepEqual(afterStart.records.map(row => row.subject), ['黑笔', '水杯', '黑笔']);
    const afterInterruption = await memory.query({
      scope: { type: 'event', source: 'playback', state: 'interrupted', query: '白键盘', relation: 'after' },
      sources: ['playback'], order: 'chronological',
    });
    assert.deepEqual(afterInterruption.records.map(row => row.subject), ['水杯', '黑笔']);
    const lesson = await memory.query({ scope: { type: 'task', kind: 'teach' }, sources: ['task'] });
    assert.equal(lesson.records.at(-1).completedSteps, 1);
    assert.equal(reviews, 0);
    assert.equal(memory.frames.length, 0);
    assert.equal((await memory.query({ startSeconds: 3, endSeconds: 7, sources: ['playback'] })).records.length, 3);
  } finally { memory.close(); }
  assert.equal(t.events.length, 0);
});

test('event anchors support ambiguity, time relations, and recent visual evidence without a repeated review', async () => {
  let reviews = 0;
  const { memory, add, advance } = fixture({
    async review(frames) { reviews++; return [event(frames[1], '黑笔出现在桌面')]; },
  });
  const t = memory.timeline;
  try {
    add(2);
    t.userText('a', '检查镜头');
    advance(1000);
    t.userText('b', '开始测试');
    advance(1000);
    t.userText('c', '检查镜头');
    advance(1000);
    t.userText('ask', '检查镜头以后说了什么？');
    const scope = { type: 'event', source: 'dialogue', query: '检查镜头', relation: 'after' };
    const ambiguous = await memory.query({ scope }, { turnId: 'ask' });
    assert.equal(ambiguous.status, 'ambiguous_scope');
    assert.equal(ambiguous.candidates.length, 2);
    const afterFirst = await memory.query({ scope: { ...scope, id: ambiguous.candidates[0].id }, order: 'chronological' }, { turnId: 'ask' });
    assert.deepEqual(afterFirst.records.map(row => row.turnId), ['b', 'c']);
    const visual = await memory.query({ scope: { type: 'event', source: 'vision', query: '黑笔' } });
    assert.deepEqual(visual.records.map(row => row.text), ['黑笔出现在桌面']);
    assert.equal(reviews, 1);
  } finally { memory.close(); }
});

test('a pending recall keeps its own turn boundary after the next question begins', async () => {
  const { memory, advance } = fixture({});
  try {
    memory.timeline.userText('first', '先前说的话');
    advance(1000);
    memory.captureQuestion('original');
    memory.timeline.userText('original', '此前我说过什么？', memory.questionFor('original').at);
    advance(1000);
    memory.captureQuestion('later');
    memory.timeline.userText('later', '稍后才说的话', memory.questionFor('later').at);
    const result = await memory.query({ sources: ['dialogue'] }, { turnId: 'original' });
    assert.deepEqual(result.records.map(row => row.text), ['先前说的话']);
    assert.equal(result.range.end, 1);
  } finally { memory.close(); }
});

test('official tool returns reviewed frames and keeps foreground and memory calls separate', async () => {
  const provider = await fakeProvider();
  const events = [];
  let reviewed;
  const runtime = await createRuntime({ callId: 'memory-test', apiKey: 'fixture',
    model: 'qwen3.8-omni-flash-realtime', voice: 'Tina', maxGenerations: 12,
    maxMonitorGenerations: 1, memoryEnabled: true }, e => (events.push(e), true), {
    gateway: { upstreamUrl: provider.url },
    memoryObserver: { close() {}, async review(frames) { reviewed = frames; return [event(frames[0], '黑笔')]; } },
  });
  try {
    await runtime.start();
    await runtime.handle({ type: 'image', callId: 'memory-test', epoch: 1, image: 'ZmFrZQ==', width: 1, height: 1 });
    provider.turn(provider.peers[0], { text: '刚才出现过什么？', tool: { name: 'recall_call_history',
      arguments: JSON.stringify({ query: '', period: 'recent' }) } });
    await until(() => provider.requests.some(r => r.item?.type === 'function_call_output' && r.item.output.includes('黑笔')));
    await until(() => events.some(e => e.type === 'audio_end'));
    assert.equal(reviewed.length, 1);
    assert.equal(provider.peers.length, 1);
    assert.ok(events.some(e => e.type === 'memory' && e.phase === 'query' && e.submitted && e.frames === 1));
  } finally { await runtime.close(); await provider.close(); }
});

test('misrouted and truncated recap tool calls return the latest guide playback, even before the last minute', async () => {
  const provider = await fakeProvider();
  const events = [];
  let now = 0;
  let reviews = 0;
  const runtime = await createRuntime({ callId: 'guide-recap', apiKey: 'fixture',
    model: 'qwen3.8-omni-flash-realtime', voice: 'Tina', maxGenerations: 12,
    maxMonitorGenerations: 1, memoryEnabled: true }, e => (events.push(e), true), {
    gateway: { upstreamUrl: provider.url }, memoryOptions: { now: () => now },
    memoryObserver: { close() {}, async review() { reviews++; return []; } },
  });
  const t = runtime.memory.timeline;
  try {
    await runtime.start();
    t.task({ id: 'old-guide', mode: 'guide', goal: '上一轮', status: 'watching' });
    t.append('playback', { taskId: 'old-guide', state: 'completed', text: '水杯' }, 1);
    now = 3000;
    const guide = { id: 'desk-guide', mode: 'guide', goal: '本轮', status: 'watching' };
    t.task(guide);
    for (const [end, text, state] of [[12, '白色罗技鼠标', 'completed'], [74, '黑色键盘', 'completed'],
      [85, '面包造型挂件', 'completed'], [95, '黑色笔', 'interrupted']]) {
      t.append('playback', { taskId: guide.id, state, subject: text, ...(state === 'completed' && { text }) }, end);
    }
    now = 96000;
    t.task({ id: 'timer', kind: 'timer', title: '提醒', status: 'running' });
    t.append('playback', { taskId: 'timer', state: 'completed', text: '喝水' }, 97);
    now = 102000;
    runtime.memory.feed('recent frame');
    const cases = [
      ['刚才依次介绍了哪些物品？', JSON.stringify({ question: '刚才依次展示了哪些物品',
        period: 'recent', sources: ['vision'], order: 'chronological' })],
      ['回忆一下刚才介绍了哪些物品。',
        '{"query": "介绍了哪些物品", "period": "recent", "sources": ["playback"], "order": "chronological", "playbackState": "completed", "scope": {"type":'],
    ];
    for (const [index, [text, args]] of cases.entries()) {
      provider.turn(provider.peers[0], { text, tool: { name: 'recall_call_history', arguments: args } });
      const output = () => provider.requests.filter(r => r.item?.type === 'function_call_output')[index];
      await until(() => output());
      const result = JSON.parse(output().item.output);
      assert.equal(result.range.anchor.id, 'desk-guide');
      assert.deepEqual(result.sources, ['playback']);
      assert.deepEqual(result.records.map(row => row.text), ['白色罗技鼠标', '黑色键盘', '面包造型挂件']);
      const audioEnd = () => events.filter(e => e.type === 'audio_end')[index];
      await until(() => audioEnd());
      const { outputId, sequence } = audioEnd();
      const identity = { callId: 'guide-recap', epoch: 1, outputId };
      await runtime.handle({ ...identity, type: 'playback_started' });
      await runtime.handle({ ...identity, type: 'playback_completed', sequence });
    }
    assert.equal(reviews, 0);

    const bounded = await runtime.memory.query({ question: '刚才依次介绍了哪些物品？', startSeconds: 70,
      sources: ['vision'], scope: { type: 'task', kind: 'guide', id: guide.id } });
    assert.deepEqual(bounded.records.map(row => row.text), ['黑色键盘', '面包造型挂件']);
  } finally { await runtime.close(); await provider.close(); }
});

test('other malformed history calls can retry once in the same user turn', async () => {
  for (const retryIsValid of [true, false]) {
    const outputs = [];
    const provider = await fakeProvider({ onRequest(request, peer) {
      if (request.item?.type !== 'function_call_output') return;
      const result = JSON.parse(request.item.output);
      outputs.push(result);
      if (result.retryable) {
        peer.nextTool = { name: 'recall_call_history', arguments: retryIsValid ? '{"period":"beginning","sources":["dialogue"]}' : '{"scope":' };
      }
    } });
    const runtime = await createRuntime({ callId: 'history-retry', apiKey: 'fixture',
      model: 'qwen3.8-omni-flash-realtime', voice: 'Tina', maxGenerations: 12,
      maxMonitorGenerations: 1, memoryEnabled: true }, () => true, {
      gateway: { upstreamUrl: provider.url }, memoryObserver: { close() {} },
    });
    try {
      await runtime.start();
      runtime.memory.timeline.userText('earlier', '先确认麦克风。');
      provider.turn(provider.peers[0], { text: '我最早说的话是什么？',
        tool: { name: 'recall_call_history', arguments: '{"scope":{"type":' } });
      await until(() => outputs.length >= 2);
      assert.equal(outputs[0].status, 'invalid_arguments');
      assert.equal(outputs[0].retryable, true);
      if (retryIsValid) {
        assert.equal(outputs[1].status, 'history');
        assert.deepEqual(outputs[1].records.map(row => row.text), ['先确认麦克风。']);
      } else {
        assert.equal(outputs[1].status, 'invalid_arguments');
        assert.equal(outputs[1].retryable, false);
      }
    } finally { await runtime.close(); await provider.close(); }
  }
});

test('the real runtime binds browser playback and final dialogue to the recall tool', async () => {
  const provider = await fakeProvider();
  const events = [];
  const identity = { callId: 'memory-playback', epoch: 1 };
  const runtime = await createRuntime({ ...identity, apiKey: 'fixture', model: 'qwen3.8-omni-flash-realtime', voice: 'Tina',
    maxGenerations: 20, maxMonitorGenerations: 5, memoryEnabled: true, proactiveEnabled: true },
  e => (events.push(e), true), { gateway: { upstreamUrl: provider.url }, memoryObserver: { close() {} } });
  const receipt = (type, audio) => runtime.handle({ ...identity, type, outputId: audio.outputId, sequence: audio.sequence });
  try {
    await runtime.start();
    provider.turn(provider.peers[0], { text: '检查一下麦克风' });
    await until(() => events.some(event => event.type === 'audio_end'));
    const direct = events.find(event => event.type === 'audio_end');
    await receipt('playback_started', direct);
    await receipt('playback_completed', direct);

    runtime.session.active.proactive.createTimer({ title: '喝水提醒', reminderText: '现在喝水', durationSec: .05 });
    await until(() => events.filter(event => event.type === 'audio_end').length === 2);
    const reminder = events.filter(event => event.type === 'audio_end').at(-1);
    await receipt('playback_started', reminder);
    await receipt('playback_completed', { ...reminder, sequence: reminder.sequence + 1 });
    assert.ok(!runtime.memory.timeline.events.some(row => row.state === 'completed' && row.outputId === reminder.outputId));
    await receipt('playback_completed', reminder);

    provider.turn(provider.peers[0], { text: '刚才的喝水提醒播完了吗？', tool: { name: 'recall_call_history',
      arguments: JSON.stringify({ scope: { type: 'task', kind: 'timer', pick: 'latest' }, sources: ['playback'] }) } });
    const getOutput = () => provider.requests.find(request => request.item?.type === 'function_call_output');
    await until(() => getOutput());
    const result = JSON.parse(getOutput().item.output);
    assert.equal(result.range.anchor.kind, 'timer');
    assert.equal(result.records.length, 1);
    assert.equal(result.records[0].outputId, reminder.outputId);
    assert.equal(result.records[0].state, 'completed');
    assert.equal(result.records[0].text, '本地协议测试。');
    assert.ok(result.records[0].deliveryId);
    const dialogue = await runtime.memory.query({ sources: ['dialogue'] });
    assert.ok(dialogue.records.some(row => row.role === 'user' && row.text === '检查一下麦克风'));
    assert.ok(dialogue.records.some(row => row.role === 'assistant' && row.outputId === direct.outputId));
    await until(() => events.filter(event => event.type === 'audio_end').length === 3);
    const answer = events.filter(event => event.type === 'audio_end').at(-1);
    await receipt('playback_started', answer);
    runtime.host.clearOutput(1);
    await receipt('playback_completed', answer);
    const finished = await runtime.memory.query({ sources: ['playback'] });
    assert.ok(!finished.records.some(row => row.outputId === answer.outputId));
    const interrupted = await runtime.memory.query({ sources: ['playback'], playbackState: 'interrupted' });
    // A new question snapshot is taken when the user next speaks.
    assert.ok(!interrupted.records.some(row => row.outputId === answer.outputId));
    runtime.memory.captureQuestion();
    const afterInterruption = await runtime.memory.query({ sources: ['playback'], playbackState: 'interrupted' });
    assert.ok(afterInterruption.records.some(row => row.outputId === answer.outputId && !row.text));
  } finally { await runtime.close(); await provider.close(); }
});
