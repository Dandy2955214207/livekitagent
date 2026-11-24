// Real LiveKit + Worker + Qwen; only synthetic speech and approved public/test images.
import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = path.join(root, '.runtime/m2-results'); await fs.mkdir(out, { recursive: true });
const controlsOnly = process.env.M2_CONTROLS_ONLY === '1';
const report = { status: 'failed', controlsOnly, cases: [], rounds: [], physicalDevicesUsed: false, pageErrors: [] };
const scenes = {};
for (const name of ['dog', 'red', 'blue']) scenes[name] = (await fs.readFile(path.join(root, `.runtime/m0-fixtures/${name}.jpg`))).toString('base64');
const voices = await Promise.all([1, 2, 3, 4].map(async (n) => (await fs.readFile(path.join(root, `.runtime/m2-fixtures/round-${n}.wav`))).toString('base64')));
const browser = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', headless: true,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--mute-audio'] });
const context = await browser.newContext();
const page = await context.newPage();
page.on('pageerror', (error) => report.pageErrors.push(error.message));
await page.addInitScript(({ scenes }) => {
  window.testTracks = [];
  window.testPcs = [];
  const OriginalPC = window.RTCPeerConnection;
  window.RTCPeerConnection = class extends OriginalPC { constructor(...args) { super(...args); window.testPcs.push(this); } };
  const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
  const draw = canvas.getContext('2d'); let image;
  window.testScene = async (name) => {
    image = new Image(); image.src = `data:image/jpeg;base64,${scenes[name]}`;
    await image.decode(); draw.drawImage(image, 0, 0, 640, 480);
  };
  setInterval(() => { if (image?.complete) draw.drawImage(image, 0, 0, 640, 480); }, 66);
  let audio, destination;
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    if (constraints.video) {
      if (window.testDenyCamera) throw new DOMException('Test denial', 'NotAllowedError');
      if (window.testDelayCamera) await new Promise((resolve) => { window.testReleaseCamera = resolve; });
      const stream = canvas.captureStream(15); window.testTracks.push(...stream.getTracks()); return stream;
    }
    audio = new AudioContext({ sampleRate: 48000 }); await audio.resume();
    destination = audio.createMediaStreamDestination();
    window.testSpeak = async (b64) => {
      await audio.resume();
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const buffer = await audio.decodeAudioData(bytes.buffer);
      const source = audio.createBufferSource(); source.buffer = buffer; source.connect(destination); source.start();
      await new Promise((resolve) => { source.onended = resolve; });
    };
    window.testTracks.push(...destination.stream.getTracks()); return destination.stream;
  };
}, { scenes });

