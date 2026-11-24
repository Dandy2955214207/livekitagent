// Real browser -> RTC -> manual harness -> Qwen -> RTC playout; fixed media only.
import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = path.join(root, '.runtime/h2-results'); await fs.mkdir(out, { recursive: true });
const guideOnly = process.env.H2_GUIDE_ONLY === '1';
const report = { status: 'failed', guideOnly, physicalDevicesUsed: false, turns: [], cases: [], pageErrors: [] };
const voices = {};
for (const [name, file] of Object.entries({ color: 'm0-fixtures/color.wav', after: 'h2-fixtures/after.wav', guide: 'h2-fixtures/guide.wav',
  done: 'h2-fixtures/done.wav', question: 'h2-fixtures/question.wav' })) {
  voices[name] = (await fs.readFile(path.join(root, '.runtime', file))).toString('base64');
}
const scenes = {};
for (const name of ['red', 'blue']) scenes[name] = (await fs.readFile(path.join(root, `.runtime/m0-fixtures/${name}.jpg`))).toString('base64');
const browser = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', headless: true,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--mute-audio'] });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
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
async function ask(name, expected) {
  const before = await assistants.count();
  await page.locator('#record').click();
  await page.waitForFunction(() => document.getElementById('record').textContent === '提交本次语音');
  await page.waitForTimeout(250);
  await page.evaluate((voice) => window.testSpeak(voice), voices[name]);
  await page.locator('#record').click();
  await page.waitForFunction((count) => document.querySelectorAll('.message.agent[data-final="true"]').length > count
    || ['closed', 'error'].includes(document.getElementById('connection').dataset.state)
    || document.getElementById('task-panel').dataset.notice?.includes('模型没有返回'), before, { timeout: 45000 });
  if (await assistants.count() <= before) throw new Error(await page.locator('#hint').innerText());
  const answer = await assistants.nth(before).locator('p').innerText();
  if (/submit_task_decision|speech_intent|APP_DATA_JSON|evidence_ids/.test(answer)) throw new Error('Internal control leaked to transcript');
  report.turns.push({ name, answer, expectedMatched: !expected || expected.test(answer) });
  if (expected && !expected.test(answer)) throw new Error(`Unexpected answer: ${answer}`);
  await page.waitForFunction(() => document.getElementById('task-panel').dataset.busy === 'false', null, { timeout: 45000 });
}
try {
  await page.goto('http://127.0.0.1:8000');
  await page.evaluate(() => window.testScene('red'));
  await page.locator('#join').click();
  await page.waitForFunction(() => !document.getElementById('record').disabled, null, { timeout: 45000 });
  if (!guideOnly) {
  await page.locator('#camera').click();
  await page.waitForFunction(() => document.getElementById('video-status').textContent.includes('画面已准备'));
  await page.waitForTimeout(1000);
  if (await assistants.count()) throw new Error('Unexpected automatic answer');
  report.cases.push('manual idle is quiet');
  await ask('color', /红/);
  await page.evaluate(() => window.testScene('blue'));
  await page.waitForTimeout(1200);
  await ask('color', /蓝/);
  report.cases.push('current red image and blue follow-up');
  await page.locator('#camera').click();
  }
  await ask('guide', /积木|摆|放/);
  await page.waitForFunction(() => document.getElementById('task-panel').dataset.status === 'waiting_action');
  if (await page.locator('#task-panel').getAttribute('data-step') !== '1') throw new Error('Guidance did not start at step one');
  await page.locator('#task-pause').click();
  await page.waitForFunction(() => document.getElementById('task-panel').dataset.status === 'paused');
  await page.locator('#task-resume').click();
  await page.waitForFunction(() => document.getElementById('task-panel').dataset.status === 'waiting_action');
  await ask('question', /积木|摆|放/);
  if (await page.locator('#task-panel').getAttribute('data-step') !== '1') throw new Error('Question skipped the step');
  await ask('done', /积木|摆|放/);
  if (await page.locator('#task-panel').getAttribute('data-step') !== '2') throw new Error('User confirmation did not advance one step');
  await page.locator('#task-cancel').click();
  await page.waitForFunction(() => document.getElementById('task-panel').dataset.status === 'cancelled');
  report.cases.push('guidance, pause/resume, follow-up, user-confirmed advance, end task');
  if (!await page.locator('#record').isEnabled()) throw new Error('Ending task ended the call');
  await ask('after', /笔|杯|书|纸|手机|钥匙|积木/);
  report.cases.push('ordinary voice QA after task ended');
  report.audioBytesReceived = await page.evaluate(async () => {
    let bytes = 0;
    for (const pc of window.testPcs) for (const item of (await pc.getStats()).values()) {
      if (item.type === 'inbound-rtp' && item.kind === 'audio') bytes += item.bytesReceived ?? 0;
    }
    return bytes;
  });
  if (!report.audioBytesReceived) throw new Error('Browser received no RTC audio');
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile horizontal overflow');
  await page.screenshot({ path: path.join(out, 'phone-width.png'), fullPage: true });
  if (report.pageErrors.length) throw new Error('Browser page errors');
  report.status = 'passed';
} catch (error) {
  report.error = error.message;
  await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true }).catch(() => {});
} finally {
  if (await page.locator('#leave').isEnabled()) await page.locator('#leave').click().catch(() => {});
  report.liveTracksAfterHangup = await page.evaluate(() => window.testTracks.filter((track) => track.readyState === 'live').length).catch(() => null);
  await fs.writeFile(path.join(out, `report-${Date.now()}.json`), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
  await browser.close();
}
process.exitCode = report.status === 'passed' ? 0 : 1;
