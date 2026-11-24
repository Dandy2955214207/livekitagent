const MAX_EVENTS = 480;
const MAX_PENDING = 48;
const MAX_RESULTS = 16;
export const HISTORY_SOURCES = ['vision', 'dialogue', 'task', 'playback'];
export const PLAYBACK_STATES = ['completed', 'started', 'interrupted', 'discarded', 'queued', 'any'];
const ended = new Set(['completed', 'cancelled', 'failed', 'paused']);

export const chronological = (a, b) => a.start - b.start || a.end - b.end || (a.sequence ?? 0) - (b.sequence ?? 0);

function remember(map, key, value) {
  map.set(key, value);
  if (map.size > MAX_PENDING) map.delete(map.keys().next().value);
}

function matches(text, query) {
  if (!query) return true;
  const value = text.toLowerCase();
  const words = query.toLowerCase().trim().split(/\s+/u);
  return words.every(word => value.includes(word));
}

export function validHistoryScope(scope) {
  return scope === undefined || scope && ['task', 'event', 'previous'].includes(scope.type) &&
    (scope.id === undefined || typeof scope.id === 'string' && scope.id.length <= 120) &&
    (scope.query === undefined || typeof scope.query === 'string' && scope.query.length <= 200) &&
    (scope.state === undefined || typeof scope.state === 'string' && /^[a-z_]{1,24}$/u.test(scope.state)) &&
    (scope.kind === undefined || ['guide', 'teach', 'timer', 'monitor'].includes(scope.kind)) &&
    (scope.source === undefined || HISTORY_SOURCES.includes(scope.source)) &&
    (scope.pick === undefined || ['latest', 'earliest'].includes(scope.pick)) &&
    (scope.boundary === undefined || ['start', 'end'].includes(scope.boundary)) &&
    (scope.relation === undefined || ['during', 'before', 'after'].includes(scope.relation));
}

export class CallTimeline {
  constructor(now, origin, trace) {
    this.now = now;
    this.origin = origin;
    this.trace = trace;
    this.events = [];
    this.tasks = new Map();
    this.deliveries = new Map();
    this.responses = new Map();
    this.sequence = 0;
    this.evicted = 0;
  }

  seconds(at = this.now()) { return Math.max(0, (at - this.origin) / 1000); }

  append(source, data, end = this.seconds()) {
    const event = { source, start: end, end, ...data, sequence: ++this.sequence };
    event.eventId = 'event-' + event.sequence;
    this.events.push(event);
    if (this.events.length > MAX_EVENTS) {
      const removed = this.events.shift();
      this.evicted++;
      this.trace?.('memory.timeline_evicted', { eventId: removed.eventId });
    }
    this.trace?.('memory.timeline_written', event);
    return event;
  }

  task(state, completionSource) {
    if (!state?.id) return;
    const kind = state.mode ?? state.kind;
    const label = state.goal ?? state.title ?? '';
    const detail = [label, state.step && '第' + state.step + '步', state.instruction, state.subject, state.condition].filter(Boolean).join(' · ').slice(0, 520);
    const current = { taskId: state.id, kind, label, state: state.status, reason: state.reason, step: state.step,
      completedSteps: state.completedSteps, completionSource, progress: state.progress, text: detail };
    const fingerprint = JSON.stringify(current);
    const prior = this.tasks.get(state.id);
    if (fingerprint === prior?.fingerprint) return;
    const event = this.append('task', { ...current, taskStart: prior?.start ?? this.seconds() });
    remember(this.tasks, state.id, { fingerprint, start: event.taskStart });
  }

  userText(turnId, text, at) {
    if (!text?.trim() || this.events.some(row => row.source === 'dialogue' && row.turnId === turnId && turnId)) return;
    this.append('dialogue', { role: 'user', turnId, text: text.trim().slice(0, 520) }, this.seconds(at));
  }

  userTime(turnId) {
    if (!turnId) return undefined;
    const row = this.events.findLast(event => event.source === 'dialogue' && event.turnId === turnId);
    return row ? this.origin + row.start * 1000 : undefined;
  }

  queue(deliveryId, task) {
    if (this.deliveries.has(deliveryId)) return;
    const row = this.append('playback', { deliveryId, taskId: task?.id, kind: task?.kind,
      label: task?.label, subject: task?.subject, step: task?.step, state: 'queued' });
    remember(this.deliveries, deliveryId, row);
  }

  stopDelivery(deliveryId) {
    const queued = this.deliveries.get(deliveryId);
    if (!queued) return;
    this.deliveries.delete(deliveryId);
    const { taskId, kind, label, subject, step } = queued;
    this.append('playback', { deliveryId, taskId, kind, label, subject, step, state: 'discarded' });
  }

