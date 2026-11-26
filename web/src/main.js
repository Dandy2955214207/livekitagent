import { Room, RoomEvent, Track, createLocalAudioTrack, createLocalVideoTrack } from 'livekit-client';
import './style.css';
import { createOfficialPanel } from './official.js';
import { createCallView } from './call-view.js';
import { createMobileTest } from './mobile-test.js';
import { BrowserTrialRoom, mountTrialInvite } from './browser-trial.js';

const $ = (id) => document.getElementById(id);
const isTrial = document.documentElement.hasAttribute('data-trial');
if (isTrial || new URLSearchParams(location.search).get('view') === 'call') document.documentElement.dataset.view = 'call';
const refreshTrialInvite = isTrial ? mountTrialInvite() : undefined;
const mobileTest = createMobileTest({ checkbox: $('mobile-test-enable'), mark: $('mobile-test-mark'), status: $('mobile-test-status') });
const callView = createCallView({ selectTarget: (event) => officialPanel.selectTarget(event), showTrialTools: isTrial });
const states = { idle: '尚未加入', connecting: '正在接通', ready: '已接通 · 可以说话',
  listening: '正在听你说', thinking: '正在思考', speaking: '正在回答', closed: '通话已结束', error: '连接异常' };
let room, microphone, call, timer, joining = false, ending = false;
let generation = 0;
let camera, cameraBusy = false, cameraAttempt = 0, facingMode = 'environment', active = false;
let cameraError;
const segments = new Map();
const officialPanel = createOfficialPanel(async (payload) => {
  if (!room || !call || ending) return;
  const playback = payload.type.startsWith('playback_');
  mobileTest.event(playback ? 'playback' : 'control', playback ? payload.type : payload.action ?? payload.type,
    playback ? { outputId: payload.outputId } : { taskId: payload.id ?? payload.taskId });
  await room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify(payload)),
    { reliable: true, topic: 'agent.official.control' });
}, {
  onTranscript: (event) => transcript([{ id: event.id, text: event.text, final: true }],
    { identity: event.role === 'user' ? call?.identity : 'official-agent' }),
  onState: (state) => { if (!ending) status(state); },
  onError: (message) => showError(message, '助手暂时无法处理，请稍后重试。'),
  onBlocked: requireAudioGesture,
  onTargetAvailable: callView?.setTargetAvailable,
});
function status(state, hint) {
  $('connection').dataset.state = state;
  $('status').textContent = states[state] ?? state;
  document.body.dataset.state = state;
  if (hint) $('hint').textContent = hint;
  renderCallView();
}
function renderCallView() {
  $('mobile-test-enable').disabled = Boolean(joining || active || ending);
  callView?.render({
    phase: ending ? 'ending' : active ? 'active' : joining ? 'connecting' : 'idle',
    state: document.body.dataset.state, cameraOn: Boolean(camera), cameraBusy,
  });
}
function showError(message, userMessage = message) {
  mobileTest.event('error', message);
  $('error').textContent = message;
  $('error').hidden = false;
  callView?.setNotice(userMessage);
}
function requireAudioGesture() {
  $('enable-audio').hidden = false;
  callView?.showControls();
}
function controls(connected) {
  active = connected;
  $('join').disabled = connected || joining || ending;
  $('mic').disabled = !connected;
  $('leave').disabled = !connected && !joining;
  cameraControls();
  renderCallView();
}
function cameraControls() {
  $('camera').disabled = !active || cameraBusy;
  $('switch-camera').disabled = !active || !camera || cameraBusy;
  $('camera').textContent = camera ? '关闭摄像头' : '开启摄像头';
  $('camera').setAttribute('aria-pressed', String(Boolean(camera)));
  renderCallView();
}
function detachCamera(track) {
  track?.stop();
  track?.detach().forEach((element) => element.remove());
  $('preview').replaceChildren();
  $('camera-placeholder').hidden = false;
}
async function stopCamera() {
  const old = camera; camera = undefined;
  detachCamera(old);
  $('video-status').textContent = '摄像头已关闭 · 不发送新画面';
  if (old && room) await room.localParticipant.unpublishTrack(old);
}
async function changeCamera(switching = false) {
  if (!active || cameraBusy) return;
  const attempt = ++cameraAttempt, callGeneration = generation, currentRoom = room;
  const wasOn = Boolean(camera);
  mobileTest.event('camera.request', switching ? '切换镜头' : wasOn ? '关闭摄像头' : '开启摄像头');
  cameraBusy = true; cameraControls();
  let track;
  try {
    if (wasOn) await stopCamera();
    if (wasOn && !switching) return;
    if (attempt !== cameraAttempt || callGeneration !== generation || !active) return;
    if (switching) facingMode = facingMode === 'environment' ? 'user' : 'environment';
    $('video-status').textContent = '正在请求摄像头权限…';
    track = await createLocalVideoTrack({ facingMode,
      resolution: { width: 640, height: 480, frameRate: 15 } });
    if (attempt !== cameraAttempt || callGeneration !== generation || !active) { track.stop(); return; }
    await currentRoom.localParticipant.publishTrack(track, { source: Track.Source.Camera,
      videoCodec: 'vp8', simulcast: false, videoEncoding: { maxBitrate: 500000, maxFramerate: 15 } });
    if (attempt !== cameraAttempt || callGeneration !== generation || !active) {
      track.stop(); await currentRoom.localParticipant.unpublishTrack(track); return;
    }
    camera = track;
    if (cameraError && $('error').textContent === cameraError) {
      $('error').hidden = true;
      callView?.setNotice('');
    }
    cameraError = undefined;
    const preview = track.attach(); preview.autoplay = true; preview.muted = true; preview.playsInline = true;
    preview.style.transform = facingMode === 'user' ? 'scaleX(-1)' : '';
    preview.dataset.mirrored = String(facingMode === 'user');
    $('preview').append(preview); $('camera-placeholder').hidden = true;
    await preview.play().catch(() => {});
    $('video-status').textContent = '摄像头已发布 · 等待服务端图像确认';
  } catch (error) {
    track?.stop();
    if (track) await currentRoom?.localParticipant.unpublishTrack(track).catch(() => {});
    if (attempt === cameraAttempt && callGeneration === generation) {
      camera = undefined; detachCamera(track);
      $('video-status').textContent = '摄像头未开启 · 语音仍可使用';
      cameraError = error.name === 'NotAllowedError' ? '未获得摄像头权限，允许后可重试。' : '摄像头开启失败，请检查设备是否被占用后重试。';
      showError(cameraError);
    }
  } finally {
    if (attempt === cameraAttempt) {
      mobileTest.event('camera.state', camera ? `已开启 · ${facingMode === 'user' ? '前置' : '后置'}` : '已关闭');
      cameraBusy = false; cameraControls();
    }
  }
}
async function request(path, options = {}) {
  const response = await fetch(path, { ...options, signal: AbortSignal.timeout(12000),
    headers: { 'Content-Type': 'application/json', ...(call ? { Authorization: `Bearer ${call.access_token}` } : {}), ...options.headers } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail ?? '本地服务请求失败。');
  return data;
}
function transcript(items, participant) {
  for (const segment of items) {
    if (!segment.text?.trim() && !segments.has(segment.id)) continue;
    $('empty')?.remove();
    let entry = segments.get(segment.id);
    if (!entry) {
      const row = document.createElement('article');
      const mine = participant?.identity === call?.identity;
      row.className = mine ? 'message user' : 'message agent';
      row.dataset.role = mine ? 'user' : 'assistant';
      const label = document.createElement('span'); label.textContent = mine ? '你' : 'Qwen';
      const text = document.createElement('p');
      row.append(label, text); $('messages').append(row);
      entry = { row, text }; segments.set(segment.id, entry);
      if (segments.size > 100) {
        const oldest = segments.keys().next().value;
        segments.get(oldest).row.remove(); segments.delete(oldest);
      }
    }
    entry.text.textContent = segment.text;
    entry.row.dataset.final = String(segment.final);
  }
  $('messages').scrollTop = $('messages').scrollHeight;
}
function handleOfficialEvent(event) {
  if (ending || !call || event.callId !== call.session_id || event.epoch !== 1) return;
  if (event.type === 'audio' && event.sequence === 0) mobileTest.event('audio.received', '收到首段音频', { outputId: event.outputId });
  if (event.type === 'audio_end') mobileTest.event('audio.end', '收到音频结束包', { outputId: event.outputId });
  if (event.type === 'audio_clear') mobileTest.event('audio.clear', '清除播放队列', { outputId: event.throughId || undefined });
  officialPanel.handle(event);
}
function handleVideoEvent(event) {
  if (!camera || ending) return;
  if (event.error) $('video-status').textContent = event.error;
  else if (event.enabled && event.last_sent_ago_ms !== null && event.last_sent_ago_ms < 2500 && event.latest) {
    $('video-status').textContent = `图像已送达 Qwen 连接 · 共 ${event.counts.sent} 帧 · 帧龄 ${Math.round(event.latest.age_ms)} ms`;
  } else $('video-status').textContent = '等待新画面 · 请暂缓当前画面提问';
}
async function prepareLocalRoom() {
  room = new Room({ adaptiveStream: false, dynacast: false });
  room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
    if (room?.canPlaybackAudio === false) requireAudioGesture();
    else $('enable-audio').hidden = true;
  });
  room.on(RoomEvent.DataReceived, (bytes, participant, kind, topic) => {
    if (topic === 'agent.official' && participant?.isAgent && !ending) {
      try { handleOfficialEvent(JSON.parse(new TextDecoder().decode(bytes))); } catch { /* Ignore malformed state. */ }
      return;
    }
    if (topic === 'agent.video' && participant?.isAgent && camera && !ending) {
      try { handleVideoEvent(JSON.parse(new TextDecoder().decode(bytes))); } catch { /* Ignore malformed status data. */ }
      return;
    }
    if (topic !== 'agent.status' || !participant?.isAgent) return;
    try { const event = JSON.parse(new TextDecoder().decode(bytes)); if (event.state && !ending) status(event.state); } catch { /* Ignore unrelated data. */ }
  });
  room.on(RoomEvent.Reconnecting, () => { mobileTest.event('room.reconnecting'); status('connecting', '网络正在重连，请稍候。'); });
  room.on(RoomEvent.Reconnected, () => { mobileTest.event('room.reconnected'); status('ready', '已恢复媒体连接。'); });
  room.on(RoomEvent.Disconnected, async () => {
    if (ending || !call) return;
    mobileTest.event('room.disconnected');
    const disconnectedCall = call;
    let hint = '连接已断开；再次加入会新建上下文。';
    try {
      const current = await request(`/api/sessions/${call.session_id}`);
      if (current.error) { hint = current.error; showError(hint, '连接已断开，请重新开始通话。'); }
    } catch { /* The API may also be unavailable. */ }
    if (!ending && call === disconnectedCall) await leave(hint);
  });
  await room.startAudio();
}
async function join() {
  if (joining || call) return;
  const attempt = ++generation;
  joining = true; ending = false; controls(false); $('error').hidden = true;
  callView?.setNotice('');
  status('connecting', '正在准备麦克风并接通 Agent…');
  try {
    // Resume within the join button's user activation, before any network await.
    const trialRoom = isTrial ? new BrowserTrialRoom({
      onEvent: (topic, event) => topic === 'agent.official' ? handleOfficialEvent(event) : handleVideoEvent(event),
      onDisconnect: (message) => { if (!ending) { showError(message); void leave(message); } },
    }) : undefined;
    if (trialRoom) room = trialRoom;
    await Promise.all([officialPanel.prepare().catch(() => {}), trialRoom?.startAudio()]);
    if (attempt !== generation) return;
    const capture = await mobileTest.start();
    if (attempt !== generation) return;
    if (!trialRoom) await prepareLocalRoom();
    const track = await createLocalAudioTrack({ echoCancellation: true, noiseSuppression: true, autoGainControl: true });
    if (attempt !== generation) { track.stop(); return; }
    microphone = track;
    mobileTest.event('microphone.ready');
    const created = trialRoom ? await trialRoom.connect(capture) :
      await request('/api/sessions', { method: 'POST', body: JSON.stringify(capture ? { test_capture: capture } : {}) });
    if (attempt !== generation) {
      if (trialRoom) trialRoom.disconnect();
      else await fetch(`/api/sessions/${created.session_id}`, { method: 'DELETE', headers: { Authorization: `Bearer ${created.access_token}` } });
      return;
    }
    if (trialRoom) mobileTest.event('room.connected');
    call = created;
    officialPanel.activate(call);
    trialRoom?.activate();
    document.querySelector('.intro').textContent = call.official_harness_capabilities?.proactive_enabled
      ? '使用官方核心进行实时对话和任务调度。开启摄像头后，可询问画面或交代观察任务。'
      : '使用官方核心进行实时对话。开启摄像头后，可以询问当前画面。';
    if (trialRoom) {
      await trialRoom.localParticipant.publishTrack(microphone, { source: Track.Source.Microphone });
      if (attempt === generation) status('ready', '可以开始说话。建议先用耳机测试。');
      return;
    }
    await room.connect(call.livekit_url, call.token);
    mobileTest.event('room.connected');
    const deadline = performance.now() + 45000;
    while (joining && !ending) {
      const current = await request(`/api/sessions/${call.session_id}`);
      if (current.state === 'error' || current.state === 'closed') throw new Error(current.error ?? 'Agent 已离开，请重新加入。');
      if (current.state === 'ready') break;
      if (performance.now() > deadline) throw new Error('Agent 接通超时，请检查服务是否启动。');
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    if (ending || !joining) return;
    await room.localParticipant.publishTrack(microphone, { source: Track.Source.Microphone });
    status('ready', '可以开始说话。建议先用耳机测试。');
    timer = setInterval(async () => {
      if (!call || ending) return;
      try {
        const current = await request(`/api/sessions/${call.session_id}`);
        if (current.state === 'error' || current.state === 'closed') {
          if (current.error) showError(current.error, '通话已中断，请重新开始。');
          await leave(current.error ?? '通话已结束。');
        }
      } catch { showError('本地服务暂时不可达，请挂断后检查服务。', '连接暂时不可用，请检查网络。'); }
    }, 3000);
  } catch (error) {
    if (attempt !== generation) return;
    const message = error.name === 'NotAllowedError' ? '未获得麦克风权限，请允许后重新加入。' : error.message;
    showError(message, error.name === 'NotAllowedError' ? '请允许使用麦克风，再试一次。' : '暂时无法接通，请稍后重试。');
    await leave(message); status('error');
    if (isTrial) void refreshTrialInvite();
  } finally {
    if (attempt === generation) { joining = false; controls(Boolean(call && room?.state === 'connected')); }
  }
}
async function leave(hint = '已挂断。再次加入会开始新的对话。') {
  if (ending) return;
  generation++;
  cameraAttempt++; cameraBusy = false; active = false; cameraControls();
  ending = true; joining = false; clearInterval(timer); timer = undefined;
  renderCallView();
  const previous = call; call = undefined;
  try {
    if (isTrial) room?.disconnect();
    await officialPanel.reset();
    detachCamera(camera); camera = undefined;
    $('video-status').textContent = '摄像头已关闭 · 不发送新画面';
    microphone?.stop(); microphone = undefined;
    if (room && !isTrial) await room.disconnect();
    room = undefined; $('enable-audio').hidden = true;
    if (previous && !isTrial) {
      const response = await fetch(`/api/sessions/${previous.session_id}`, { method: 'DELETE',
        headers: { Authorization: `Bearer ${previous.access_token}` }, signal: AbortSignal.timeout(5000) });
      if (!response.ok) showError('通话已断开；服务端正在清理房间。', '通话已结束。');
    }
  } catch { showError('通话已断开；服务端将自动清理遗留会话。', '通话已结束。'); }
  finally {
    mobileTest.end(hint);
    ending = false; $('mic').textContent = '关闭麦克风'; $('mic').setAttribute('aria-pressed', 'false');
    status('closed', hint); controls(false);
  }
}
$('join').addEventListener('click', join);
$('leave').addEventListener('click', () => {
  callView?.setNotice('');
  void leave();
});
$('camera').addEventListener('click', () => {
  const opening = active && !camera && !cameraBusy;
  void changeCamera();
  if (opening) callView?.requestFullscreen();
});
$('switch-camera').addEventListener('click', () => changeCamera(true));
$('mic').addEventListener('click', async () => {
  if (!microphone) return;
  $('mic').disabled = true;
  try {
    if (microphone.isMuted) await microphone.unmute(); else await microphone.mute();
    if (isTrial) await room.setMicrophone(!microphone.isMuted);
    $('mic').textContent = microphone.isMuted ? '开启麦克风' : '关闭麦克风';
    $('mic').setAttribute('aria-pressed', String(microphone.isMuted));
    mobileTest.event('microphone.change', microphone.isMuted ? '静音' : '取消静音');
  } catch { showError('麦克风切换失败，请重新加入。'); }
  finally { $('mic').disabled = !active; }
});
$('enable-audio').addEventListener('click', async () => {
  try { await officialPanel.prepare(); await room?.startAudio(); $('enable-audio').hidden = true; } catch { showError('声音未能播放，请检查浏览器声音设置。'); }
});
$('clear').addEventListener('click', () => { segments.clear(); $('messages').replaceChildren(); });
window.addEventListener('pagehide', () => {
  mobileTest.end('关闭或离开页面', true);
  if (call && !isTrial) {
    navigator.sendBeacon(`/api/sessions/${call.session_id}/end`, new Blob([
      JSON.stringify({ access_token: call.access_token })], { type: 'application/json' }));
  }
  microphone?.stop(); void room?.disconnect();
  camera?.stop();
  void officialPanel.reset();
});
