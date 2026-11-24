import { createHash } from 'node:crypto';

export const frameKey = image => createHash('sha256').update(Buffer.from(image, 'base64')).digest('hex');

export function makeTestTrace(config, emit) {
  if (!config.testCapture) return undefined;
  let seq = 0, dropped = 0;
  return (kind, data = {}) => {
    const sent = emit({ type: 'test_trace', callId: config.callId, epoch: config.epoch ?? 1,
      seq: ++seq, at: Date.now(), kind, data: { ...data, traceDropped: dropped } });
    if (sent === false) dropped++;
  };
}

export function traceRealtime(open, host, trace) {
  return (config, callbacks) => {
    const context = event => ({ turnId: event.inputItemId, responseId: event.responseId,
      authority: event.authority });
    return open(config, {
      ...callbacks,
      onSpeechStarted(event) {
        trace('turn.started', { ...context(event), turnId: event.itemId });
        return callbacks.onSpeechStarted?.(event);
      },
      onInputTranscriptDone(event) {
        trace('turn.transcript', { ...context(event), turnId: event.itemId, text: event.text });
        return callbacks.onInputTranscriptDone?.(event);
      },
      onResponseCreated(event) {
        trace('response.created', context(event));
        return callbacks.onResponseCreated?.(event);
      },
      onOutputAudioDelta(event) {
        const previous = host.traceResponse;
        host.traceResponse = event;
        try { return callbacks.onOutputAudioDelta?.(event); }
        finally { host.traceResponse = previous; }
      },
      onOutputTextDone(event) {
        trace('response.text', { ...context(event), text: event.text, source: event.source,
          suppressed: event.audioSuppressed });
        return callbacks.onOutputTextDone?.(event);
      },
      onResponseDone(event) {
        trace('response.done', { ...context(event), status: event.status });
        return callbacks.onResponseDone?.(event);
      },
      onBargeIn(event) {
        trace('response.barge_in', context(event));
        return callbacks.onBargeIn?.(event);
      },
      onFunctionCall(event) {
        trace('response.tool', { ...context(event), tool: event.name, toolCallId: event.callId });
        return callbacks.onFunctionCall?.(event);
      },
    });
  };
}
