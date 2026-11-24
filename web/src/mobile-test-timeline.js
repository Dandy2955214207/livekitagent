export const isConversation = event => event.kind === 'turn.transcript' ||
  event.kind === 'response.text' && !event.data?.suppressed && Boolean(event.data?.text);

export function inTimelineMode(event, mode) {
  if (event.kind === 'mark' || isConversation(event)) return true;
  const media = /^(audio\.|camera\.(request|state)$|microphone\.|turn\.started$|frame\.bridge$|playback$)/.test(event.kind);
  return mode === (media ? 'media' : 'system');
}

const isAnchor = event => isConversation(event) ||
  ['official.ready', 'official.fatal', 'error', 'end'].includes(event.kind);
const anchorPriority = { 'turn.transcript': 0, 'response.text': 1, 'official.fatal': 0, error: 0 };

export function clockTime(milliseconds) {
  const seconds = Math.floor(Math.max(0, milliseconds) / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

const $ = id => document.getElementById(id);

function node(tag, cls, text) {
  const element = document.createElement(tag);
  if (cls) element.className = cls;
  if (text !== undefined) element.textContent = text;
  return element;
}

export function createTimeline({ describe, framePreview, onSelect }) {
  const axis = $('axis'), pointsLayer = $('axis-points'), ticks = $('axis-ticks'), popup = $('axis-preview');
  const media = $('media-mode'), system = $('system-mode'), pan = $('pan');
  let report, mode = 'media', zoom = 0, offset = 0, points = [], previewed, selected;
  const duration = () => Math.max(1000, (report.events.at(-1)?.time ?? report.startedAt) - report.startedAt);
  const span = () => Math.min(duration(), Math.max(1000, duration() / 2 ** zoom));
  const elapsed = event => Math.max(0, event.time - report.startedAt);
  const timeLabel = event => `${(elapsed(event) / 1000).toFixed(2)} 秒`;
  const maxOffset = () => Math.max(0, Math.ceil(duration() - span()));

  function clearPreview() {
    previewed = undefined;
    popup.hidden = true;
  }

  function preview(point) {
    if (previewed === point) return;
    previewed = point;
    const first = point.events[0], label = point.label ?? describe(first).title;
    const title = point.events.length > 1 ? `${point.events.length} 条邻近事件` : label;
    popup.replaceChildren(node('strong', null, title), node('span', 'preview-time', timeLabel(first)));
    const list = node('ul');
    for (const event of point.events.slice(0, 4)) {
      const { title, text } = describe(event);
      const value = point.events.length === 1 ? text : [title, text].filter(Boolean).join(' · ');
      if (value) list.append(node('li', null, value));
    }
    if (list.childElementCount) popup.append(list);
    const image = point.events.slice(0, 4).map(framePreview).find(Boolean);
    if (image) {
      const thumb = node('img');
      thumb.src = image;
      thumb.alt = '该事件关联的画面';
      thumb.onerror = () => thumb.remove();
      popup.append(thumb);
    }
    if (point.events.length > 4) popup.append(node('p', 'preview-time', '点击查看该时段的全部事件'));
    popup.hidden = false;
    const left = Math.max(-8, Math.min(point.x - popup.offsetWidth / 2, axis.clientWidth - popup.offsetWidth + 8));
    popup.style.left = `${left}px`;
  }

  function updateSelection() {
    for (const point of points) {
      const active = selected && point.events.some(event => selected.events.includes(event));
      if (active) point.button.setAttribute('aria-current', 'true');
      else point.button.removeAttribute('aria-current');
    }
    for (const button of $('mark-jumps').children) {
      button.setAttribute('aria-pressed', String(selected?.type === 'mark' && selected.label === button.textContent));
    }
  }

  function choose(point) {
    selected = point;
    clearPreview();
    updateSelection();
    onSelect(selected, mode);
  }

  function markPoint(event, x) {
    return { x, events: [event], type: 'mark', label: `问题 ${report.marks.indexOf(event) + 1}` };
  }

  function drawTicks(width, windowMs) {
    const desired = (windowMs / 1000) / Math.max(2, Math.floor(width / 88));
    const order = 10 ** Math.floor(Math.log10(Math.max(1, desired)));
    const step = [1, 2, 5, 10].find(factor => factor * order >= desired) * order * 1000;
    const marks = [offset];
    for (let ms = Math.ceil(offset / step) * step; ms < offset + windowMs; ms += step) {
      if (ms - marks.at(-1) > windowMs * 45 / width && offset + windowMs - ms > windowMs * 45 / width) marks.push(ms);
    }
    marks.push(offset + windowMs);
    ticks.replaceChildren(...marks.map((ms, index) => {
      const tick = node('span', `axis-tick${index === 0 ? ' first' : index === marks.length - 1 ? ' last' : ''}`, clockTime(ms));
      tick.style.left = `${100 * (ms - offset) / windowMs}%`;
      return tick;
    }));
  }

  function makePoints(width, windowMs) {
    const anchors = [], marks = [], bins = new Map();
    const right = offset + windowMs;
    for (const event of report.events) {
      const ms = elapsed(event);
      if (ms < offset || ms > right || !inTimelineMode(event, mode)) continue;
      const x = width * (ms - offset) / windowMs;
      if (event.kind === 'mark') marks.push(markPoint(event, x));
      else if (isAnchor(event)) anchors.push({ type: 'anchor', x, events: [event] });
      else {
        const key = Math.floor(x / 13);
        const bin = bins.get(key) ?? [];
        bin.push({ x, event });
        bins.set(key, bin);
      }
    }
    const dots = [...bins.values()].map(bin => ({
      type: 'dot', x: bin[Math.floor(bin.length / 2)].x, events: bin.map(({ event }) => event),
    }));
    const occupied = [[], []];
    const labelWidth = Math.min(width / 2 - 9, width < 550 ? 130 : 191);
    const priority = point => anchorPriority[point.events[0].kind] ?? 2;
    for (const point of [...anchors].sort((a, b) => priority(a) - priority(b) || a.x - b.x)) {
      const first = point.events[0];
      const left = Math.max(0, Math.min(point.x - labelWidth / 2, width - labelWidth));
      const order = first.kind === 'response.text' ? [1, 0] : [0, 1];
      const row = order.find(index => occupied[index].every(([a, b]) => left > b + 7 || left + labelWidth + 7 < a));
      if (row !== undefined) {
        point.card = { left, row, width: labelWidth };
        occupied[row].push([left, left + labelWidth]);
      }
    }
    return [...dots, ...anchors, ...marks].sort((a, b) => a.x - b.x);
  }

  function pointElement(point) {
    const first = point.events[0];
    const { title, text } = describe(first);
    const button = node('button', `axis-point ${point.type}`);
    point.button = button;
    button.type = 'button';
    button.style.left = `${point.x}px`;
    button.dataset.kind = first.kind;
    button.dataset.time = String(first.time);
    button.setAttribute('aria-label', `${point.label ?? (point.events.length > 1 ? `${point.events.length} 条邻近事件` : title)} · ${timeLabel(first)}`);
    button.tabIndex = -1;
    button.onclick = () => choose(point);
    button.onpointerenter = () => preview(point);
    button.onfocus = () => preview(point);
    if (point.type === 'mark' && point.showLabel) {
      const label = node('span', 'mark-label', point.label);
      label.style.left = `${(point.labelOffset ?? 0) + 7.5}px`;
      button.append(label);
    }
    if (point.card) {
      const card = node('span', 'point-card');
      card.style.left = `${point.card.left - point.x}px`;
      card.style.width = `${point.card.width}px`;
      card.dataset.row = point.card.row;
      card.append(node('span', 'point-card-title', `${clockTime(elapsed(first))}  ${title}`));
      if (text) card.append(node('span', 'point-card-text', text));
      button.append(card);
    }
    return button;
  }

  function draw() {
    if (!report || axis.clientWidth < 50) return;
    clearPreview();
    const width = axis.clientWidth, windowMs = span();
    drawTicks(width, windowMs);
    points = makePoints(width, windowMs);
    let lastMark = -Infinity;
    for (const point of points) {
      if (point.type !== 'mark') continue;
      const center = Math.max(22, Math.min(width - 22, point.x));
      if (center - lastMark < 48) continue;
      point.showLabel = true;
      point.labelOffset = center - point.x;
      lastMark = center;
    }
    pointsLayer.replaceChildren(...points.map(pointElement));
    if (points.length) (points.find(point => point.type === 'anchor') ?? points[0]).button.tabIndex = 0;
    $('zoom-out').disabled = zoom === 0;
    $('zoom-reset').disabled = zoom === 0;
    $('zoom-in').disabled = windowMs <= 1000 || zoom >= 8;
    $('pan-control').hidden = zoom === 0;
    pan.max = String(maxOffset());
    pan.value = String(offset);
    $('visible-range').textContent = `${clockTime(offset)} – ${clockTime(offset + windowMs)}`;
    updateSelection();
  }

  function setMode(value) {
    if (mode === value) return;
    mode = value;
    media.setAttribute('aria-pressed', String(mode === 'media'));
    system.setAttribute('aria-pressed', String(mode === 'system'));
    if (selected?.type === 'dot') selected = undefined;
    draw();
    onSelect(selected, mode);
  }

  function setZoom(value) {
    const center = selected ? elapsed(selected.events[0]) : offset + span() / 2;
    zoom = value;
    offset = Math.round(Math.max(0, Math.min(maxOffset(), center - span() / 2)));
    draw();
  }

  media.onclick = () => setMode('media');
  system.onclick = () => setMode('system');
  $('zoom-in').onclick = () => setZoom(zoom + 1);
  $('zoom-out').onclick = () => setZoom(Math.max(0, zoom - 1));
  $('zoom-reset').onclick = () => setZoom(0);
  pan.oninput = () => { offset = Number(pan.value); draw(); };
  axis.addEventListener('pointerleave', clearPreview);
  axis.addEventListener('pointermove', event => {
    if (event.pointerType === 'touch' || event.target.closest('.axis-point')) return;
    const rail = axis.querySelector('.axis-line').getBoundingClientRect();
    if (Math.abs(event.clientY - rail.top) > 20) { clearPreview(); return; }
    const x = event.clientX - rail.left;
    const nearest = points.reduce((best, point) => Math.abs(point.x - x) < Math.abs((best?.x ?? Infinity) - x) ? point : best, undefined);
    if (nearest && Math.abs(nearest.x - x) <= 20) preview(nearest);
    else clearPreview();
  });
  axis.addEventListener('keydown', event => {
    if (event.key === 'Escape') { clearPreview(); return; }
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    const index = points.findIndex(point => point.button === document.activeElement);
    const next = points[index + (event.key === 'ArrowRight' ? 1 : -1)];
    if (index < 0 || !next) return;
    event.preventDefault();
    points[index].button.tabIndex = -1;
    next.button.tabIndex = 0;
    next.button.focus();
  });
  axis.addEventListener('focusout', event => {
    if (!axis.contains(event.relatedTarget)) clearPreview();
  });
  new ResizeObserver(draw).observe(axis);

  return {
    load(value) {
      report = value;
      zoom = offset = 0;
      selected = undefined;
      const jump = $('mark-jumps');
      jump.replaceChildren(...report.marks.map((event, index) => {
        const button = node('button', 'jump-mark', `问题 ${index + 1}`);
        button.type = 'button';
        button.title = `跳到 ${timeLabel(event)}`;
        button.onclick = () => {
          const ms = elapsed(event);
          if (ms < offset || ms > offset + span()) {
            offset = Math.round(Math.max(0, Math.min(maxOffset(), ms - span() / 2)));
            draw();
          }
          choose(markPoint(event, axis.clientWidth * (ms - offset) / span()));
        };
        return button;
      }));
      jump.hidden = !report.marks.length;
      $('duration').textContent = `全程 ${clockTime(duration())}${report.marks.length ? ` · ${report.marks.length} 个问题标记` : ''}`;
      draw();
      onSelect(undefined, mode);
    },
    clear() { choose(undefined); },
  };
}
