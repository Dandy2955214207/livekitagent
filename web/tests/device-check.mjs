import { chromium } from 'playwright-core';
import path from 'node:path';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, '.runtime/mobile'); await fs.mkdir(output, { recursive: true });
const report = { status: 'failed', device: 'isolated desktop Edge fake devices; not the Huawei phone', modelRequests: 0 };
const browser = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  headless: true, args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--mute-audio'] });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
page.on('request', (request) => { if (/\/api\/|\/livekit|maas\.aliyuncs/.test(new URL(request.url()).pathname)) report.modelRequests++; });
try {
  await page.goto(process.env.DEVICE_CHECK_URL ?? 'http://127.0.0.1:8000/device-check.html');
  await page.getByRole('button', { name: '开始本地采集' }).click();
  await page.waitForFunction(() => document.querySelector('video').videoWidth > 0);
  await page.evaluate(() => { window.__oldTracks = document.querySelector('video').srcObject.getTracks(); });
  await page.getByRole('button', { name: '切换前后摄像头' }).click();
  await page.waitForFunction(() => window.__oldTracks.every((track) => track.readyState === 'ended') && document.querySelector('video').videoWidth > 0);
  await page.getByRole('button', { name: '播放提示音' }).click();
  await page.getByRole('button', { name: '我听到了' }).waitFor();
  await page.getByRole('button', { name: '停止采集' }).click();
  const data = JSON.parse(await page.locator('#result').textContent());
  if (!data.captureStopped || data.audioContext !== 'running') throw new Error('Capture release or audio context check failed');
  if (report.modelRequests) throw new Error('Local capability test called a session/model endpoint');
  report.capabilities = data;
  report.cases = ['local preview displays frames', 'old tracks ended before reacquisition', 'stop releases tracks', 'audio context starts', 'no session/model request'];
  report.status = 'passed';
  await page.screenshot({ path: path.join(output, 'device-check-desktop.png'), fullPage: true });
} catch (error) { report.error = error.message; console.error(error.message); }
finally { await browser.close(); await fs.writeFile(path.join(output, 'desktop-check-report.json'), JSON.stringify(report, null, 2)); }
console.log(report.status);
process.exitCode = report.status === 'passed' ? 0 : 1;
