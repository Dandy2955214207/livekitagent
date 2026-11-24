import { randomUUID } from 'node:crypto';

// All durations use one server clock. Phone acknowledgement includes the return
// network trip; it is not an acoustic measurement of what a person heard.
export class Latency {
  constructor(emit, now = Date.now) { this.emit = emit; this.now = now; this.observations = new Map(); this.deliveries = new Map(); }
  sample(metric, start, fields = {}) {
    const ms = this.now() - start;
    if (Number.isFinite(ms) && ms >= 0 && ms < 600000) this.emit({ metric, ms, ...fields });
  }
  userStarted() { this.next = undefined; }
  userStopped() { this.next = { traceId: randomUUID(), kind: 'conversation', at: this.now() }; }
  observed(observation) {
    if (Number.isFinite(observation.evaluationMs)) this.emit({ metric: 'observer_evaluation', ms: observation.evaluationMs, taskId: observation.taskId });
    if (observation.state === 'matched') this.observations.set(observation.taskId, { at: this.now(), frameAt: observation.at });
    else this.observations.delete(observation.taskId);
    while (this.observations.size > 16) this.observations.delete(this.observations.keys().next().value);
  }
  queued(delivery) {
    const observation = this.observations.get(delivery.taskId);
    this.deliveries.set(delivery.deliveryId, { traceId: delivery.deliveryId, taskId: delivery.taskId,
      kind: observation ? 'visual' : 'timer', at: this.now(), frameAt: observation?.frameAt,
      cameraAt: this.camera?.taskId === delivery.taskId ? this.camera.at : undefined });
    while (this.deliveries.size > 16) this.deliveries.delete(this.deliveries.keys().next().value);
  }
  preparing(delivery) {
    const trace = this.deliveries.get(delivery.deliveryId); if (!trace) return;
    trace.preparingAt = this.now(); this.next = trace;
    this.sample('intervention_queue', trace.at, { traceId: trace.traceId, kind: trace.kind });
  }
  retired(delivery) {
    if (this.next?.traceId === delivery.deliveryId) this.next = undefined;
    this.deliveries.delete(delivery.deliveryId);
  }
  output(output) {
    const trace = this.next; this.next = undefined;
    output.trace = trace; output.firstAudioAt = this.now();
    if (trace) this.sample(trace.kind === 'conversation' ? 'speech_stop_to_first_audio' : 'intervention_to_first_audio', trace.at,
      { traceId: trace.traceId, outputId: output.id, kind: trace.kind });
    if (trace?.preparingAt !== undefined) this.sample('preparing_to_first_audio', trace.preparingAt, { traceId: trace.traceId, kind: trace.kind });
  }
  started(output, browserBufferMs) {
    const fields = { outputId: output.id, traceId: output.trace?.traceId, kind: output.trace?.kind ?? 'unattributed' };
    this.sample('first_audio_to_playback_ack', output.firstAudioAt, fields);
    if (output.trace) {
      this.sample('trigger_to_playback_ack', output.trace.at, fields);
      if (output.trace.frameAt !== undefined) this.sample('latest_evidence_to_playback_ack', output.trace.frameAt, fields);
      if (output.trace.cameraAt !== undefined) {
        this.sample('motion_detected_to_playback_ack', output.trace.cameraAt, fields);
        if (this.camera?.at === output.trace.cameraAt) this.camera = undefined;
      }
    }
    if (Number.isFinite(browserBufferMs) && browserBufferMs >= 0 && browserBufferMs < 60000)
      this.emit({ metric: 'browser_buffer_to_playback', ms: browserBufferMs, ...fields });
  }
  cameraChanged(taskId) { this.camera = { taskId, at: this.now() }; }
  clearOutput() {
    // Command latency only, not phone speaker stop time. Ignore unrelated later clears.
    if (this.camera && this.now() - this.camera.at < 1000 && !this.camera.clearReported) {
      this.camera.clearReported = true;
      this.sample('motion_detected_to_clear_command', this.camera.at, { taskId: this.camera.taskId });
    }
  }
}
