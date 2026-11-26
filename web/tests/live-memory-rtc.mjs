// Paid, bounded deployed-service probe. Uses synthetic media and real browser
// playback receipts; never opens a physical camera or microphone.
import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import assert from 'node:assert/strict';
import { setTimeout as sleep } from 'node:timers/promises';

if (process.env.MEMORY_RTC_LIVE !== '1') throw new Error('Set MEMORY_RTC_LIVE=1 to use the configured paid provider');
const root = process.env.MEMORY_RTC_ROOT ?? 'http://127.0.0.1:8000';
const trial = process.env.OFFICIAL_TRIAL_ORIGIN;
assert.match(root, /^http:\/\/127\.0\.0\.1:\d+$/);
const workerLog = process.env.OFFICIAL_TEST_OUTPUT ? process.env.OFFICIAL_TEST_OUTPUT + '/worker.log' : '.runtime/logs/worker.log';
assert.equal((await (await fetch(root + '/api/health')).json()).active_sessions, 0, 'Do not interrupt an existing call');
const voice = (await fs.readFile('.runtime/m0-fixtures/call-memory.wav')).toString('base64');
const greeting = (await fs.readFile('.runtime/m0-fixtures/greeting.wav')).toString('base64');
const report = { status: 'failed', physicalDevices: false, browserPlayback: true, errors: [] };
let call;
const browser = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  headless: true, args: ['--use-fake-ui-for-media-stream', '--mute-audio'] });
const page = await browser.newPage();
page.on('pageerror', error => report.errors.push(error.message));
page.on('response', async response => {
  if (response.url() === root + '/api/sessions' && response.request().method() === 'POST' && response.ok()) call = await response.json();
});
if (trial) page.on('websocket', socket => socket.on('framereceived', ({ payload }) => {
  if (typeof payload !== 'string') return;
  const event = JSON.parse(payload);
  if (event.type === 'state' && event.state === 'ready') call = { ...event, session_id: event.call_id };
}));
const signals = async () => (await fs.readFile(workerLog, 'utf8')).split('\n').flatMap(line => {
  try {
    const msg = JSON.parse(line).message;
    if (call && msg.includes(call.session_id) && /^(official_memory|official_ready) /.test(msg)) return [JSON.parse(msg.slice(msg.indexOf(' ') + 1))];
  } catch { /* A log line may still be in flight. */ }
  return [];
});
try {
  await page.addInitScript(() => {
    window.memoryFixture = { color: 'red', contexts: [], timers: [] };
    navigator.mediaDevices.getUserMedia = async constraints => {
      if (constraints.video) {
        const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 240;
        const ctx = canvas.getContext('2d');
        const draw = () => {
          ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 320, 240);
          ctx.fillStyle = window.memoryFixture.color;
          ctx.beginPath(); ctx.arc(160, 120, 90, 0, Math.PI * 2); ctx.fill();
        };
        draw(); window.memoryFixture.timers.push(setInterval(draw, 100));
        return canvas.captureStream(10);
      }
      const context = new AudioContext(); await context.resume();
      const destination = context.createMediaStreamDestination();
      const oscillator = context.createOscillator(), gain = context.createGain();
      gain.gain.value = .00001; oscillator.connect(gain); gain.connect(destination); oscillator.start();
      window.memoryFixture.contexts.push(context);
      window.memoryFixture.speak = async base64 => {
        const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
        const source = context.createBufferSource();
        source.buffer = await context.decodeAudioData(bytes.buffer); source.connect(destination);
        source.start(); await new Promise(resolve => { source.onended = resolve; });
      };
      return destination.stream;
    };
  });
  await page.goto(trial ?? root);
  if (trial) {
    await page.locator('#trial-code').fill(process.env.HARMONY_TRIAL_CODE);
    await page.locator('#trial-invite-form button').click();
    await page.locator('#trial-invite').waitFor({ state: 'hidden' });
  }
  await page.locator('#join').click();
  await page.waitForFunction(() => !document.getElementById('camera').disabled, {}, { timeout: 50000 });
  assert.equal(call?.official_harness_capabilities.memory, 'call_visual');
  report.callId = call.session_id;
  // Confirm the synthetic microphone and VAD before the timed visual test.
  await page.evaluate(async audio => { await window.memoryFixture.speak(audio); }, greeting);
  await page.waitForFunction(() => document.getElementById('official-panel').dataset.playback === 'playback_completed', {}, { timeout: 20000 });
  await page.locator('#camera').click();
  const cameraStarted = Date.now();
  await sleep(4500);
  await page.evaluate(() => { window.memoryFixture.color = 'blue'; });
  await sleep(4500);
  await page.evaluate(() => { window.memoryFixture.color = 'green'; });
  // Ask immediately after the blue circle leaves, before the 30-frame
  // archive threshold, so the answer must use the question-time window.
  report.questionStartedMs = Date.now() - cameraStarted;
  await page.evaluate(async voice => { await window.memoryFixture.speak(voice); }, voice);
  await page.waitForFunction(() => {
    const text = document.getElementById('messages').textContent;
    return /红/.test(text) && /蓝/.test(text);
  }, {}, { timeout: 30000 });
  await page.waitForFunction(() => document.getElementById('official-panel').dataset.playback === 'playback_completed', {}, { timeout: 20000 });
  report.playbackCompleted = true;
  report.transcripts = await page.locator('#messages').textContent();
  report.signals = await signals();
  const recall = report.signals.find(row => row.phase === 'query' && row.submitted && row.reviewStatus === 'observed');
  assert.ok(recall?.frames > 0 && recall.records > 0);
  assert.ok(!report.signals.slice(0, report.signals.indexOf(recall)).some(row => row.phase === 'summary' && row.status === 'ready'));
  assert.equal(report.signals.some(row => row.status === 'degraded'), false);
  assert.deepEqual(report.errors, []);
  report.status = 'passed_live_visual_recall';
} catch (error) {
  report.failure = error.message;
  report.transcripts = await page.locator('#messages').textContent().catch(() => '');
} finally {
  report.signals = await signals();
  await browser.close();
  if (call && !trial) {
    const response = await fetch(root + '/api/sessions/' + call.session_id, {
      method: 'DELETE', headers: { Authorization: 'Bearer ' + call.access_token },
    });
    report.cleanup = response.status;
  }
  if (trial) {
    for (let i = 0; i < 50; i++) {
      if ((await (await fetch(root + '/api/health')).json()).active_sessions === 0) {
        report.cleanup = 'websocket_call_released';
        break;
      }
      await sleep(100);
    }
    if (!report.cleanup) report.status = 'failed_cleanup';
  }
  const path = '.runtime/memory-probes/rtc-' + randomUUID() + '.json';
  await fs.mkdir('.runtime/memory-probes', { recursive: true });
  await fs.writeFile(path, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ path, ...report }));
}
process.exitCode = report.status.startsWith('passed') ? 0 : 1;
