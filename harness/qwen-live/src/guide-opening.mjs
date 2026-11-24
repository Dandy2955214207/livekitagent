import { hasLocalChange } from './step-change.mjs';

const PROBE_DELAY_MS = 4000;
const RECHECK_MS = 6000;
const STABLE_MS = 250;
const UNSETTLED_GRACE_MS = 4000;

export function waitsForPresentation(transcript = '') {
  return /(?:接下来|稍后|等(?:我|一下|会儿)|一会儿|之后|when i|as i|i(?:'ll| will))/iu.test(transcript) &&
    /(?:展示|给你看|拿(?:出|来|给)|摆|放到|对准|拍给|show|present|hold up)/iu.test(transcript) &&
    !/(?:眼前这个|现在这个|已经摆好|这个已经|this (?:one|object) (?:here|now))/iu.test(transcript);
}

function decode(grid) {
  if (typeof grid !== 'string' || grid.length !== 384) return undefined;
  const frame = Buffer.from(grid, 'base64');
  return frame.length === 288 ? frame : undefined;
}

// Local readiness for the opening of a future-presentation guide. It does not
// identify objects or establish evidence used by the model.
export class GuideOpening {
  constructor(reference, at) {
    this.baseline = decode(reference);
    this.nextProbeAt = at + PROBE_DELAY_MS;
    this.version = 0;
  }

  sample(grid, at) {
    if (this.finished) return false;
    const frame = decode(grid);
    if (!frame) return false;
    this.baseline ??= frame;
    if (!this.last || hasLocalChange(frame, this.last, 4)) this.steadySince = at;
    this.changed = hasLocalChange(frame, this.baseline, 4);
    this.last = frame;
    this.sampleAt = at;
    if (this.changed && this.probed) {
      // The in-flight clip can finish, but its result belongs to the old view.
      this.version++;
      this.probed = false;
    }
    const firstChange = this.changed && !this.burstUsed;
    if (firstChange) this.burstUsed = true;
    return firstChange;
  }

  ready(at) {
    if (this.finished) return true;
    const settled = this.steadySince !== undefined && this.sampleAt - this.steadySince >= STABLE_MS;
    return (settled && this.changed) ||
      (at >= this.nextProbeAt && (!this.last || settled || at >= this.nextProbeAt + UNSETTLED_GRACE_MS));
  }

  requested(at) {
    if (this.finished) return;
    this.baseline = this.last ?? this.baseline;
    this.changed = false;
    this.probed = true;
    this.nextProbeAt = at + RECHECK_MS;
  }

  finish() { this.finished = true; }
}
