// Fixed synthetic speech and drawings -> real RTC -> Qwen -> RTC. No physical devices.
import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = path.join(root, '.runtime/showcase-results'); await fs.mkdir(out, { recursive: true });
const voicePath = process.env.SHOWCASE_VOICE_PATH || '.runtime/h2-fixtures/showcase.wav';
const voice = (await fs.readFile(path.resolve(root, voicePath))).toString('base64');
const qaOnly = process.env.AUTO_INPUT_QA_ONLY === '1';
const qaAfter = qaOnly || process.env.SHOWCASE_QA_AFTER === '1';
const voiceAfter = qaAfter ? (await fs.readFile(path.resolve(root,
  process.env.SHOWCASE_QA_VOICE_PATH || '.runtime/h2-fixtures/after.wav'))).toString('base64') : null;
const handheld = process.env.SHOWCASE_MOTION !== '0';
const motionProfile = { xPixels: 48, yPixels: 30, rotationDegrees: 4, scaleChange: .06, lightingChange: .08 };
const report = { status: 'failed', physicalDevicesUsed: false, syntheticDrawings: true, handheldSimulation: handheld,
  motionProfile, qaAfter, qaOnly, voicePath, measurementLabel: process.env.SHOWCASE_LABEL || 'unspecified',
  answers: [], cases: [], sceneTimings: [], pageErrors: [] };
