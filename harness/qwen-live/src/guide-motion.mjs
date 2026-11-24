// Local-only 12x8 RGB grid, sampled alongside the existing frame. Detect
// broad changes; a small foreground gesture is insufficient to stop narration.
function difference(a, b) {
  let changed = 0, sum = 0;
  for (let i = 0; i < a.length; i += 3) {
    const d = (Math.abs(a[i]-b[i])+Math.abs(a[i+1]-b[i+1])+Math.abs(a[i+2]-b[i+2])) / 3;
    if (d > 36) changed++; sum += d;
  }
  return changed >= 48 && sum / 96 >= 40;
}
export class GuideMotion {
  constructor(now = Date.now) { this.now = now; }
  reset() { this.last = this.anchor = undefined; this.moving = false; }
  sample(grid) {
    if (typeof grid !== 'string' || grid.length !== 384) return;
    const frame = Buffer.from(grid, 'base64'); if (frame.length !== 288) return;
    const cut = this.last && (difference(frame, this.last) || difference(frame, this.anchor));
    this.last = frame;
    if (!this.anchor || cut) this.anchor = frame;
    if (cut) this.changedAt = this.now();
    const moving = Boolean(cut || this.moving && this.now() - this.changedAt < 250);
    if (moving !== Boolean(this.moving)) { this.moving = moving; return moving ? 'moving' : 'stable'; }
  }
}
