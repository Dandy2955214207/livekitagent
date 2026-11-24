// LiveHostControl backed by LiveKit data delivery and the phone's Web Audio sink.
export class LiveKitHost {
  constructor(emit, { epoch = 1, callId }) {
    this.emit = emit;
    this.epoch = epoch;
    this.callId = callId;
    this.nextOutput = 0;
    this.outputs = new Map();
    this.output = undefined;
    this.image = undefined;
    this.inputMuted = false;
    this.closed = false;
  }
  send(type, data = {}) { return this.emit({ type, callId: this.callId, epoch: this.epoch, ...data }); }
  valid(epoch) { return !this.closed && epoch === this.epoch; }
  setCallState(epoch, state) { return this.valid(epoch) && this.send('state', { state }); }
  setCoordinator(epoch) { return this.valid(epoch); }
  isOutputMuted() { return false; }
  isInputMuted() { return this.inputMuted; }
  setCaption(epoch, text) { return this.valid(epoch) && this.send('caption', { text: text.slice(0, 8000) }); }
  setTranscript() { return true; } // Final, identified text comes from the session-log callback.
  setStatusText(epoch, text) { return this.valid(epoch) && this.send('notice', { text: (text ?? '').slice(0, 1000) }); }
  setProviderReachability() {}
  failCall(epoch, _message, code) {
    const detail = this.terminalDiagnostic;
    return this.valid(epoch) && this.send('fatal', { code: code ??
      (detail && Date.now() - detail.at < 2000 ? detail.code : undefined) ?? 'official_call_failed',
      stage: code ? 'audio_output' : detail?.stage, status: code ? undefined : detail?.status,
      pendingOutputs: this.outputs.size, outputBytes: this.output?.bytes ?? 0 });
  }
  sendOutputAudio(epoch, pcm) {
    if (!this.valid(epoch) || !pcm.byteLength || pcm.byteLength % 2) return false;
    if (!this.output || this.output.ended) {
      if (this.outputs.size >= 8) return this.failCall(epoch, null, 'playback_receipt_backlog'), false;
      this.output = { id: ++this.nextOutput, sequence: 0, bytes: 0, started: false, ended: false,
        responseId: (this.memoryResponse ?? this.traceResponse)?.responseId };
      this.outputs.set(this.output.id, this.output);
      this.latency?.output(this.output);
      this.timeline?.outputCreated(this.output);
      this.trace?.('output.created', { outputId: this.output.id, responseId: this.output.responseId,
        turnId: (this.memoryResponse ?? this.traceResponse)?.inputItemId });
    }
    const output = this.output;
    if (output.bytes + pcm.byteLength > 960000) return this.failCall(epoch, null, 'audio_output_limit'), false;
    output.bytes += pcm.byteLength;
    const bytes = Buffer.from(pcm);
    // Stay below LiveKit's reliable packet size, including Base64/JSON overhead.
    for (let at = 0; at < bytes.length; at += 4800) {
      if (!this.send('audio', { outputId: output.id, sequence: output.sequence++,
        audio: bytes.subarray(at, at + 4800).toString('base64') })) return false;
    }
    return true;
  }
  finishOutputAudio(epoch) {
    if (!this.valid(epoch) || !this.output || this.output.ended) return;
    this.output.ended = true;
    this.trace?.('output.end', { outputId: this.output.id, responseId: this.output.responseId });
    this.send('audio_end', { outputId: this.output.id, sequence: this.output.sequence });
  }
  clearOutput(epoch) {
    if (!this.valid(epoch)) return;
    if (this.trace) for (const output of this.outputs.values()) {
      this.trace('output.cleared', { outputId: output.id, responseId: output.responseId,
        status: output.started ? 'interrupted' : 'discarded' });
    }
    if (this.timeline) {
      for (const output of this.outputs.values()) this.timeline.playback(output, output.started ? 'interrupted' : 'discarded');
    }
    this.outputs.clear(); this.output = undefined;
    this.latency?.clearOutput();
    this.send('audio_clear', { throughId: this.nextOutput });
  }
  receipt(event, session) {
    if (!this.valid(event.epoch) || event.callId !== this.callId) return false;
    const output = this.outputs.get(event.outputId);
    if (!output) return false;
    if (event.type === 'playback_started' && !output.started) {
      output.started = true;
      this.latency?.started(output, event.browserBufferMs);
      this.timeline?.playback(output, 'started');
      if (output === this.output) session.playbackStarted({ epoch: this.epoch });
      return true;
    }
    if (event.type === 'playback_completed' && output.started && output.ended && event.sequence === output.sequence) {
      this.outputs.delete(output.id);
      this.timeline?.playback(output, 'completed');
      if (output === this.output) session.playbackCompleted({ epoch: this.epoch });
      return true;
    }
    if (event.type === 'playback_blocked') {
      session.outputMuted({ epoch: this.epoch });
      this.clearOutput(this.epoch);
      return true;
    }
    return false;
  }
  async captureVisualContext(caller) {
    if (caller !== this.callId || !this.image || Date.now() - this.image.at > 1500) throw new Error('camera_frame_unavailable');
    return { source: 'camera', image: this.image.image, width: this.image.width, height: this.image.height };
  }
}