const browser = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', headless: true,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--mute-audio'] });
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, ignoreHTTPSErrors: true });
page.on('pageerror', error => report.pageErrors.push(error.message));
page.setDefaultTimeout(45000);
await page.addInitScript(({ handheld, motionProfile }) => {
  window.testTracks = []; window.testPcs = [];
  window.testRemoteVoice = [];
  const monitored = new WeakSet();
  let monitorAudio;
  setInterval(() => {
    for (const element of document.querySelectorAll('#audio-elements audio')) {
      if (!element.srcObject || monitored.has(element)) continue;
      monitored.add(element);
      monitorAudio ??= new AudioContext(); void monitorAudio.resume();
      const source = monitorAudio.createMediaStreamSource(element.srcObject);
      const analyser = monitorAudio.createAnalyser(); analyser.fftSize = 512;
      const muted = monitorAudio.createGain(); muted.gain.value = 0;
      source.connect(analyser); analyser.connect(muted); muted.connect(monitorAudio.destination);
      const samples = new Float32Array(512);
      let lastVoice = -Infinity;
      setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
        if (rms > .008) {
          const now = performance.now();
          if (now - lastVoice > 250) window.testRemoteVoice.push(now);
          lastVoice = now;
        }
      }, 10);
    }
  }, 30);
  const OriginalPC = window.RTCPeerConnection;
  window.RTCPeerConnection = class extends OriginalPC { constructor(...args) { super(...args); window.testPcs.push(this); } };
  const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
  const ctx = canvas.getContext('2d'); let scene = 'cup', sceneStarted = performance.now();
  window.testScene = name => { scene = name; sceneStarted = performance.now(); draw(); return sceneStarted; };
  function draw() {
    ctx.fillStyle = '#e7e7df'; ctx.fillRect(0, 0, 640, 480);
    ctx.save();
    if (handheld && !['passing', 'sweeping'].includes(scene)) {
      ctx.translate(320 + Math.sin(performance.now() / 470) * motionProfile.xPixels,
        240 + Math.cos(performance.now() / 530) * motionProfile.yPixels);
      ctx.rotate(Math.sin(performance.now() / 870) * motionProfile.rotationDegrees * Math.PI / 180);
      const scale = 1 + Math.sin(performance.now() / 950) * motionProfile.scaleChange;
      ctx.scale(scale, scale); ctx.translate(-320, -240);
    }
    ctx.strokeStyle = '#292b30'; ctx.lineWidth = 12;
    if (scene === 'cup') {
      ctx.fillStyle = '#206fa0'; ctx.fillRect(190, 115, 240, 270);
      ctx.strokeRect(190, 115, 240, 270); ctx.beginPath(); ctx.ellipse(468, 232, 60, 72, 0, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = '#133549'; ctx.beginPath(); ctx.ellipse(310, 115, 120, 25, 0, 0, Math.PI * 2); ctx.fill();
    } else if (scene === 'keys') {
      ctx.strokeStyle = '#ac6e08'; ctx.lineWidth = 35;
      ctx.beginPath(); ctx.arc(230, 160, 75, 0, Math.PI * 2); ctx.moveTo(275, 215); ctx.lineTo(440, 385);
      ctx.moveTo(375, 320); ctx.lineTo(415, 280); ctx.moveTo(415, 365); ctx.lineTo(460, 320); ctx.stroke();
    } else if (scene === 'book') {
      ctx.fillStyle = '#ae2941'; ctx.fillRect(165, 65, 310, 350); ctx.strokeRect(165, 65, 310, 350);
      ctx.fillStyle = '#faf0cb'; ctx.fillRect(190, 385, 270, 22);
      ctx.fillStyle = '#e8ca88'; ctx.fillRect(195, 115, 240, 12); ctx.fillRect(195, 148, 200, 12);
      ctx.beginPath(); ctx.moveTo(185, 65); ctx.lineTo(185, 385); ctx.stroke();
    } else if (scene === 'sweeping') {
      const offset = (performance.now() - sceneStarted) / 1000 * 120;
      for (let index = 0; index < 50; index++) {
        ctx.fillStyle = `hsl(${index * 137 % 360} 35% ${25 + index * 19 % 50}%)`;
        ctx.fillRect(index * 61 - offset, 0, 45, 480);
      }
    } else {
      ctx.fillStyle = `hsl(${Math.floor(performance.now() / 180) * 113 % 360} 80% 45%)`;
      ctx.fillRect(0, 0, 640, 480);
    }
    ctx.restore();
    if (handheld && !['passing', 'sweeping'].includes(scene)) {
      const lighting = Math.sin(performance.now() / 720);
      ctx.fillStyle = lighting > 0 ? 'white' : 'black'; ctx.globalAlpha = Math.abs(lighting) * .08;
      ctx.fillRect(0, 0, 640, 480); ctx.globalAlpha = 1;
    }
  }
  setInterval(draw, 66); draw();
  let audio, destination;
  navigator.mediaDevices.getUserMedia = async constraints => {
    if (constraints.video) { const stream = canvas.captureStream(15); window.testTracks.push(...stream.getTracks()); return stream; }
    audio = new AudioContext({ sampleRate: 48000 }); await audio.resume(); destination = audio.createMediaStreamDestination();
    window.testSpeak = async b64 => {
      await audio.resume(); const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const source = audio.createBufferSource(); source.buffer = await audio.decodeAudioData(bytes.buffer);
      source.connect(destination); source.start(); await new Promise(resolve => { source.onended = resolve; });
    };
    window.testTracks.push(...destination.stream.getTracks()); return destination.stream;
  };
}, { handheld, motionProfile });
const assistants = page.locator('.message.agent[data-final="true"]');
async function idle() { await page.waitForFunction(() => document.getElementById('task-panel').dataset.busy === 'false'); }
async function speakTurn(voice) {
  const automatic = await page.locator('#record').isHidden();
  report.autoInput = automatic;
  if (!automatic) {
    await page.locator('#record').click();
    await page.waitForFunction(() => document.getElementById('record').textContent === '提交本次语音');
  }
  await page.waitForTimeout(250);
  await page.evaluate(voice => window.testSpeak(voice), voice);
  if (!automatic) await page.locator('#record').click();
}
async function nextAnswer(before, pattern) {
  await page.waitForFunction(count => document.querySelectorAll('.message.agent[data-final="true"]').length > count ||
    Boolean(document.getElementById('task-panel').dataset.notice), before, { timeout: 65000 });
  if (await assistants.count() <= before) throw new Error(await page.locator('#hint').innerText());
  const answer = await assistants.nth(before).locator('p').innerText();
  report.answers.push(answer);
  if (pattern && !pattern.test(answer)) throw new Error(`Unexpected subject: ${answer}`);
  if (/submit_task_decision|speech_intent|APP_DATA_JSON/.test(answer)) throw new Error('Internal output leak');
  await idle();
}
try {
  await page.goto(process.env.SHOWCASE_ORIGIN || 'http://127.0.0.1:8000'); await page.locator('#join').click();
  await page.waitForFunction(() => !document.getElementById('record').disabled);
  if (qaOnly) {
    if (!(await page.locator('#record').isHidden())) throw new Error('Automatic input not enabled');
    await page.waitForTimeout(1500);
    if (await assistants.count()) throw new Error('Silence triggered a response');
    await speakTurn(voiceAfter); await nextAnswer(0, /杯|笔|书|纸|手机|钥匙|积木/);
    report.cases.push('silence is quiet; ordinary voice QA needs no manual submit');
  } else {
  await speakTurn(voice); await nextAnswer(0);
  await page.waitForFunction(() => document.getElementById('task-step').textContent.includes('连续介绍'));
  report.cases.push('one spoken request starts continuous showcase');
  await page.evaluate(() => window.testScene('sweeping')); await page.locator('#camera').click();
  await page.waitForTimeout(5500);
  if (await assistants.count() !== 1) throw new Error('Passing motion triggered speech');
  report.cases.push('continuous camera sweep is quiet');
  for (const [scene, pattern] of [['cup', /杯/], ['keys', /钥匙|鑰匙/], ['book', /书|書|笔记本|本子/]]) {
    await page.waitForTimeout(400); // Let the previous decoded audio tail drain before starting the clock.
    const started = Date.now();
    const before = await assistants.count();
    const switchedAt = await page.evaluate(scene => window.testScene(scene), scene);
    await nextAnswer(before, pattern);
    const firstVoice = await page.evaluate(switchedAt => window.testRemoteVoice.find(time => time >= switchedAt), switchedAt);
    if (firstVoice === undefined) throw new Error('No decoded voice onset measured');
    report.sceneTimings.push({ scene, switchToFirstDecodedAudioMs: Math.round(firstVoice - switchedAt),
      switchToPlaybackEndMs: Date.now() - started });
    report.cases.push(`${scene} introduced without new speech`);
    if (scene === 'cup') {
      await page.waitForTimeout(15000);
      if (await assistants.count() !== before + 1) throw new Error('Still cup repeated');
      report.cases.push('held cup does not repeat across recheck');
    }
  }
  await page.locator('#task-pause').click();
  await page.waitForFunction(() => document.getElementById('task-panel').dataset.status === 'paused');
  const pausedCount = await assistants.count(); await page.evaluate(() => window.testScene('cup')); await page.waitForTimeout(3000);
  if (await assistants.count() !== pausedCount) throw new Error('Spoke while paused');
  await page.locator('#task-resume').click(); await page.waitForTimeout(5000);
  if (await assistants.count() !== pausedCount) throw new Error('Known cup repeated after resume');
  await page.locator('#task-cancel').click();
  await page.waitForFunction(() => document.getElementById('task-panel').dataset.status === 'cancelled');
  await page.evaluate(() => window.testScene('keys')); await page.waitForTimeout(4000);
  if (await assistants.count() !== pausedCount) throw new Error('Spoke after task ended');
  report.cases.push('pause/resume/end preserve silence for known or inactive scenes');
  if (qaAfter) {
    const before = await assistants.count();
    await speakTurn(voiceAfter);
    await nextAnswer(before, /杯|笔|书|纸|手机|钥匙|积木/);
    report.cases.push('ordinary submitted voice QA after ending task');
  }
  }
  report.audioBytesReceived = await page.evaluate(async () => {
    let bytes = 0; for (const pc of window.testPcs) for (const entry of (await pc.getStats()).values())
      if (entry.type === 'inbound-rtp' && entry.kind === 'audio') bytes += entry.bytesReceived ?? 0;
    return bytes;
  });
  if (!report.audioBytesReceived) throw new Error('No RTC audio received');
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile overflow');
  if (report.pageErrors.length) throw new Error('Page errors');
  report.userTranscriptRows = await page.locator('.message.user').count();
  if (report.userTranscriptRows > (qaAfter ? 2 : 1)) throw new Error('Synthetic observations appeared as user turns');
  await page.screenshot({ path: path.join(out, 'phone-width.png'), fullPage: true }); report.status = 'passed';
} catch (error) {
  report.error = error.message;
  await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true }).catch(() => {});
} finally {
  const canLeave = await page.locator('#leave').isEnabled({ timeout: 1000 }).catch(() => false);
  if (canLeave) await page.locator('#leave').click().catch(() => {});
  report.liveTracksAfterHangup = await page.evaluate(() => window.testTracks.filter(track => track.readyState === 'live').length).catch(() => null);
  await fs.writeFile(path.join(out, `report-${Date.now()}.json`), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report)); await browser.close();
}
process.exitCode = report.status === 'passed' ? 0 : 1;
