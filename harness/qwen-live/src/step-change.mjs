// A local scheduling hint. The model still decides whether a step is complete.
export function hasLocalChange(a, b, minCells = 2) {
  const offsets = [0, 0, 0];
  for (let i = 0; i < a.length; i++) offsets[i % 3] += a[i] - b[i];
  let cells = 0;
  for (let i = 0; i < a.length; i += 3) {
    let difference = 0;
    for (let c = 0; c < 3; c++) difference += Math.abs(a[i + c] - b[i + c] - offsets[c] / 96);
    if (difference / 3 > 18) cells++;
  }
  return cells >= minCells;
}

export class StepChange {
  sample(grid, at) {
    if (typeof grid !== 'string' || grid.length !== 384) return;
    const frame = Buffer.from(grid, 'base64');
    if (frame.length !== 288) return;
    this.readyAt = this.baseline && this.last && hasLocalChange(frame, this.baseline) && !hasLocalChange(frame, this.last) ? at : undefined;
    this.baseline ??= frame;
    this.last = frame;
  }
  consume() { this.baseline = this.last; this.readyAt = undefined; }
}
