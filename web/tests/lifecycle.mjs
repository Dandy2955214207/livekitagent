import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = path.join(root, '.runtime/m1-results'); await fs.mkdir(out, { recursive: true });
const report = { status: 'failed', cases: [], physicalMicrophoneUsed: false };
const browser = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', headless: true,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(root, '.runtime/m1-fixtures/browser-1.wav')}`] });
const context = await browser.newContext();
let created;
try {
  const denialPage = await context.newPage();
  await denialPage.addInitScript(() => { navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Denied by test', 'NotAllowedError'); }; });
  let sessionRequests = 0;
  denialPage.on('request', (request) => { if (request.url().endsWith('/api/sessions') && request.method() === 'POST') sessionRequests++; });
  await denialPage.goto('http://127.0.0.1:8000');
  await denialPage.getByRole('button', { name: /加入通话/ }).click();
  await denialPage.getByRole('alert').filter({ hasText: '未获得麦克风权限' }).waitFor();
  if (sessionRequests !== 0) throw new Error('Denied microphone still created a paid session');
  report.cases.push('microphone denial does not create a session');
  await denialPage.close();

  const page = await context.newPage();
  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const response = await route.fetch(); created = await response.json();
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await route.fulfill({ response });
  });
  await page.goto('http://127.0.0.1:8000');
  await page.getByRole('button', { name: /加入通话/ }).click();
  while (!created) await new Promise((resolve) => setTimeout(resolve, 50));
  await page.getByRole('button', { name: '挂断', exact: true }).click();
  await page.waitForTimeout(2000);
  const status = await context.request.get(`http://127.0.0.1:8000/api/sessions/${created.session_id}`, { headers: { Authorization: `Bearer ${created.access_token}` } });
  if ((await status.json()).state !== 'closed') throw new Error('Join cancellation leaked an active session');
  if (!(await page.getByRole('button', { name: /加入通话/ }).isEnabled())) throw new Error('Join button remained disabled');
  report.cases.push('hangup during delayed session creation closes the stale session');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(out, 'mobile.png'), fullPage: true });
  const fits = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  if (!fits) throw new Error('Mobile layout overflows horizontally');
  report.cases.push('390px mobile layout has no horizontal overflow');
  report.status = 'passed';
} catch (error) { report.error = error.message; console.error(error.message); }
finally {
  if (created) await context.request.delete(`http://127.0.0.1:8000/api/sessions/${created.session_id}`, { headers: { Authorization: `Bearer ${created.access_token}` } }).catch(() => {});
  await browser.close();
  await fs.writeFile(path.join(out, 'lifecycle-report.json'), JSON.stringify(report, null, 2));
}
console.log(JSON.stringify(report));
process.exitCode = report.status === 'passed' ? 0 : 1;
