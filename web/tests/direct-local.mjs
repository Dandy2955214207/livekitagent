// Offline DOM check. No RTC connection, provider calls, or physical media.
import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const browser = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const errors = []; page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', (route) => route.abort());
  const html = (await fs.readFile(path.join(root, 'web/index.html'), 'utf8')).replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  // Give crypto.randomUUID the same secure localhost origin as the application.
  // The response is fulfilled in-process; no local server or network is used.
  await page.route('http://127.0.0.1:49999/', (route) => route.fulfill({ contentType: 'text/html', body: html }));
  await page.goto('http://127.0.0.1:49999/');
  await page.addStyleTag({ content: await fs.readFile(path.join(root, 'web/src/style.css'), 'utf8') });
  await page.addScriptTag({ content: (await fs.readFile(path.join(root, 'web/src/direct.js'), 'utf8')).replace('export function createDirectPanel', 'window.createDirectPanel = function') });
  await page.evaluate(() => {
    window.requests = [];
    window.panel = window.createDirectPanel(async (data) => window.requests.push(data));
    window.panel.activate(true, true);
  });
  assert.equal(await page.locator('#memory-save').isDisabled(), true);
  await page.evaluate(() => window.panel.update({ request_id: window.requests.at(-1).request_id,
    notice: '观察协议待验证', memory: { enabled: true, library_id: 'isolated', persistent: false, records: [] } }));
  await page.locator('#observation-condition').fill('看到钥匙提醒');
  await page.locator('#observation-create').click();
  assert.equal(await page.evaluate(() => window.requests.at(-1).action), 'observation_create');
  await page.evaluate(() => window.panel.update({ request_id: window.requests.at(-1).request_id,
    task: { condition: '看到钥匙提醒', status: 'paused' }, notice: '已登记，暂停等待验证',
    memory: { enabled: true, library_id: 'isolated', persistent: false,
      records: [{ memory_id: 'fact-1', content: '<img src=x onerror=alert(1)>测试事实' }] } }));
  assert.match(await page.locator('#observation-state').textContent(), /已暂停/);
  assert.equal(await page.locator('#memory-records img').count(), 0);
  await page.locator('#direct-panel summary').click();
  await page.locator('#memory-existing').selectOption('fact-1');
  await page.locator('#memory-content').fill('纠正后的事实');
  await page.locator('#memory-save').click();
  const correction = await page.evaluate(() => window.requests.at(-1));
  assert.equal(correction.action, 'memory_correct'); assert.equal(correction.record_id, 'fact-1');
  await page.evaluate(() => window.panel.update({ request_id: window.requests.at(-1).request_id,
    notice: '本地保存，尚未回注', memory: { enabled: true, library_id: 'isolated', persistent: false,
      records: [{ memory_id: 'fact-2', content: '纠正后的事实' }] } }));
  await page.evaluate(() => window.panel.update({ observation: 'experimental_text',
    task: { condition: '看到钥匙提醒', status: 'completed' },
    events: [{ summary: '钥匙已出现', evidence: '图中出现钥匙轮廓', delivery: 'queued', voice_played: false }],
    notice: '匹配后仅显示文字结果' }));
  assert.equal(await page.locator('#observation-create').textContent(), '开始文字观察');
  assert.match(await page.locator('#observation-result').textContent(), /钥匙已出现.*未语音播报/);
  await page.locator('#observation-pause').click();
  assert.equal(await page.evaluate(() => window.requests.at(-1).action), 'observation_pause');
  await page.evaluate(() => window.panel.update({ request_id: window.requests.at(-1).request_id,
    observation: 'experimental_text', task: { condition: '看到钥匙提醒', status: 'paused' }, notice: '观察已暂停' }));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const output = path.join(root, '.runtime/direct-background-ui'); await fs.mkdir(output, { recursive: true });
  await page.screenshot({ path: path.join(output, 'phone-local.png'), fullPage: true });
  await page.evaluate(() => window.panel.activate(false, false));
  assert.equal(await page.locator('#direct-panel').isHidden(), true);
  assert.equal(await page.locator('#memory-content').inputValue(), '');
  assert.equal(await page.locator('#memory-records li').count(), 0);
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({ status: 'passed', width: 390,
    providerCalls: 0, physicalDevices: false, pageErrors: errors,
    cases: ['ack disables controls', 'paused target registration', 'experimental text result without fake voice receipt', 'pause command', 'fact correction uses real record ID',
      'untrusted memory rendered as text', 'no horizontal overflow', 'leave clears local views'] }, null, 2));
  console.log('Offline direct panel checks passed; provider calls: 0.');
} finally { await browser.close(); }
