import captureModule from './capture.js?url&no-inline';

export function mountTrialInvite() {
  const dialog = document.getElementById('trial-invite');
  const form = document.getElementById('trial-invite-form');
  const code = document.getElementById('trial-code');
  const status = document.getElementById('trial-invite-status');
  const submit = form.querySelector('button');
  let accessCheck = 0;
  dialog.addEventListener('cancel', event => event.preventDefault());
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const attempt = ++accessCheck;
    submit.disabled = true;
    try {
      const response = await fetch('/trial/access', { method: 'POST', body: code.value.trim(), signal: AbortSignal.timeout(10000) });
      if (attempt !== accessCheck) return;
      if (!response.ok) {
        status.textContent = response.status === 401 ? '试用码无效，请核对后重试。' : '试用入口暂不可用，请刷新后重试。';
        return;
      }
      form.reset();
      dialog.close();
    } catch { status.textContent = '试用入口暂不可用，请检查网络后重试。'; }
    finally { submit.disabled = false; }
  });
  async function refresh() {
    const attempt = ++accessCheck;
    try {
      const response = await fetch('/trial/access', { signal: AbortSignal.timeout(5000) });
      if (attempt !== accessCheck) return;
      if (response.ok) { if (dialog.open) dialog.close(); return; }
    } catch { /* The dialog lets the user retry. */ }
    if (attempt === accessCheck && !dialog.open) dialog.showModal();
  }
  dialog.showModal();
  void refresh();
  return refresh;
}

const CAMERA_INTERVAL = 250;
const SEND_LIMIT = 96 * 1024;
const JPEG_LIMIT = 160 * 1024;

export class BrowserTrialRoom {
  constructor({ onEvent, onDisconnect }) {
    this.onEvent = onEvent;
    this.onDisconnect = onDisconnect;
    this.state = 'connecting';
    this.startup = [];
    this.devices = new Map();
    this.microphoneEnabled = true;
    this.localParticipant = {
      publishTrack: (track, options) => this.publishTrack(track, options),
      unpublishTrack: (track) => this.unpublishTrack(track),
      publishData: (bytes) => this.publishData(bytes),
    };
  }

  async startAudio() {
    if (!this.context) this.context = new AudioContext();
    await this.context.resume();
  }

