import test from 'node:test';
import assert from 'node:assert/strict';
import { createOfficialAudio } from '../src/official-audio.js';

function fakeContext() {
  return { currentTime: 0, state: 'running', sources: [], destination: {},
    async resume() { this.state = 'running'; }, async close() { this.state = 'closed'; },
    createBuffer(_channels, count, rate) { return { duration: count / rate, getChannelData: () => new Float32Array(count) }; },
    createBufferSource() {
      const source = { buffer: undefined, onended: undefined, connect() {}, disconnect() {},
        start(at) { this.at = at; }, stop() { this.stopped = true; } };
      this.sources.push(source); return source;
    } };
}
const event = (type, extra = {}) => ({ type, callId: 'call', epoch: 1, outputId: 1, sequence: 0, ...extra });

test('only a running browser sink with matching end marker and drained sources acknowledges completion', async () => {
  const context = fakeContext(), receipts = [];
  const audio = createOfficialAudio((value) => receipts.push(value), { contextFactory: () => context });
  try {
    await audio.prepare(); audio.activate('call');
    audio.handle(event('audio', { audio: Buffer.alloc(4800).toString('base64') }));
    audio.handle(event('audio_end', { sequence: 1 }));
    assert.equal(receipts.length, 0);
    context.currentTime = .2; context.sources[0].onended();
    assert.deepEqual(receipts.map((value) => value.type), ['playback_started', 'playback_completed']);
    assert.equal(receipts[1].sequence, 1);
    audio.handle(event('audio_end', { sequence: 1 }));
    assert.equal(receipts.length, 2);
  } finally { await audio.reset(); }
});

test('suspension, stale call and output clearing never acknowledge completion', async () => {
  const context = fakeContext(), receipts = [];
  const audio = createOfficialAudio((value) => receipts.push(value), { contextFactory: () => context });
  try {
    await audio.prepare(); audio.activate('call');
    const packet = event('audio', { audio: Buffer.alloc(4800).toString('base64') });
    audio.handle({ ...packet, callId: 'old-call' });
    assert.equal(context.sources.length, 0);
    audio.handle(packet);
    audio.handle(event('audio_clear', { throughId: 1 }));
    context.currentTime = .2;
    audio.handle(event('audio_end', { sequence: 1 }));
    assert.equal(receipts.length, 0);
    audio.handle({ ...packet, outputId: 2 });
    context.state = 'suspended'; context.onstatechange();
    assert.deepEqual(receipts.map((value) => value.type), ['playback_blocked']);
    audio.handle(event('audio_end', { outputId: 2, sequence: 1 }));
    assert.equal(receipts.length, 1);
  } finally { await audio.reset(); }
});

test('missing sequence fails closed and does not re-create an invalidated output', async () => {
  const context = fakeContext(), receipts = [];
  const audio = createOfficialAudio((value) => receipts.push(value), { contextFactory: () => context });
  try {
    await audio.prepare(); audio.activate('call');
    audio.handle(event('audio', { sequence: 3, audio: Buffer.alloc(4800).toString('base64') }));
    audio.handle(event('audio', { audio: Buffer.alloc(4800).toString('base64') }));
    assert.equal(context.sources.length, 0);
    assert.deepEqual(receipts.map((value) => value.type), ['playback_blocked']);
  } finally { await audio.reset(); }
});
