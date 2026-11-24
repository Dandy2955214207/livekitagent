import { criterionKind } from './evidence-monitor.mjs';

// Call-scoped bookkeeping only. No persistent/semantic memory and no image logs.
export class IntentState {
  constructor(emit, now = Date.now) { this.emit = emit; this.now = now; this.tasks = new Map(); this.paused = false; }
  changed(task, notification) {
    let state = this.tasks.get(task.taskId);
    if (!state || state.generation !== task.generation) {
      state = { generation: task.generation, phase: 'waiting_for_media', deliveredCount: state?.deliveredCount ?? 0 };
      this.tasks.set(task.taskId, state);
      while (this.tasks.size > 64) this.tasks.delete(this.tasks.keys().next().value);
    }
    if (notification === 'delivered' && state.notification !== 'delivered') state.deliveredCount++;
    Object.assign(state, { repeat: task.repeat, kind: task.monitorMode === 'always' ? 'narration' :
      task.taskType === 'time_reminder' ? 'timer' : criterionKind(task.taskDescription),
      condition: (task.taskDescription ?? '').slice(0, 220), status: task.status,
      triggerCount: task.triggerCount, notification });
    this.publish(task.taskId);
  }
  observed(observation) {
    const state = this.tasks.get(observation.taskId);
    if (!state || state.generation !== observation.generation || state.status !== 'running') return;
    Object.assign(state, { observation: observation.state, reason: observation.reason,
      evaluationMs: observation.evaluationMs, evidenceAgeMs: observation.evidenceAgeMs,
      evidence: observation.evidence?.map(e => e.detail).join('；').slice(0, 240) ?? '',
      phase: observation.state === 'unknown' ? observation.reason : state.phase });
    this.publish(observation.taskId);
  }
  debug(event, details) {
    const state = this.tasks.get(details.taskId);
    if (!state || (details.generation !== undefined && state.generation !== details.generation)) return;
    if (event === 'proactive.evaluation_decision') {
      state.phase = details.decision;
      state.awaitingReentry = details.awaitingFalse;
      if (details.decision === 'notification_accepted') state.matchedAt = this.now();
    } else if (event === 'proactive.cooldown_started') {
      state.phase = 'cooldown'; state.awaitingReentry = details.awaitingFalse;
    } else return;
    this.publish(details.taskId);
  }
  budgetPaused() {
    if (this.paused) return;
    this.paused = true;
    for (const id of this.tasks.keys()) this.publish(id);
  }
  cameraCleared() {
    for (const [id, state] of this.tasks) {
      if (!['running', 'provisioning'].includes(state.status) || ['timer', 'narration'].includes(state.kind)) continue;
      Object.assign(state, { phase: 'waiting_for_media', observation: 'unknown', evidence: '',
        evaluationMs: undefined, evidenceAgeMs: undefined });
      this.publish(id);
    }
  }
  publish(id) {
    const state = this.tasks.get(id);
    this.emit({ taskId: `proactive:${id}`, ...state,
      phase: this.paused && ['running', 'provisioning'].includes(state.status) ? 'budget_paused' : state.phase });
  }
}