  connect(capture) {
    const url = new URL('/v1/calls?client=browser', location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.socket = new WebSocket(url);
    this.socket.binaryType = 'arraybuffer';
    return new Promise((resolve, reject) => {
      this.resolveConnection = resolve;
      this.rejectConnection = reject;
      this.deadline = setTimeout(() => this.fail('通话连接超时，请重试。'), 50000);
      this.socket.onopen = () => this.send({ type: 'start', version: 1, ...(capture ? { test_capture: capture } : {}) });
      this.socket.onmessage = ({ data }) => this.receive(data);
      this.socket.onclose = () => this.fail('连接已断开，再次加入会新建通话。');
    });
  }

  send(message) {
    if (this.socket?.readyState !== WebSocket.OPEN) throw new Error('通话连接已断开。');
    this.socket.send(JSON.stringify(message));
  }

  dispatch(topic, event) {
    if (this.startup) {
      if (this.startup.length < 128) this.startup.push([topic, event]);
    } else this.onEvent(topic, event);
  }

  activate() {
    const buffered = this.startup;
    this.startup = undefined;
    for (const [topic, event] of buffered) {
      if (topic === 'agent.official' && !event.callId) event.callId = this.callId;
      this.onEvent(topic, event);
    }
  }

  receive(data) {
    if (this.state === 'disconnected') return;
    if (data instanceof ArrayBuffer) {
      if (data.byteLength < 11) return;
      const view = new DataView(data);
      if (view.getUint8(0) === 0x11) {
        this.dispatch('agent.official', { type: 'audio', epoch: 1, callId: this.callId,
          outputId: view.getUint32(1), sequence: view.getUint32(5),
          audio: btoa(String.fromCharCode(...new Uint8Array(data, 9))) });
      }
      return;
    }
    const event = JSON.parse(data);
    if (event.type === 'state' && event.state === 'ready') {
      this.callId = event.call_id;
      this.state = 'connected';
      clearTimeout(this.deadline);
      this.resolveConnection({ session_id: event.call_id, identity: event.identity, harness_mode: 'qwen_live_official',
        official_harness_capabilities: event.official_harness_capabilities });
    } else if (event.type === 'error' && event.fatal) this.fail(event.message);
    else if (event.type === 'state' && event.state === 'ended') this.fail('通话已结束。');
    else if (event.type === 'official_event') this.dispatch('agent.official', event.event);
    else if (event.type === 'video_event') this.dispatch('agent.video', event.event);
    else if (event.type === 'audio_clear' || event.type === 'audio_end') {
      this.dispatch('agent.official', { type: event.type, epoch: 1, callId: this.callId,
        outputId: event.output_id, throughId: event.through_id, sequence: event.sequence });
    } else {
      const pending = this.devices.get(event.type);
      if (pending && pending.enabled === event.enabled) pending.finish();
    }
  }

  publishData(bytes) {
    const event = JSON.parse(new TextDecoder().decode(bytes));
    if (event.type.startsWith('playback_')) {
      this.send({ type: event.type, output_id: event.outputId, sequence: event.sequence });
    } else this.send({ type: 'official_control', event });
  }

  setDevice(name, enabled) {
    const key = `${name}_state`;
    return new Promise((resolve, reject) => {
      const finish = error => {
        clearTimeout(timer);
        this.devices.delete(key);
        if (error) reject(error); else resolve();
      };
      const timer = setTimeout(() => finish(new Error('设备状态确认超时。')), 12000);
      this.devices.set(key, { enabled, finish });
      try { this.send({ type: `set_${name}`, enabled }); } catch (error) { finish(error); }
    });
  }

  async setMicrophone(enabled) {
    if (!enabled) this.microphoneEnabled = false;
    await this.setDevice('microphone', enabled);
    this.microphoneEnabled = enabled;
  }

  async publishTrack(track, { source }) {
    if (source === 'microphone') {
      const context = this.context;
      await context.audioWorklet.addModule(captureModule);
      if (this.state !== 'connected') throw new Error('通话连接已断开。');
      const capture = new AudioWorkletNode(context, 'trial-microphone', { channelCount: 1, numberOfOutputs: 0 });
      context.createMediaStreamSource(new MediaStream([track.mediaStreamTrack])).connect(capture);
      capture.port.onmessage = ({ data }) => {
        if (this.microphoneEnabled && this.canSendMedia()) this.socket.send(data);
      };
      return;
    }
    await this.setDevice('camera', true);
    if (this.state !== 'connected') throw new Error('通话连接已断开。');
    const video = document.createElement('video');
    video.playsInline = true;
    video.muted = true;
    video.srcObject = new MediaStream([track.mediaStreamTrack]);
    void video.play().catch(() => {});
    const canvas = document.createElement('canvas');
    this.camera = { track, video, canvas, drawing: canvas.getContext('2d') };
    this.frames = setInterval(() => this.captureFrame(), CAMERA_INTERVAL);
  }

  canSendMedia() {
    return this.state === 'connected' && this.socket?.readyState === WebSocket.OPEN && this.socket.bufferedAmount < SEND_LIMIT;
  }

  captureFrame() {
    const camera = this.camera;
    if (!camera || !camera.video.videoWidth || camera.encoding || !this.canSendMedia()) return;
    const { canvas, video, drawing } = camera;
    const scale = Math.min(1, 640 / Math.max(video.videoWidth, video.videoHeight));
    canvas.width = Math.max(2, 2 * Math.round(video.videoWidth * scale / 2));
    canvas.height = Math.max(2, 2 * Math.round(video.videoHeight * scale / 2));
    drawing.drawImage(video, 0, 0, canvas.width, canvas.height);
    camera.encoding = true;
    const send = blob => {
      camera.encoding = false;
      if (this.camera === camera && blob?.size <= JPEG_LIMIT && this.canSendMedia()) {
        this.socket.send(new Blob([new Uint8Array([2]), blob]));
      }
    };
    canvas.toBlob(blob => {
      if (blob?.size > JPEG_LIMIT && this.camera === camera) canvas.toBlob(send, 'image/jpeg', 0.5);
      else send(blob);
    }, 'image/jpeg', 0.72);
  }

  stopCamera() {
    clearInterval(this.frames);
    if (this.camera) this.camera.video.srcObject = null;
    this.camera = undefined;
  }

  async unpublishTrack(track) {
    if (this.camera?.track !== track) return;
    this.stopCamera();
    await this.setDevice('camera', false);
  }

  fail(message) {
    if (this.state === 'disconnected') return;
    const connected = this.state === 'connected';
    if (!connected) this.rejectConnection?.(new Error(message));
    this.disconnect();
    if (connected) this.onDisconnect(message);
  }

  disconnect() {
    if (this.state === 'disconnected') return;
    const connecting = this.state === 'connecting';
    this.state = 'disconnected';
    clearTimeout(this.deadline);
    this.stopCamera();
    const error = new Error('通话连接已关闭。');
    for (const pending of this.devices.values()) pending.finish(error);
    if (this.context) void this.context.close().catch(() => {});
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.send({ type: 'end' });
    }
    this.socket?.close();
    if (connecting) this.rejectConnection?.(error);
  }
}
