import { randomUUID } from 'node:crypto';
import { validBox, modelBox } from './guide-monitor.mjs';

export function overlap(a, b) {
  const intersection = Math.max(0, Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0])) *
    Math.max(0, Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]));
  return intersection / (a[2] * a[3] + b[2] * b[3] - intersection);
}
const contains = (b, p) => p.x >= b[0] && p.x <= b[0] + b[2] && p.y >= b[1] && p.y <= b[1] + b[3];
const prominent = b => b[2] * b[3] >= .12 || b[2] * b[3] >= .025 &&
  b[0] + b[2] / 2 >= .18 && b[0] + b[2] / 2 <= .82 && b[1] + b[3] / 2 >= .15 && b[1] + b[3] / 2 <= .85;
const strong = b => b[2] * b[3] >= .15 && contains([.2, .15, .6, .7], { x: b[0] + b[2] / 2, y: b[1] + b[3] / 2 });
const incidentalEdge = ([x, y, width, height]) => !prominent([x, y, width, height]) &&
  (x < .08 || y < .08 || x + width > .92 || y + height > .92);

// Temporal target selection, not a detector or independent visual truth check.
export class GuideTarget {
  constructor(now = Date.now) { this.now = now; this.status = 'searching'; this.view = 0; this.preferred = false; }
  context() {
    return { target: this.current ? { id: this.current.id, subject: this.current.subject, box: modelBox(this.current.box) } : null,
      preferred: this.preferred, selection: this.selection ? { point: this.selection.point ? { x: Math.round(this.selection.point.x * 1000), y: Math.round(this.selection.point.y * 1000) } : undefined,
        hint: this.selection.hint } : null };
  }
  snapshot() {
    return { id: this.current?.id, status: this.status, preferred: this.preferred, box: this.status === 'locked' ? this.current?.box : undefined,
      validForMs: this.status === 'locked' ? Math.max(0, 5000 - (this.now() - this.current.at)) : 0,
      view: this.view, reason: this.reason, needsSelection: this.status === 'ambiguous' };
  }
  resetExitCheck() {
    this.absence = undefined;
    if (this.status === 'confirming_exit') { this.status = 'searching'; this.reason = undefined; }
  }
  move(moving) {
    this.resetExitCheck();
    this.view++; this.candidate = undefined; this.ambiguous = undefined; this.reason = undefined;
    if (moving) {
      this.selection = undefined; this.status = 'moving';
      if (!this.preferred) this.current = undefined;
    }
    else this.status = 'searching';
  }
  select({ point, hint }) {
    this.resetExitCheck();
    this.reason = undefined;
    this.selection = { point, hint, at: this.now() }; this.candidate = undefined;
    this.ambiguous = undefined; this.status = 'selecting'; this.view++;
  }
  observe(o) {
    let released = false;
    const reject = (status, ambiguous = false, reason = o.state === 'unknown' ? o.reason ?? status : status) => {
      this.reason = reason;
      this.status = status; this.candidate = undefined;
      const previous = this.ambiguous;
      const ask = ambiguous && previous && o.at > previous && o.at - previous < 10000;
      this.ambiguous = ambiguous ? o.at : undefined;
      return { accepted: false, ask: Boolean(ask), invalidate: true, released };
    };
    if (this.selection && this.now() - this.selection.at > 12000) {
      this.selection = undefined; this.preferred = false; this.view++;
      return { ...reject('searching', false, 'selection_expired'), resetView: true };
    }
    const explicit = Boolean(this.selection);
    const fresh = Number.isFinite(o.at) && this.now() - o.at <= 5000 && this.now() >= o.at;
    if (!explicit && this.preferred && fresh && o.previousAbsent && Number.isInteger(o.clip)) {
      const first = this.absence;
      if (first && (o.clip <= first.clip || o.at <= first.at)) return reject('confirming_exit', false, 'checking_previous_exit');
      if (!first || o.at - first.at > 5000) {
        this.absence = { clip: o.clip, at: o.at };
        return reject('confirming_exit', false, 'checking_previous_exit');
      }
      this.current = undefined; this.preferred = false; this.view++; released = true;
    }
    this.resetExitCheck();
    if (o.state !== 'matched') return reject(o.reason === 'ambiguous' ? 'ambiguous' : 'searching', o.reason === 'ambiguous');
    if (!fresh) return reject('searching');
    if (!o.boxes?.every(validBox) || o.boxes.length !== 2) return reject('searching', false, 'unstable_region');
    const regionOverlap = overlap(...o.boxes);
    if (regionOverlap < .15) return reject('searching', false, 'unstable_region');
    const box = o.boxes[1];
    if (this.selection?.point && !o.boxes.every(b => contains(b, this.selection.point))) return reject('selecting', true, 'point_outside_region');
    if (!explicit && !this.preferred && this.current && o.relation === 'same' && o.boxes.every(incidentalEdge)) {
      // The old object is still recognizable, but only a small edge fragment remains.
      this.current = undefined; this.view++; released = true;
      return reject('searching', false, 'target_at_edge');
    }
    if (!explicit && this.current && o.relation === 'new' && ['visible', 'uncertain'].includes(o.previous)) return reject('ambiguous', true);
    if (!explicit && this.current && o.relation === 'same' && o.previous !== 'visible') return reject('searching');
    const same = Boolean(this.current && o.relation === 'same' && o.previous === 'visible');
    if (!explicit && this.preferred && !same) return reject('searching', false, 'waiting_for_preferred');
    if (!same && !explicit && !o.boxes.every(prominent)) return reject('searching', false, 'not_prominent');
    if (!same && !explicit) {
      const confirmedReplacement = o.relation === 'new' && o.previousAbsent && regionOverlap >= .35;
      const fast = (!this.current || confirmedReplacement) && o.boxes.every(strong);
      const confirmed = this.candidate && this.candidate.subject === o.subject && o.at > this.candidate.at &&
        o.at - this.candidate.at <= 10000 && overlap(this.candidate.box, box) >= .35;
      if (!fast && !confirmed) {
        this.candidate = { subject: o.subject, box, at: o.at }; this.status = 'candidate'; this.reason = 'needs_confirmation';
        return { accepted: false, invalidate: true, released };
      }
    }
    const changed = !same || explicit;
    if (changed) this.current = { id: randomUUID(), subject: o.subject, box, at: o.at };
    else Object.assign(this.current, { box, at: o.at });
    if (explicit) this.preferred = Boolean(this.selection.hint);
    this.candidate = this.selection = this.ambiguous = undefined; this.status = 'locked'; this.reason = undefined;
    return { accepted: true, changed, released, subject: this.current.subject };
  }
}
