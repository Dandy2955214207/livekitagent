export function imageRect(width, height, videoWidth, videoHeight, fit = 'contain') {
  if (![width, height, videoWidth, videoHeight].every(n => Number.isFinite(n) && n > 0)) return null;
  const scale = fit === 'cover' ? Math.max(width / videoWidth, height / videoHeight) :
    Math.min(width / videoWidth, height / videoHeight);
  const w = videoWidth * scale, h = videoHeight * scale;
  return { x: (width - w) / 2, y: (height - h) / 2, width: w, height: h };
}
export function imagePoint(x, y, rect, mirrored = false) {
  if (!rect || x < rect.x || y < rect.y || x > rect.x + rect.width || y > rect.y + rect.height) return null;
  const px = (x - rect.x) / rect.width;
  return { x: mirrored ? 1 - px : px, y: (y - rect.y) / rect.height };
}
export function boxRect(box, rect, mirrored = false) {
  return { left: rect.x + (mirrored ? 1 - box[0] - box[2] : box[0]) * rect.width,
    top: rect.y + box[1] * rect.height, width: box[2] * rect.width, height: box[3] * rect.height };
}

export function createGuideTarget(select, onAvailability) {
  const preview = document.getElementById('preview'), box = document.getElementById('guide-target-box');
  const label = document.getElementById('guide-target-label'), hint = document.getElementById('guide-target-hint');
  const marker = document.createElement('span'); marker.className = 'guide-selection-point'; marker.hidden = true;
  box.parentElement.append(marker);
  let tapped;
  let scene, expires = 0, timer;
  const enabled = () => scene?.mode === 'guide' && scene.status === 'watching';
  function geometry() {
    const video = preview.querySelector('video');
    if (!video || !video.videoWidth || video.readyState < 2) return null;
    const bounds = preview.getBoundingClientRect();
    return { bounds, rect: imageRect(bounds.width, bounds.height, video.videoWidth, video.videoHeight, getComputedStyle(video).objectFit), mirrored: video.dataset.mirrored === 'true' };
  }
  function render() {
    const geo = geometry(), target = scene?.target;
    marker.hidden = !enabled() || !geo || scene.moving || !tapped || performance.now() > tapped.until;
    if (!marker.hidden) {
      marker.style.left = `${geo.rect.x + (geo.mirrored ? 1 - tapped.x : tapped.x) * geo.rect.width}px`;
      marker.style.top = `${geo.rect.y + tapped.y * geo.rect.height}px`;
    }
    let message = '';
    if (target?.status === 'selecting') message = '正在确认指定的物品，请保持镜头稳定。';
    else if (target?.needsSelection) message = onAvailability ? '可从「更多功能」选择主体。' : '不确定您想听哪一件，请点选画面中的物品。';
    else if (!onAvailability) message = '框线表示最近确认的讲解主体；选错时可点一下要介绍的物品。';
    hint.hidden = !enabled() || !geo || !message;
    if (hint.textContent !== message) hint.textContent = message;
    box.hidden = !enabled() || !geo || scene.moving || target?.status !== 'locked' || !target.box || performance.now() >= expires;
    if (box.hidden) return;
    const placed = boxRect(target.box, geo.rect, geo.mirrored);
    for (const [key, value] of Object.entries(placed)) box.style[key] = `${value}px`;
    label.textContent = scene.subject || '讲解主体';
  }
  function selectAt(event) {
    const geo = geometry(); if (!enabled() || !geo || scene.moving) return false;
    const point = imagePoint(event.clientX - geo.bounds.left, event.clientY - geo.bounds.top, geo.rect, geo.mirrored);
    if (!point) return false;
    tapped = { ...point, until: performance.now() + 3000 }; expires = 0;
    render(); select(point, scene.target?.view);
    return true;
  }
  if (!onAvailability) preview.addEventListener('click', selectAt);
  if (typeof ResizeObserver === 'function') new ResizeObserver(render).observe(preview);
  else window.addEventListener('resize', render);
  new MutationObserver(() => { expires = 0; tapped = undefined; render(); }).observe(preview, { childList: true });
  return {
    selectAt,
    update(state) {
      scene = state; expires = performance.now() + Math.max(0, Math.min(5000, state.target?.validForMs ?? 0));
      onAvailability?.(enabled());
      if (state.target?.status === 'locked' || state.moving || !enabled()) tapped = undefined;
      if (enabled() && !timer) timer = setInterval(render, 250);
      if (!enabled()) { clearInterval(timer); timer = undefined; }
      render();
    },
    reset() { scene = undefined; tapped = undefined; expires = 0; onAvailability?.(false); clearInterval(timer); timer = undefined; render(); },
  };
}
