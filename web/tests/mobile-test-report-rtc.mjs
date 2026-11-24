import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright-core';

const root = 'http://127.0.0.1:8002';
const reports = root + '/api/test-reports';
const browser = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  headless: true, args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--mute-audio'] });
const errors = [];
const get = async (url, format = 'json') => {
  const response = await fetch(url);
  assert.equal(response.status, 200);
  return response[format]();
};
const records = async (id, source) => (await get(`${reports}/${id}/events/${source}`, 'text')).trim()
  .split('\n').filter(Boolean).map(line => JSON.parse(line));
const ids = new Set((await get(reports)).map(run => run.id));
const testIds = async () => (await get(reports)).filter(run => !ids.has(run.id));
const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
phone.on('pageerror', error => errors.push(error.message));
try {
  await phone.goto(root + '/?view=call');
  await phone.locator('#mobile-test-enable').check();
  await phone.locator('#join').click();
  await phone.waitForFunction(() => !document.getElementById('camera').disabled, {}, { timeout: 45000 });
  await phone.locator('#camera').click();
  await phone.waitForFunction(() => document.getElementById('official-panel').dataset.playback === 'playback_completed',
    {}, { timeout: 15000 });
  await phone.locator('#mobile-test-mark').click();
  await phone.locator('#leave').click();
  await phone.getByText('测试已保存', { exact: false }).waitFor();
  const [first] = await testIds();
  const desktop = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  desktop.on('pageerror', error => errors.push(error.message));
  await desktop.goto(root + '/mobile-test-report.html?run=' + first.id);
  const axis = desktop.locator('#axis');
  const question = axis.locator('[data-kind="turn.transcript"]');
  const mark = axis.locator('[data-kind="mark"]');
  await question.waitFor();
  assert.equal(await question.count(), 1);
  await question.hover();
  await desktop.locator('#axis-preview').getByText('刚才是什么颜色？').waitFor();
  await question.click();
  assert.match(await desktop.locator('#summary').textContent(), /本地协议测试/);
  assert.match(await desktop.locator('#summary').textContent(), /已播完/);
  const markerPosition = await mark.getAttribute('style');
  await desktop.getByRole('button', { name: '系统动作', exact: true }).click();
  assert.equal(await mark.getAttribute('style'), markerPosition);
  const related = desktop.locator('#event-details select');
  const recall = related.getByRole('option').filter({ hasText: '返回给模型的记忆内容' });
  await related.selectOption(await recall.getAttribute('value'));
  await desktop.locator('#event-details').getByText('绿色方块', { exact: false }).first().waitFor();
  await axis.locator('.dot').first().hover();
  await desktop.locator('#axis-preview').waitFor();
  await desktop.locator('#mark-jumps button').click();
  await desktop.getByText('标记前后 7 秒', { exact: false }).waitFor();
  await desktop.getByRole('heading', { name: '手机问题标记' }).waitFor();
  await desktop.getByRole('button', { name: '放大时间轴' }).click();
  assert.ok(await desktop.locator('#pan').isVisible());
  await mark.waitFor();
  await desktop.getByRole('button', { name: '全程', exact: true }).click();
  assert.equal(await mark.getAttribute('style'), markerPosition);
  await desktop.setViewportSize({ width: 390, height: 840 });
  await desktop.getByRole('button', { name: '音视频', exact: true }).click();
  await mark.click();
  assert.equal(await desktop.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);

  const worker = await records(first.id, 'worker');
  const frame = worker.find(row => row.kind === 'frame.bridge')?.data;
  const sent = worker.find(row => row.kind === 'provider.image_sent')?.data;
  assert.equal(frame.frameKey, sent.frameKey);
  const jpeg = Buffer.from(await get(`${reports}/${first.id}/frames/${frame.frameKey}`, 'arrayBuffer'));
  assert.equal(createHash('sha256').update(jpeg).digest('hex'), frame.frameKey);
  assert.equal(jpeg.readUInt16BE(), 0xffd8);
  const query = worker.find(row => row.kind === 'memory.tool_result')?.data;
  assert.equal(query.result.records[0].text, '绿色方块');
  assert.equal(query.submitted, true);
  assert.ok(worker.some(row => row.kind === 'provider.tool_output_ack' && row.data.toolCallId === query.toolCallId));
  assert.ok(worker.some(row => row.kind === 'capture.worker_end'));
  assert.equal(worker.some(row => 'audio' in row.data || 'apiKey' in row.data), false);
  assert.equal((await fetch(reports, { headers: { 'X-LiveKit-Mobile-Gateway': '1' } })).status, 403);

  const denied = await browser.newPage();
  await denied.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('fixture', 'NotAllowedError'));
  });
  await denied.goto(root + '/');
  await denied.locator('#mobile-test-enable').check();
  await denied.locator('#join').click();
  await denied.getByText('测试已保存', { exact: false }).waitFor();
  const [second] = await testIds();
  assert.notEqual(second.id, first.id);
  assert.equal((await get(`${reports}/${second.id}`)).bound, false);
  assert.equal((await records(second.id, 'worker')).length, 0);
  assert.ok((await records(second.id, 'phone')).some(row => row.kind === 'error' && row.detail.includes('麦克风权限')));
  assert.deepEqual(errors, []);
  console.log('Phone trace, exact JPEG, recall tool result and acknowledgement, playback, mark, and failed join verified.');
} finally {
  await browser.close();
}