const assistants = page.locator('.message.agent[data-final="true"]');
async function ask(index, expected) {
  const before = await assistants.count();
  await page.evaluate((voice) => window.testSpeak(voice), voices[index-1]);
  await page.waitForFunction((count) => document.querySelectorAll('.message.agent[data-final="true"]').length > count
    || document.getElementById('connection').dataset.state === 'closed', before, { timeout: 30000 });
  if (await assistants.count() <= before) throw new Error(await page.locator('#hint').innerText());
  const answer = await assistants.nth(before).locator('p').innerText();
  report.rounds.push({ index, answer, passed: expected.test(answer) });
  if (!expected.test(answer)) throw new Error(`Round ${index} answer did not match expectation: ${answer}`);
  await page.waitForTimeout(1200);
}
try {
  await page.goto('http://127.0.0.1:8000');
  await page.evaluate(() => window.testScene('dog'));
  await page.getByRole('button', { name: /加入通话/ }).click();
  await page.waitForFunction(() => document.getElementById('camera').disabled === false, null, { timeout: 45000 });
  // Permission failure must leave the same voice call usable.
  await page.evaluate(() => { window.testDenyCamera = true; });
  await page.getByRole('button', { name: '开启摄像头', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '未获得摄像头权限' }).waitFor();
  if (!(await page.getByRole('button', { name: '关闭麦克风', exact: true }).isEnabled())) throw new Error('Camera denial broke voice controls');
  report.cases.push('camera permission denial leaves voice call usable');
  await page.evaluate(() => { window.testDenyCamera = false; });
  await page.getByRole('button', { name: '开启摄像头', exact: true }).click();
  await page.locator('#preview video').waitFor();
  await page.locator('#video-status').filter({ hasText: '图像已送达' }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(3000);
  if (await assistants.count()) throw new Error('Video-only input triggered an unsolicited answer');
  report.cases.push('camera publishes over real WebRTC, image-only input does not trigger answers');
  if (!controlsOnly) {
    await ask(1, /狗|犬/);
    await page.evaluate(() => window.testScene('red'));
    await page.waitForTimeout(1800);
    await ask(2, /红/);
    await page.evaluate(() => window.testScene('blue'));
    // Deliberately ask immediately after changing the scene; no frame wait here.
    await ask(3, /蓝/);
    report.cases.push('public dog image and red-to-blue current-scene update');
  }
  await page.getByRole('button', { name: '切换前后镜头', exact: true }).click();
  await page.waitForFunction(() => !document.getElementById('switch-camera').disabled);
  const tracks = await page.evaluate(() => window.testTracks.map((t) => ({ kind: t.kind, state: t.readyState })));
  if (tracks.filter((t) => t.kind === 'video' && t.state === 'live').length !== 1) throw new Error('Switch leaked a camera track');
  report.cases.push('switch releases previous camera before acquiring replacement');
  await page.getByRole('button', { name: '关闭摄像头', exact: true }).click();
  await page.waitForFunction(() => !document.getElementById('camera').disabled);
  if (await page.locator('#preview video').count()) throw new Error('Preview survived camera off');
  await page.waitForTimeout(2000);
  const inboundAudio = () => page.evaluate(async () => {
    let bytes = 0;
    for (const pc of window.testPcs) for (const s of (await pc.getStats()).values()) {
      if (s.type === 'inbound-rtp' && s.kind === 'audio') bytes += s.bytesReceived;
    }
    return bytes;
  });
  const beforeAudio = await inboundAudio();
  // This case checks that voice still flows; exact phrase obedience is a
  // separate model behavior and is not required for transport acceptance.
  await ask(4, /.+/u);
  report.cameraOffAudioBytes = (await inboundAudio()) - beforeAudio;
  if (report.cameraOffAudioBytes <= 0) throw new Error('No new voice audio after camera off');
  report.cases.push('camera off releases capture and voice answer still works');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(out, 'mobile.png'), fullPage: true });
  if (!(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))) throw new Error('Mobile layout overflow');
  report.cases.push('390px layout fits');
  report.media = await page.evaluate(async () => {
    const result = [];
    for (const pc of window.testPcs) {
      for (const stat of (await pc.getStats()).values()) {
        if (['outbound-rtp', 'inbound-rtp'].includes(stat.type)) result.push({ type: stat.type, kind: stat.kind,
          bytesSent: stat.bytesSent, bytesReceived: stat.bytesReceived, framesEncoded: stat.framesEncoded, packetsLost: stat.packetsLost });
      }
    }
    return result;
  });
  await page.evaluate(() => { window.testDelayCamera = true; });
  await page.getByRole('button', { name: '开启摄像头', exact: true }).click();
  await page.waitForFunction(() => typeof window.testReleaseCamera === 'function');
  await page.getByRole('button', { name: '挂断', exact: true }).click();
  await page.evaluate(() => window.testReleaseCamera());
  await page.waitForTimeout(1000);
  const liveTracks = await page.evaluate(() => window.testTracks.filter((t) => t.readyState === 'live').length);
  if (liveTracks) throw new Error('Late camera permission resolution leaked live capture after hangup');
  report.cases.push('hangup during camera acquisition stops late tracks');
  if (report.pageErrors.length) throw new Error('Browser page errors occurred');
  report.status = 'passed';
} catch (error) {
  report.error = error.message;
  report.screen = await page.locator('#hint').innerText().catch(() => '');
  console.error(error.message);
}
finally {
  await page.getByRole('button', { name: '挂断', exact: true }).click({ timeout: 1000 }).catch(() => {});
  await browser.close();
  await fs.writeFile(path.join(out, controlsOnly ? 'controls-report.json' : 'smoke-report.json'), JSON.stringify(report, null, 2));
}
console.log(JSON.stringify(report));
process.exitCode = report.status === 'passed' ? 0 : 1;
