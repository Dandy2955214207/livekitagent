import { randomUUID } from 'node:crypto';

const terminal = status => ['completed', 'cancelled', 'failed'].includes(status);
const clean = (value, max = 400) => {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Error('invalid_requirement_text');
  return value.trim();
};

// Per-call ledger. Official scheduler still owns evidence, rearming and speech.
// A requirement survives pause/reinstallation; its transient monitor ID does not.
export class Requirements {
  constructor(scenes, emit) {
    this.scenes = scenes; this.emit = emit; this.rows = new Map(); this.byTask = new Map();
  }
  snapshot() {
    const rows = [...this.rows.values()];
    const visible = [...rows.filter(r => !terminal(r.status)), ...rows.filter(r => terminal(r.status)).slice(-4)];
    return { primary: this.scenes.snapshot(), auxiliary: visible.map(r => ({
      id: r.id, revision: r.revision, taskId: r.taskId, title: r.title, kind: r.kind,
      status: r.status, reason: r.reason, repeat: r.input.repeat ?? false,
      ...(!terminal(r.status) ? { condition: r.input.condition, triggerResponse: r.input.triggerResponse,
        reminderText: r.input.reminderText, durationSec: r.input.durationSec } : {}),
      progress: r.progress, delivery: r.delivery, deliveredCount: r.deliveredCount,
    })) };
  }
  attachContext(send) { this.sendContext = send; this.publish(true); }
  publish(force = false) {
    const snapshot = this.snapshot(), serialized = JSON.stringify(snapshot);
    if (!force && serialized === this.last) return;
    this.last = serialized; this.emit(snapshot);
    this.sendContext?.(`[REQUIREMENTS_STATE] ${serialized} [/REQUIREMENTS_STATE]`);
  }
  active() { return [...this.rows.values()].filter(r => !terminal(r.status)); }
  install(scheduler) {
    this.scheduler = scheduler;
    this.create = {};
    for (const [method, kind] of [['createTimer', 'timer'], ['createPerceptionMonitor', 'monitor'], ['createLiveNarration', 'narration']]) {
      const original = scheduler[method].bind(scheduler); this.create[kind] = original;
      scheduler[method] = input => {
        if (this.scenes.installingRole) return original(input);
        if (kind === 'monitor' && this.budgetExhausted) throw new Error('observation_budget_exhausted');
        if (kind === 'narration') throw new Error('use_start_scene_task_for_guidance');
        if (this.active().length >= 2 || kind === 'monitor' && this.active().some(r => r.kind === 'monitor'))
          throw new Error('auxiliary_capacity_reached');
        clean(input.title, 160);
        if (kind === 'monitor') { clean(input.condition); clean(input.triggerResponse); }
        else clean(input.reminderText);
        const row = { id: randomUUID(), revision: 1, title: input.title, kind, input: { ...input },
          status: 'provisioning', progress: 'pending', delivery: 'none', deliveredCount: 0 };
        this.rows.set(row.id, row);
        try { return this.activate(row); }
        catch (error) { row.status = 'failed'; this.publish(); throw error; }
      };
    }
    const update = scheduler.updateTask.bind(scheduler), cancel = scheduler.cancelTasks.bind(scheduler);
    this.update = update;
    scheduler.updateTask = input => {
      const row = this.resolve(input);
      for (const key of ['title', 'condition', 'triggerResponse', 'reminderText'])
        if (input[key] !== undefined) clean(input[key], key === 'title' ? 160 : 400);
      const result = update({ ...input, targetTitle: row.title, targetTitleContains: undefined });
      this.rememberUpdate(row, result); return result;
    };
    scheduler.cancelTasks = input => {
      // Never let title-less/all selectors accidentally cancel scene children.
      const row = this.resolve(input);
      return cancel({ targetTitle: row.title });
    };
    return this;
  }
  activate(row) {
    this.installing = row;
    try {
      const task = this.create[row.kind](row.input);
      this.bind(row, task); this.changed(task); return task;
    } finally { this.installing = undefined; this.prune(); this.publish(); }
  }
  bind(row, task) { row.taskId = task.taskId; row.generation = task.generation; this.byTask.set(task.taskId, row.id); }
  resolve(input) {
    if (input.all || !input.targetTitle && !input.targetTitleContains) throw new Error('use_requirement_id_and_revision');
    const rows = this.active().filter(r => r.status !== 'paused' &&
      (input.targetTitle ? r.title === input.targetTitle : r.title.includes(input.targetTitleContains)));
    if (rows.length !== 1) throw new Error('ambiguous_requirement_target');
    return rows[0];
  }
  rememberUpdate(row, task) {
    row.revision++; row.generation = task.generation; row.title = task.title;
    row.input = task.taskType === 'time_reminder' ? { title: task.title, durationSec: task.durationSec, reminderText: task.reminderText } :
      { title: task.title, modalities: task.modalities, condition: task.taskDescription, triggerResponse: task.interventionText, repeat: task.repeat };
    row.progress = 'pending'; row.delivery = 'none'; this.publish();
  }
  changed(task, notification) {
    if (this.closed) return;
    if (this.scenes.ownedIds.has(task.taskId) || this.scenes.installingRole) return;
    let row = this.rows.get(this.byTask.get(task.taskId));
    if (!row && this.installing) { row = this.installing; this.bind(row, task); }
    if (!row || row.taskId !== task.taskId || row.generation > task.generation) return;
    row.status = task.status;
    if (notification) row.delivery = notification;
    if (notification === 'undelivered') {
      row.status = 'paused';
      if (row.kind === 'timer') row.input.durationSec = .05;
      const id = row.taskId;
      queueMicrotask(() => {
        if (row.taskId !== id || row.status !== 'paused') return;
        this.byTask.delete(id); this.scheduler.cancelTaskById(id);
      });
    }
    // Delivery IDs, rather than repeated task notifications, count successful speech.
    this.publish();
  }
  onDelivery(delivery) {
    const row = this.rows.get(this.byTask.get(delivery.taskId));
    if (!row || row.generation !== delivery.taskGeneration || row.deliveryId === delivery.deliveryId) return;
    row.progress = 'matched'; row.delivery = 'queued'; row.deliveryId = delivery.deliveryId; this.publish();
  }
  delivered(delivery) {
    const row = this.rows.get(this.byTask.get(delivery.taskId));
    if (!row || row.deliveryId !== delivery.deliveryId || row.generation !== delivery.taskGeneration || row.delivery === 'delivered') return;
    row.deliveredCount++; row.delivery = 'delivered';
    row.progress = row.input.repeat ? 'waiting_next_occurrence' : 'completed'; this.publish();
  }
  playbackStarted(delivery) {
    const row = this.rows.get(this.byTask.get(delivery.taskId));
    if (row?.deliveryId === delivery.deliveryId && row.generation === delivery.taskGeneration) {
      row.delivery = 'speaking'; this.publish();
    }
  }
  control(input, authority = {}) {
    if (input.id === this.scenes.snapshot().id) return this.scenes.control(input, authority);
    const row = this.rows.get(input.id);
    if (!row || row.revision !== input.revision || terminal(row.status)) throw new Error('stale_requirement_revision');
    const action = input.action;
    if (!['pause', 'resume', 'stop', 'update'].includes(action)) throw new Error('invalid_requirement_action');
    if (action === 'resume') {
      if (row.kind === 'monitor' && this.budgetExhausted) throw new Error('observation_budget_exhausted');
      if (row.status !== 'paused') throw new Error('requirement_not_paused');
      // An undelivered visual occurrence must be re-observed, never replay stale evidence.
      this.byTask.delete(row.taskId); this.scheduler.cancelTaskById(row.taskId);
      row.revision++; row.delivery = 'none'; row.progress = 'pending';
      try { this.activate(row); } catch (error) { row.status = 'failed'; this.publish(); throw error; }
    } else if (action === 'pause' || action === 'stop') {
      const task = this.scheduler.listTasks().find(t => t.taskId === row.taskId);
      if (action === 'pause' && row.kind === 'timer' && task?.remainingSec !== undefined)
        row.input.durationSec = Math.max(.05, task.remainingSec);
      this.byTask.delete(row.taskId); row.revision++;
      row.status = action === 'pause' ? 'paused' : 'cancelled'; row.delivery = 'none';
      this.scheduler.cancelTaskById(row.taskId);
    } else {
      const patch = {};
      for (const key of row.kind === 'timer' ? ['reminderText', 'durationSec'] : ['condition', 'triggerResponse', 'repeat']) {
        if (input[key] === undefined) continue;
        if (key === 'repeat') { if (typeof input[key] !== 'boolean') throw new Error('invalid_repeat'); patch[key] = input[key]; }
        else if (key === 'durationSec') { if (!Number.isFinite(input[key]) || input[key] <= 0 || input[key] > 86400) throw new Error('invalid_duration'); patch[key] = input[key]; }
        else patch[key] = clean(input[key]);
      }
      if (!Object.keys(patch).length) throw new Error('empty_requirement_update');
      if (row.status === 'paused') { Object.assign(row.input, patch); row.revision++; }
      else this.rememberUpdate(row, this.update({ targetTitle: row.title, ...patch }));
    }
    this.publish(); return { ok: true, ...this.snapshot() };
  }
  prune() {
    for (const [id, row] of this.rows) {
      if (this.rows.size <= 12) break;
      if (terminal(row.status)) { this.rows.delete(id); this.byTask.delete(row.taskId); }
    }
  }
  pauseForBudget() {
    if (this.budgetExhausted) return;
    this.budgetExhausted = true;
    for (const row of this.active().filter(r => r.kind === 'monitor')) {
      this.control({ id: row.id, revision: row.revision, action: 'pause' }); row.reason = 'budget';
    }
    this.publish();
  }
  close() { this.closed = true; this.sendContext = undefined; }
}