  response(event) {
    remember(this.responses, event.responseId, { turnId: event.inputItemId, authority: event.authority, text: '' });
  }

  linkDelivery(responseId, deliveryId) {
    const response = this.responses.get(responseId);
    const queued = this.deliveries.get(deliveryId);
    if (response && queued) response.delivery = queued;
  }

  assistantText(event) {
    const response = this.responses.get(event.responseId);
    if (!response || event.audioSuppressed || event.source !== 'audio_transcript') return;
    response.text = [response.text, event.text.trim()].filter(Boolean).join(' ').slice(0, 520);
  }

  outputCreated(output) {
    const response = this.responses.get(output.responseId);
    if (!response) return;
    response.outputId = output.id;
    if (response.delivery) this.deliveries.delete(response.delivery.deliveryId);
  }

  finishResponse(responseId) {
    const response = this.responses.get(responseId);
    if (!response || response.outputId !== undefined) return;
    if (response.delivery) this.stopDelivery(response.delivery.deliveryId);
    this.responses.delete(responseId);
  }

  playback(output, state) {
    const response = this.responses.get(output.responseId);
    const delivery = response?.delivery;
    if (delivery) this.deliveries.delete(delivery.deliveryId);
    const record = { outputId: output.id, responseId: output.responseId, turnId: response?.turnId, role: 'assistant',
      deliveryId: delivery?.deliveryId, taskId: delivery?.taskId, kind: delivery?.kind,
      label: delivery?.label, subject: delivery?.subject, step: delivery?.step, state };
    if (state === 'started') {
      output.timelineStarted = this.seconds();
      this.append('playback', record, output.timelineStarted);
      return;
    }
    this.append('playback', { ...record, start: output.timelineStarted ?? this.seconds(),
      ...(state === 'completed' && response?.text ? { text: response.text } : {}) });
    this.responses.delete(output.responseId);
  }

  resolve(scope, askedAt, vision, previous, turnId) {
    if (!scope) return undefined;
    if (scope.type === 'previous') {
      return previous ? { ...previous.range, inherited: true } : { missing: true };
    }
    const past = this.events.filter(row => row.end <= askedAt && (!turnId || row.turnId !== turnId));
    let candidates;
    if (scope.type === 'task') {
      const latest = new Map();
      for (const event of past) if (event.source === 'task') latest.set(event.taskId, event);
      candidates = [...latest.values()].map(row => ({
        id: row.taskId, kind: row.kind, label: row.label, start: row.taskStart, state: row.state,
        end: ended.has(row.state) ? row.end : askedAt, source: 'task',
      }));
    } else {
      candidates = [...past.filter(row => row.source !== 'playback' || scope.id || row.state === (scope.state ?? 'completed')), ...vision]
        .filter(row => row.end <= askedAt).map(row => ({
          id: row.eventId ?? 'frame-' + row.firstFrame + '-' + row.lastFrame,
          kind: row.kind, taskId: row.taskId, state: row.state,
          label: [row.subject, row.text ?? row.label].filter(Boolean).join(' · '),
          start: row.start, end: row.end, source: row.firstFrame === undefined ? row.source : 'vision',
        }));
    }
    candidates = candidates.filter(item => (!scope.id || item.id === scope.id) && (!scope.kind || item.kind === scope.kind) &&
      (!scope.source || item.source === scope.source) && (!scope.state || item.state === scope.state) &&
      matches(item.label, scope.query)).sort(chronological);
    const selected = scope.pick === 'latest' ? candidates.at(-1) : scope.pick === 'earliest' ? candidates[0] :
      candidates.length === 1 ? candidates[0] : undefined;
    if (!selected) return candidates.length ? { candidates: candidates.slice(-5) } : { missing: true };
    return { anchor: selected, start: selected.start, end: selected.end };
  }

  search({ sources, from, to, taskId, turnId, query, ordered, playbackState }) {
    const rows = this.events.filter(row => row.end >= from && row.end <= to && (!turnId || row.turnId !== turnId) &&
      (!taskId || row.taskId === taskId || row.source === 'dialogue' ||
        row.source === 'playback' && !row.taskId && sources.includes('dialogue') && row.state === 'completed') &&
      (row.source === 'playback' ? sources.includes('playback') && (playbackState === 'any' || row.state === playbackState) ||
        sources.includes('dialogue') && !row.taskId && row.state === 'completed' : sources.includes(row.source)) &&
      matches([row.text, row.label, row.subject].filter(Boolean).join(' '), query)).sort(chronological);
    const records = ordered ? rows.slice(0, MAX_RESULTS) : rows.slice(-MAX_RESULTS);
    return { records, availableRecords: rows.length, truncated: records.length < rows.length };
  }

  close() {
    this.events.length = 0;
    this.tasks.clear();
    this.deliveries.clear();
    this.responses.clear();
  }
}
