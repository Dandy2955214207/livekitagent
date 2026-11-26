import './call-view.css';

export function createCallView({ selectTarget, showTrialTools = false }) {
  if (document.documentElement.dataset.view !== 'call') return;

  const panel = document.querySelector('.call-panel');
  const preview = document.getElementById('preview');
  const avatar = document.getElementById('call-avatar');
  const restore = document.getElementById('call-restore');
  const hide = document.getElementById('call-hide');
  const options = document.getElementById('call-options');
  const more = document.getElementById('call-more');
  const morePanel = document.getElementById('call-more-panel');
  const select = document.getElementById('call-select-subject');
  const details = document.getElementById('call-details');
  const openDetails = document.getElementById('call-open-details');
  const selectHint = document.getElementById('call-select-hint');
  const notice = document.getElementById('call-notice');
  const status = document.getElementById('call-status');
  const leave = document.getElementById('leave');
  const cameraControls = document.querySelector('.camera-controls');
  let drag, targetAvailable = false, cameraReady = false;

  panel.dataset.callPhase = 'idle';
  avatar.hidden = false;
  document.getElementById('join').textContent = '开始通话';
  cameraControls.prepend(document.getElementById('mic'), document.getElementById('record'));
  if (showTrialTools) {
    details.append(document.getElementById('official-panel'), document.querySelector('.conversation'));
    openDetails.hidden = false;
    openDetails.addEventListener('click', () => { showMenu(false); details.showModal(); });
  }

  function showControls() {
    delete panel.dataset.immersive;
    restore.hidden = true;
  }
  function showMenu(open) {
    morePanel.hidden = !open;
    more.setAttribute('aria-expanded', String(open));
  }
  function updateOptions() {
    select.hidden = !cameraReady || !targetAvailable;
    options.hidden = select.hidden && !(showTrialTools && panel.dataset.callPhase === 'active');
    if (select.hidden) selectHint.hidden = true;
    if (options.hidden) {
      showMenu(false);
      selectHint.hidden = true;
    }
  }
  function hideControls() {
    if (!preview.querySelector('video')) return;
    showMenu(false);
    selectHint.hidden = true;
    panel.dataset.immersive = 'true';
    restore.hidden = false;
    if (document.activeElement === hide) restore.focus({ preventScroll: true });
  }
  preview.addEventListener('click', (event) => {
    if (!selectHint.hidden) {
      if (selectTarget(event)) selectHint.hidden = true;
      return;
    }
    hideControls();
  });
  more.addEventListener('click', () => {
    selectHint.hidden = true;
    showMenu(morePanel.hidden);
  });
  select.addEventListener('click', () => {
    showMenu(false);
    selectHint.hidden = false;
    more.focus({ preventScroll: true });
  });
  panel.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || (morePanel.hidden && selectHint.hidden)) return;
    event.preventDefault();
    showMenu(false);
    selectHint.hidden = true;
    more.focus({ preventScroll: true });
  });
  hide.addEventListener('click', hideControls);
  restore.addEventListener('click', (event) => {
    showControls();
    if (event.detail === 0) leave.focus({ preventScroll: true });
  });

  function placeAvatar(x, y) {
    const bounds = panel.getBoundingClientRect();
    const maxY = cameraControls.getBoundingClientRect().top - bounds.top - avatar.offsetHeight - 12;
    avatar.style.left = `clamp(var(--call-left), ${x}px, calc(100% - var(--call-right) - ${avatar.offsetWidth}px))`;
    avatar.style.top = `clamp(var(--call-top), ${y}px, ${maxY}px)`;
  }
  avatar.addEventListener('pointerdown', (event) => {
    if (drag || !event.isPrimary || event.button !== 0 || !preview.querySelector('video')) return;
    const box = avatar.getBoundingClientRect();
    drag = { id: event.pointerId, x: event.clientX - box.left, y: event.clientY - box.top };
    avatar.setPointerCapture(event.pointerId);
  });
  avatar.addEventListener('pointermove', (event) => {
    if (drag?.id !== event.pointerId) return;
    const bounds = panel.getBoundingClientRect();
    placeAvatar(event.clientX - bounds.left - drag.x, event.clientY - bounds.top - drag.y);
  });
  avatar.addEventListener('lostpointercapture', () => { drag = undefined; });
  window.addEventListener('resize', () => {
    if (!avatar.style.left || !preview.querySelector('video') || panel.dataset.immersive) return;
    placeAvatar(avatar.offsetLeft, avatar.offsetTop);
  });

  function setNotice(message) {
    notice.textContent = message;
    notice.hidden = !message;
    if (message) showControls();
  }

  return {
    render({ phase, state, cameraOn, cameraBusy }) {
      const previous = panel.dataset.callPhase;
      panel.dataset.callPhase = phase;
      if (phase !== 'active' && details.open) details.close();
      cameraReady = phase === 'active' && cameraOn && !cameraBusy;
      updateOptions();
      if (!cameraOn) {
        showControls();
        avatar.style.removeProperty('left');
        avatar.style.removeProperty('top');
      }
      hide.hidden = !cameraOn;
      leave.textContent = phase === 'connecting' ? '取消' : '挂断';
      status.textContent = phase === 'ending' ? '正在结束通话…' : phase === 'connecting' ? '正在接通…' : ({
        connecting: '正在恢复连接…',
        listening: '正在听',
        thinking: '正在思考',
        speaking: '正在回答',
        ready: '已接通',
        closed: '通话已结束',
      })[state] ?? '';
      if (phase === 'idle' && previous !== 'idle' && document.fullscreenElement === panel) {
        void document.exitFullscreen().catch(() => {});
      }
    },
    setTargetAvailable(available) {
      targetAvailable = available;
      updateOptions();
    },
    setNotice,
    showControls,
    requestFullscreen() {
      if (!matchMedia('(pointer: coarse)').matches || !document.fullscreenEnabled ||
          document.fullscreenElement || !panel.requestFullscreen) return;
      void panel.requestFullscreen().then(() => {
        if (panel.dataset.callPhase === 'idle' && document.fullscreenElement === panel) {
          return document.exitFullscreen();
        }
      }).catch(() => {});
    },
  };
}
