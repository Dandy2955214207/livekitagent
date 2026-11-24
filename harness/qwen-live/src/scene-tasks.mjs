import { randomUUID } from 'node:crypto';
import { GuideTarget } from './guide-target.mjs';

const active = state => state && !['completed', 'cancelled', 'failed'].includes(state.status);
export const isStepCompletionTranscript = transcript =>
  !/(?:没|未|不是|不算|别|不要|还差|吗|么|？|\?)/u.test(transcript) &&
  /(?:完成了|做完了|做好了|画好了|画完了|这步好了|这一步好了|当前步骤好了|我好了|已经完成|^好了(?:[。！!，,\s]*(?:可以)?(?:继续|下一步))?[。！!\s]*$)/u.test(transcript.trim());
const text = (value, max) => {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Error('invalid_scene_text');
  return value.trim();
};
export class SceneTasks {
  constructor(getScheduler, emit, { now = Date.now } = {}) {
    this.getScheduler = getScheduler; this.emit = emit; this.now = now;
    this.state = null; this.children = new Map(); this.ownedIds = new Set(); this.deliveries = new Map(); this.closed = false;
  }
  attachContext(send) { this.sendContext = send; }
  snapshot() {
    const s = this.state;
    if (!s) return { active: false };
    return { active: active(s), id: s.id, revision: s.revision, mode: s.mode, goal: s.goal, focus: s.focus,
      depth: s.depth, status: s.status, step: s.mode === 'teach' ? s.index + 1 : undefined,
      total: s.steps.length, instruction: s.steps[s.index]?.instruction, completion: s.steps[s.index]?.completion,
      moving: Boolean(s.moving), subject: s.subject, segments: s.spoken.length, reason: s.reason,
      completedSteps: s.completedSteps, progress: s.progress, delivery: s.delivery,
      target: s.mode === 'guide' ? this.target?.snapshot() : undefined };
  }
  publish() {
    const snapshot = this.snapshot(); this.emit(snapshot, this.state?.completionSource);
    this.sendContext?.(`[SCENE_STATE] ${JSON.stringify(snapshot)} [/SCENE_STATE]`);
    return { ok: true, ...snapshot };
  }
  start(input, { planning = false, opening } = {}) {
    if (this.budgetExhausted) throw new Error('scene_budget_exhausted');
    if (this.closed || active(this.state)) throw new Error('scene_already_active_stop_or_modify_first');
    if (!['guide', 'teach'].includes(input.mode) || !['brief', 'normal', 'deep'].includes(input.depth)) throw new Error('invalid_scene_mode');
    const goal = text(input.goal, 200), focus = text(input.focus, 400);
    if (!Array.isArray(input.steps) || (input.mode === 'teach' && !planning && (input.steps.length < 2 || input.steps.length > 12)) ||
        (input.mode === 'guide' && input.steps.length)) throw new Error('invalid_scene_steps');
    const steps = input.steps.map(step => ({ instruction: text(step.instruction, 200), completion: text(step.completion, 250) }));
    this.state = { id: randomUUID(), revision: 1, mode: input.mode, goal, focus, depth: input.depth, steps,
      index: 0, completedSteps: 0, progress: 'pending', delivery: 'none',
      status: 'starting', subject: '', spoken: [], subjectVersion: 0, opening };
    this.target = input.mode === 'guide' ? new GuideTarget(this.now) : undefined;
    this.priorityUntil = input.mode === 'guide' ? this.now() + 8000 : 0;
    this.lastClarificationAt = undefined; this.clarifiedView = undefined;
    try {
      if (planning) this.state.status = 'planning';
      else if (input.mode === 'teach') this.instruct(); else this.guide();
    } catch (error) { this.state.status = 'failed'; this.state.reason = 'setup_failed'; this.publish(); throw error; }
    return this.publish();
  }
  beginTeaching(input, planner, goal) {
    const receipt = this.start({ ...input, steps: [] }, { planning: true });
    const id = this.state.id, revision = this.state.revision;
    void planner.plan(goal).then(steps => {
      if (this.closed || this.state.id !== id || this.state.revision !== revision || this.state.status !== 'planning') return;
      if (!Array.isArray(steps) || steps.length < 2 || steps.length > 12) throw new Error('invalid_scene_steps');
      this.state.steps = steps.map(step => ({ instruction: text(step.instruction, 200), completion: text(step.completion, 250) }));
      this.instruct(); this.publish();
    }).catch(() => {
      if (this.closed || this.state.id !== id || this.state.revision !== revision) return;
      this.cancelChildren(); this.state.status = 'failed'; this.state.reason = 'plan_failed'; this.publish();
    });
    return receipt;
  }
  own(task, role) {
    this.ownedIds.add(task.taskId);
    while (this.ownedIds.size > 128) this.ownedIds.delete(this.ownedIds.values().next().value);
    if (!this.current(task.taskId)) this.children.set(task.taskId, { id: this.state.id, revision: this.state.revision, role });
    return task;
  }
  createChild(role, create) {
    this.installingRole = role;
    try { const task = create(); if (task.status === 'failed') throw new Error('scene_observer_setup_failed'); return this.own(task, role); }
    finally { this.installingRole = undefined; }
  }
  roleFor(taskId) { return this.current(taskId)?.role ?? this.installingRole; }
  current(taskId) { const child = this.children.get(taskId); return child && child.id === this.state?.id && child.revision === this.state?.revision ? child : undefined; }
  instruct() {
    const s = this.state; s.status = 'instructing'; s.reason = undefined; s.delivery = 'pending';
    this.createChild('instruction', () => this.getScheduler().createTimer({ title: `教学第${s.index + 1}步指引-${s.revision}`, durationSec: 0.05,
      reminderText: s.progress === 'completed' ? `所有步骤已完成。简短总结${s.goal}，不添加步骤。` :
        `请只说当前第${s.index + 1}步：${s.steps[s.index].instruction}。说完等待用户操作，不讲下一步。` }));
  }
  watchStep() {
    const s = this.state; s.status = 'waiting_step';
    const next = s.steps[s.index + 1];
    this.createChild('completion', () => this.getScheduler().createPerceptionMonitor({ title: `教学第${s.index + 1}步检查-${s.revision}`,
      modalities: ['vision'], repeat: false, condition: `[state] ${s.steps[s.index].completion}`,
      triggerResponse: next ? `只用一句短句指导下一步：${next.instruction}。说完等待操作，不继续往下讲。` :
        `最后一步已完成，${s.goal}的分步教学结束。简短总结，不添加新步骤。` }));
    this.publish();
  }
  guide() {
    const s = this.state; s.status = 'watching'; s.reason = undefined;
    this.createChild('guide', () => this.getScheduler().createLiveNarration({ title: `主体讲解-${s.revision}`, modalities: ['vision'],
      narrationFocus: s.focus, narrationStyle: '只说本次主体讲解 summary，一至两句；不扩写环境，不回顾旧主体。' }));
  }
  changed(task, notification) {
    if (this.installingRole && !this.current(task.taskId)) this.own(task, this.installingRole);
    const child = this.current(task.taskId); if (!child || this.closed) return;
    if (child.role === 'clarification') {
      if (notification === 'delivered' || notification === 'undelivered' || task.status === 'failed') {
        this.children.delete(task.taskId); this.state.delivery = notification ?? 'undelivered'; this.publish();
      }
      return;
    }
    if (task.status === 'failed' || notification === 'undelivered' && child.role !== 'guide') {
      this.state.status = 'paused'; this.state.delivery = 'undelivered'; this.state.reason = 'delivery_or_observer_failed'; this.publish(); return;
    }
    if (notification !== 'delivered' || child.handled) return;
    child.handled = true;
    if (child.role === 'guide') { child.handled = false; this.state.delivery = 'delivered'; this.publish(); return; }
    // Leave the official acknowledgement stack before installing the next child.
    queueMicrotask(() => {
      if (!this.current(task.taskId) || this.closed || this.state.status === 'paused') return;
      this.children.delete(task.taskId);
      this.state.delivery = 'delivered';
      if (this.state.progress === 'completed') {
        this.state.status = 'completed'; this.state.revision++; this.publish();
      } else {
        this.state.revision++;
        try { this.watchStep(); }
        catch { this.state.status = 'paused'; this.state.reason = 'delivery_or_observer_failed'; this.publish(); }
      }
    });
  }
  onDelivery(delivery) {
    const child = this.current(delivery.taskId); if (!child) return;
    if (this.deliveries.has(delivery.deliveryId)) return;
    this.deliveries.set(delivery.deliveryId, { delivery, subjectVersion: this.state.subjectVersion,
      summary: child.role === 'guide' ? this.pendingSummary : undefined });
    this.state.delivery = 'queued';
    if (child.role === 'completion' && !child.progressApplied) {
      child.progressApplied = true; this.completeStep('visual'); this.state.status = 'advancing';
    }
    this.publish();
  }
  completeStep(source) {
    const s = this.state;
    s.completedSteps = Math.max(s.completedSteps, s.index + 1);
    s.completionSource = source;
    s.progress = s.completedSteps >= s.steps.length ? 'completed' : 'in_progress';
    s.index = Math.min(s.completedSteps, s.steps.length - 1);
  }
  playbackStarted(delivery) {
    if (this.current(delivery.taskId) && this.deliveries.has(delivery.deliveryId)) {
      if (this.current(delivery.taskId).role === 'guide') this.priorityUntil = 0;
      this.state.delivery = 'speaking'; this.publish();
    }
  }
  acknowledged(delivery) {
    const entry = this.deliveries.get(delivery.deliveryId); this.deliveries.delete(delivery.deliveryId);
    if (!entry || !this.current(delivery.taskId) || this.state.mode !== 'guide' || entry.subjectVersion !== this.state.subjectVersion) return;
    if (entry.summary && !this.state.spoken.includes(entry.summary)) this.state.spoken.push(entry.summary);
    this.publish();
  }
  invalidated(delivery) {
    if (this.deliveries.delete(delivery.deliveryId) && this.current(delivery.taskId)) {
      this.state.delivery = 'undelivered'; this.publish();
    }
  }
  cancelChildren() {
    this.target?.resetExitCheck();
    const ids = [...this.children.keys()]; this.children.clear();
    for (const id of ids) this.getScheduler()?.cancelTaskById(id);
    this.deliveries.clear();
  }
  control(input, { transcript = '', trustedUser = false } = {}) {
    const s = this.state;
    if (!active(s) || input.revision !== s.revision || input.id !== undefined && input.id !== s.id) throw new Error('stale_scene_revision');
    const action = input.action;
    if (s.status === 'planning' && action !== 'stop') throw new Error('scene_still_planning');
    if (!['pause', 'resume', 'repeat_step', 'confirm_step', 'stop', 'depth', 'focus', 'target'].includes(action)) throw new Error('invalid_scene_action');
    if (action === 'resume' && s.status !== 'paused') throw new Error('scene_not_paused');
    if (s.reason === 'budget' && action !== 'stop') throw new Error('scene_budget_exhausted');
    if (action === 'confirm_step' && (s.mode !== 'teach' || s.status !== 'waiting_step' ||
        !trustedUser && !isStepCompletionTranscript(transcript))) throw new Error('explicit_user_completion_required');
    if (action === 'depth' && (s.mode !== 'guide' || !['brief', 'normal', 'deep'].includes(input.depth))) throw new Error('invalid_guide_depth');
    if (action === 'focus' && s.mode !== 'guide') throw new Error('not_guiding');
    const focus = action === 'focus' ? text(input.focus, 400) : undefined;
    let selection;
    if (action === 'target') {
      if (s.mode !== 'guide' || s.status !== 'watching') throw new Error('guide_not_watching');
      if (input.point !== undefined) {
        if (!trustedUser || input.view !== this.target.view || !input.point ||
            !['x', 'y'].every(key => Number.isFinite(input.point[key]) && input.point[key] >= 0 && input.point[key] <= 1))
          throw new Error('invalid_target_selection');
        selection = { point: { x: input.point.x, y: input.point.y } };
      } else selection = { hint: text(input.targetHint, 160) };
    }
    if (action === 'repeat_step' && s.mode !== 'teach') throw new Error('not_teaching');
    if (selection) {
      // Keep the resident observer connection. Version the view so an in-flight
      // result from before this correction cannot select or describe the old target.
      s.revision++; s.view = (s.view ?? 0) + 1;
      this.cancelClarification();
      for (const child of this.children.values()) child.revision = s.revision;
      for (const { delivery } of [...this.deliveries.values()]) this.getScheduler().undeliverDelivery(delivery, 'target_changed');
      this.deliveries.clear();
      this.target.select(selection); s.subjectVersion++; this.pendingSummary = undefined;
      this.clarifiedView = undefined; this.priorityUntil = this.now() + 8000;
      return this.publish();
    }
    s.revision++; this.cancelChildren();
    if (action === 'stop' || action === 'pause') { s.status = action === 'stop' ? 'cancelled' : 'paused'; s.reason = 'user'; s.delivery = 'none'; }
    else {
      if (action === 'confirm_step') this.completeStep('user');
      if (s.mode === 'teach') this.instruct();
      else {
        if (action === 'depth') s.depth = input.depth;
        if (focus) { s.focus = focus; s.subject = ''; s.spoken = []; s.subjectVersion++; this.target = new GuideTarget(this.now); this.clarifiedView = undefined; }
        if (selection) { this.target.select(selection); s.subjectVersion++; this.pendingSummary = undefined; this.clarifiedView = undefined; }
        // Editing a paused requirement must not silently resume it.
        if (s.status !== 'paused' || action === 'resume') this.guide();
      }
    }
    return this.publish();
  }
  guideMotion(moving) {
    const s = this.state;
    if (s?.mode !== 'guide' || s.status !== 'watching' || Boolean(s.moving) === moving) return;
    s.moving = moving; s.view = (s.view ?? 0) + 1;
    this.target.move(moving);
    if (moving) {
      if (!this.target.current) { s.subject = ''; s.spoken = []; }
      s.subjectVersion++; this.pendingSummary = undefined;
      for (const { delivery } of [...this.deliveries.values()]) this.getScheduler().undeliverDelivery(delivery, 'camera_moved');
      this.deliveries.clear();
      this.cancelClarification();
    }
    this.publish();
  }
  monitorContext(taskId) {
    const child = this.current(taskId); if (!child || child.role !== 'guide') return undefined;
    return { focus: this.state.focus, depth: this.state.depth, subject: this.state.subject,
      ...this.target.context(),
      alreadySpoken: this.state.spoken.slice(-4), segmentLimit: { brief: 1, normal: 2, deep: 4 }[this.state.depth] };
  }
  cancelClarification() {
    for (const [id, child] of [...this.children]) if (child.role === 'clarification') {
      this.children.delete(id); this.getScheduler().cancelTaskById(id);
    }
  }
  acceptGuide(taskId, observation) {
    if (!this.current(taskId) || this.state.status !== 'watching' || this.state.moving) return false;
    const s = this.state;
    const decision = this.target.observe(observation);
    if (decision.released || decision.resetView) s.view = (s.view ?? 0) + 1;
    if (decision.changed || decision.released) {
      s.subject = decision.subject ?? ''; s.subjectVersion++; s.spoken = []; this.pendingSummary = undefined;
      for (const { delivery } of [...this.deliveries.values()]) this.getScheduler().undeliverDelivery(delivery, 'subject_changed');
      this.deliveries.clear();
    }
    if (decision.accepted || decision.released) this.cancelClarification();
    if (!decision.accepted) {
      if (decision.invalidate) for (const { delivery } of [...this.deliveries.values()])
        if (this.current(delivery.taskId)?.role === 'guide') {
          this.getScheduler().undeliverDelivery(delivery, 'target_uncertain'); this.deliveries.delete(delivery.deliveryId);
        }
      if (decision.ask && this.clarifiedView !== this.target.view &&
          (this.lastClarificationAt === undefined || this.now() - this.lastClarificationAt > 30000)) {
        this.lastClarificationAt = this.now(); this.clarifiedView = this.target.view;
        try { this.createChild('clarification', () => this.getScheduler().createTimer({ title: `讲解目标确认-${s.revision}-${this.target.view}`,
          durationSec: .05, reminderText: '只简短询问：您想听哪一件？可以告诉我物品的名称或位置。不要介绍任何物品。' })); } catch { /* UI clarification remains available. */ }
      }
      this.publish(); return false;
    }
    this.publish();
    if (!observation.summary || this.deliveries.size || s.spoken.length >= { brief: 1, normal: 2, deep: 4 }[s.depth] || s.spoken.includes(observation.summary)) return false;
    observation.subject = decision.subject;
    this.pendingSummary = observation.summary; return true;
  }
  pauseForBudget() {
    this.budgetExhausted = true;
    if (active(this.state) && this.state.reason !== 'budget') {
      this.state.revision++; this.cancelChildren();
      this.state.status = 'paused'; this.state.reason = 'budget'; this.state.delivery = 'none'; this.publish();
    }
  }
  close() { this.closed = true; this.cancelChildren(); this.sendContext = undefined; }
}
