const $ = (id) => document.getElementById(id);
let stream, facing = 'environment', context;
const report = {
  userAgent: navigator.userAgent,
  secureContext: window.isSecureContext,
  getUserMedia: Boolean(navigator.mediaDevices?.getUserMedia),
  peerConnection: typeof RTCPeerConnection !== 'undefined',
  videoCodecs: typeof RTCRtpSender !== 'undefined'
    ? (RTCRtpSender.getCapabilities?.('video')?.codecs ?? []).map((codec) => codec.mimeType) : [],
  camera: 'untested', microphone: 'untested', preview: 'untested',
  audioContext: 'untested', audioHeard: false,
};
function render() {
  $('capabilities').replaceChildren();
  for (const [label, value] of [['安全页面', report.secureContext], ['采集接口', report.getUserMedia], ['WebRTC', report.peerConnection]]) {
    const term = document.createElement('dt'); term.textContent = label;
    const description = document.createElement('dd'); description.textContent = value ? '可用' : '不可用';
    $('capabilities').append(term, description);
  }
  $('result').textContent = JSON.stringify(report, null, 2);
}
function stop() {
  stream?.getTracks().forEach((track) => track.stop()); stream = undefined;
  $('preview').srcObject = null;
  $('stop').disabled = true; $('switch').disabled = true; $('start').disabled = false;
}
async function start() {
  stop(); $('notice').textContent = '';
  if (!report.secureContext || !report.getUserMedia) {
    $('notice').textContent = '当前页面不能调用采集接口。请确认使用 HTTPS，且浏览器认可该证书。'; return;
  }
  $('start').disabled = true;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: facing }, width: { ideal: 640 }, height: { ideal: 480 } },
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const video = stream.getVideoTracks()[0], audio = stream.getAudioTracks()[0];
    delete report.captureError; report.captureStopped = false;
    report.camera = video?.readyState ?? 'missing'; report.microphone = audio?.readyState ?? 'missing';
    const settings = video?.getSettings() ?? {};
    report.videoSettings = Object.fromEntries(['width', 'height', 'frameRate', 'facingMode'].filter((key) => key in settings).map((key) => [key, settings[key]]));
    $('preview').srcObject = stream; await $('preview').play();
    report.preview = 'playing';
    $('stop').disabled = false; $('switch').disabled = false;
    $('notice').textContent = '本地采集已开始。确认画面正常后，可切换摄像头，再停止采集。';
  } catch (error) {
    report.captureError = error.name;
    $('notice').textContent = `采集失败：${error.name}。请记录错误名称及是否出现权限询问。`;
    stop();
  }
  render();
}
$('start').addEventListener('click', start);
$('switch').addEventListener('click', () => { facing = facing === 'environment' ? 'user' : 'environment'; void start(); });
$('stop').addEventListener('click', () => { stop(); report.captureStopped = true; render(); $('notice').textContent = '已释放摄像头和麦克风。'; });
$('beep').addEventListener('click', async () => {
  try {
    context ??= new (window.AudioContext ?? window.webkitAudioContext)();
    await context.resume();
    const oscillator = context.createOscillator(), gain = context.createGain();
    oscillator.frequency.value = 440; gain.gain.value = 0.04;
    oscillator.connect(gain); gain.connect(context.destination);
    oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
    oscillator.start(); oscillator.stop(context.currentTime + 0.5);
    report.audioContext = context.state; $('heard').disabled = false;
  } catch (error) { report.audioError = error.name; }
  render();
});
$('heard').addEventListener('click', () => { report.audioHeard = true; render(); });
$('copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(JSON.stringify(report, null, 2)); $('notice').textContent = '已复制，可粘贴回聊天。'; }
  catch { $('notice').textContent = '浏览器未允许自动复制，请长按下方检测结果手动复制。'; }
});
window.addEventListener('pagehide', () => { stop(); void context?.close(); });
if (!report.secureContext) $('notice').textContent = '当前不是安全页面，请先完成 HTTPS 与证书配置，再开始采集。';
render();
