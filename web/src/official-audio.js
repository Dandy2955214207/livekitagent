// PCM stays on the authenticated LiveKit connection. Only this rendering sink
// acknowledges output: provider response.done and server queues are insufficient.
export function createOfficialAudio(send, { contextFactory = () => new AudioContext(), onBlocked = () => {} } = {}) {
  let context, callId, epoch = 1, tail = 0, throughId = 0, timer;
  const outputs = new Map();
  function receipt(type, output, extra = {}) {
    void Promise.resolve(send({ type, callId, epoch, outputId: output.id, ...extra })).catch(onBlocked);
  }
  function stopSources(output) {
    for (const source of output.sources) { source.onended = null; try { source.stop(); source.disconnect(); } catch { /* Already ended. */ } }
    output.sources.clear();
  }
  function clear(id = Infinity) {
    for (const [key, output] of outputs) if (key <= id) { stopSources(output); outputs.delete(key); }
    throughId = Math.max(throughId, id === Infinity ? throughId : id);
    tail = context?.currentTime ?? 0;
  }
  function block() {
    for (const output of outputs.values()) { receipt('playback_blocked', output); throughId = Math.max(throughId, output.id); }
    clear(); onBlocked();
  }
  function check() {
    if (!context || context.state !== 'running') return;
    for (const [id, output] of outputs) {
      if (!output.started && context.currentTime >= output.start) {
        output.started = true;
        receipt('playback_started', output, { browserBufferMs: Math.max(0, (context.currentTime - output.receivedAt) * 1000) });
      }
      if (output.ended && output.started && output.sources.size === 0 && context.currentTime >= output.end) {
        outputs.delete(id);
        throughId = Math.max(throughId, id);
        receipt('playback_completed', output, { sequence: output.sequence });
      }
    }
  }
  return {
    async prepare() {
      if (!context) {
        context = contextFactory();
        context.onstatechange = () => { if (context?.state !== 'running' && outputs.size) block(); };
        timer = setInterval(check, 20);
      }
      await context.resume();
    },
    activate(id) { clear(); callId = id; throughId = 0; },
    handle(event) {
      if (!callId || event.callId !== callId || event.epoch !== epoch) return;
      if (event.type === 'audio_clear') { if (Number.isSafeInteger(event.throughId)) clear(event.throughId); return; }
      if (!Number.isSafeInteger(event.outputId) || event.outputId <= throughId) return;
      try {
        if (!context || context.state !== 'running') {
          throughId = Math.max(throughId, event.outputId);
          receipt('playback_blocked', { id: event.outputId }); onBlocked(); return;
        }
        let output = outputs.get(event.outputId);
        if (event.type === 'audio') {
          if (!output) {
            if (outputs.size >= 8) throw new Error('output_backlog');
            const start = Math.max(context.currentTime + .04, tail);
            output = { id: event.outputId, sequence: 0, start, end: start, receivedAt: context.currentTime, sources: new Set(), ended: false, started: false };
            outputs.set(output.id, output);
          }
          if (output.ended || event.sequence !== output.sequence || typeof event.audio !== 'string' || event.audio.length > 6400) throw new Error('audio_sequence');
          const bytes = Uint8Array.from(atob(event.audio), (value) => value.charCodeAt(0));
          if (!bytes.length || bytes.length % 2) throw new Error('invalid_pcm');
          const buffer = context.createBuffer(1, bytes.length / 2, 24000);
          const data = buffer.getChannelData(0), view = new DataView(bytes.buffer);
          for (let i = 0; i < data.length; i++) data[i] = view.getInt16(i * 2, true) / 32768;
          const start = Math.max(output.end, context.currentTime + .02);
          if (start + buffer.duration - context.currentTime > 20) throw new Error('output_backlog');
          const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination);
          output.sources.add(source);
          source.onended = () => { output.sources.delete(source); source.disconnect(); check(); };
          source.start(start);
          output.end = start + buffer.duration; tail = output.end; output.sequence++;
        } else if (event.type === 'audio_end' && output) {
          if (event.sequence !== output.sequence || output.ended) throw new Error('end_sequence');
          output.ended = true; check();
        }
      } catch { block(); }
    },
    async reset() {
      clear(); callId = undefined; clearInterval(timer); timer = undefined;
      if (context) { const old = context; context = undefined; old.onstatechange = null; await old.close().catch(() => {}); }
    },
  };
}
