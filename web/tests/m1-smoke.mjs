import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const rounds = Number(process.argv[2] ?? 1);
const out = path.join(root, '.runtime/m1-results');
await fs.mkdir(out, { recursive: true });
try {
  const previous = path.join(out, `browser-${rounds}-report.json`);
  await fs.access(previous);
  const history = path.join(out, 'history'); await fs.mkdir(history, { recursive: true });
  await fs.copyFile(previous, path.join(history, `${Date.now()}-browser-${rounds}-report.json`));
} catch { /* First attempt has no previous report. */ }
const report = { rounds, started: new Date().toISOString(), status: 'failed', physicalMicrophoneUsed: false };
let credentials;
const errors = [];
const browser = await chromium.launch({
  executablePath: process.env.M1_BROWSER_PATH ?? 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  headless: true,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    `--use-file-for-fake-audio-capture=${path.join(root, `.runtime/m1-fixtures/browser-${rounds}.wav`)}`,
    '--autoplay-policy=no-user-gesture-required'],
});
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await context.newPage();
await page.addInitScript(() => {
  window.__testPeers = [];
  const Native = window.RTCPeerConnection;
  window.RTCPeerConnection = class extends Native {
    constructor(...args) { super(...args); window.__testPeers.push(this); }
  };
});
page.on('pageerror', (error) => errors.push(error.message));
page.on('response', async (response) => {
  if (response.url().endsWith('/api/sessions') && response.request().method() === 'POST' && response.status() === 201) {
    credentials = await response.json();
  }
});
try {
  await page.goto(process.env.M1_WEB_URL ?? 'http://127.0.0.1:8000', { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: /加入通话/ }).click();
  await page.waitForFunction(() => ['ready', 'listening', 'thinking', 'speaking'].includes(document.querySelector('#connection').dataset.state), { timeout: 45000 });
  console.log('Browser joined; waiting for fixed audio responses.');
  const terms = rounds === 1 ? ['第一轮成功'] : ['第一轮成功', '蓝鲸七号', '蓝鲸七号', '第四轮成功', '第五轮成功', '第六轮成功', '第七轮成功', '第八轮成功', '第九轮成功', '第十轮成功'];
  for (let index = 0; index < terms.length; index++) {
    await page.waitForFunction(({ term, count }) => {
      const digits = { 一: '1', 二: '2', 三: '3', 四: '4', 五: '5', 六: '6', 七: '7', 八: '8', 九: '9', 十: '10' };
      const normalize = (value) => value.replace(/[一二三四五六七八九十]/g, (char) => digits[char]);
      const rows = [...document.querySelectorAll('.message.agent[data-final="true"]')];
      return rows.length >= count && rows.some((row, n) => n >= count - 1 && normalize(row.textContent).includes(normalize(term)));
    }, { term: terms[index], count: index + 1 }, { timeout: index === 0 ? 45000 : 25000 });
    console.log(`Round ${index + 1} received.`);
  }
  await page.waitForTimeout(1000);
  report.transcripts = await page.locator('.message').evaluateAll((rows) => rows.map((row) => ({ role: row.dataset.role, final: row.dataset.final, text: row.querySelector('p').textContent })));
  report.media = await page.evaluate(async () => {
    const packets = [];
    for (const peer of window.__testPeers) {
      for (const stat of (await peer.getStats()).values()) {
        if (['inbound-rtp', 'outbound-rtp'].includes(stat.type) && (stat.kind === 'audio' || stat.mediaType === 'audio')) {
          packets.push(Object.fromEntries(['type', 'bytesReceived', 'bytesSent', 'packetsLost', 'totalSamplesReceived', 'totalAudioEnergy'].filter((k) => k in stat).map((k) => [k, stat[k]])));
        }
      }
    }
    return { packets, playback: [...document.querySelectorAll('audio')].map((audio) => ({ currentTime: audio.currentTime, paused: audio.paused, readyState: audio.readyState })) };
  });
  if (!report.media.packets.some((stat) => stat.type === 'inbound-rtp' && stat.bytesReceived > 1000)) throw new Error('No inbound RTC audio received');
  if (!report.media.packets.some((stat) => stat.type === 'inbound-rtp' && stat.totalAudioEnergy > 0)) throw new Error('Only silent inbound audio was observed');
  if (!report.media.playback.some((audio) => audio.currentTime > 0 && !audio.paused)) throw new Error('Browser audio playback did not advance');
  await page.screenshot({ path: path.join(out, `browser-${rounds}.png`), fullPage: true });
  await page.getByRole('button', { name: '关闭麦克风', exact: true }).click();
  await page.getByRole('button', { name: '开启麦克风', exact: true }).waitFor();
  await page.getByRole('button', { name: '开启麦克风', exact: true }).click();
  await page.getByRole('button', { name: '挂断', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#connection').dataset.state === 'closed');
  const state = await context.request.get(`http://127.0.0.1:8000/api/sessions/${credentials.session_id}`, { headers: { Authorization: `Bearer ${credentials.access_token}` } });
  report.finalSessionState = (await state.json()).state;
  if (report.finalSessionState !== 'closed') throw new Error('Session was not closed');
  if (errors.length) throw new Error('Unhandled page error observed');
  report.status = 'passed';
} catch (error) {
  report.error = error.message;
  report.visibleError = await page.locator('#error').textContent().catch(() => '');
  report.visibleStatus = await page.locator('#status').textContent().catch(() => '');
  report.transcripts = await page.locator('.message').allTextContents().catch(() => []);
  await page.screenshot({ path: path.join(out, `browser-${rounds}-failure.png`), fullPage: true }).catch(() => {});
  console.error('M1 browser check failed:', report.visibleStatus, report.visibleError, error.message);
} finally {
  if (credentials) await context.request.delete(`http://127.0.0.1:8000/api/sessions/${credentials.session_id}`, { headers: { Authorization: `Bearer ${credentials.access_token}` } }).catch(() => {});
  report.pageErrors = errors;
  await browser.close();
  let text = JSON.stringify(report, null, 2);
  for (const secret of [credentials?.token, credentials?.access_token].filter(Boolean)) text = text.replaceAll(secret, '[REDACTED]');
  await fs.writeFile(path.join(out, `browser-${rounds}-report.json`), text);
}
process.exitCode = report.status === 'passed' ? 0 : 1;
