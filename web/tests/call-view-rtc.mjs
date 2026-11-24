import { chromium } from 'playwright-core';
import assert from 'node:assert/strict';
import path from 'node:path';

// Run against the loopback fixture services from verify_official_harness.py.
const origin = 'http://127.0.0.1:8002';
const browser = await chromium.launch({
  executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  headless: true,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--mute-audio'],
});
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));

try {
  await page.goto(`${origin}/?view=call`);
  const panel = page.locator('.call-panel');
  const avatar = page.locator('#call-avatar');
  const join = page.getByRole('button', { name: '开始通话' });
  const camera = page.locator('#camera');
  const leave = page.getByRole('button', { name: '挂断' });
  const more = page.getByRole('button', { name: '更多功能' });
  const selectSubject = page.getByRole('button', { name: '选择主体' });
  const selectHint = page.locator('#call-select-hint');
  await join.waitFor();
  assert.equal(await page.locator('#official-panel, #video-status, #messages').evaluateAll(
    nodes => nodes.every(node => !node.checkVisibility())), true);
  await page.screenshot({ path: path.join(process.env.OFFICIAL_TEST_OUTPUT, 'call-start.png') });
  await join.click();
  await page.waitForFunction(() => document.querySelector('.call-panel').dataset.callPhase === 'active',
    {}, { timeout: 50000 });
  assert.equal(await page.locator('#official-panel').isHidden(), true);
  assert.equal(await page.locator('#record').isHidden(), true);
  assert.equal(await page.locator('#switch-camera').isDisabled(), true);
  assert.equal(await more.isHidden(), true);
  await camera.click();
  const video = page.locator('#preview video');
  await video.waitFor();
  await page.waitForFunction(() => document.querySelector('#preview video')?.videoWidth > 0);
  const frame = await video.boundingBox();
  assert.deepEqual(frame, await panel.boundingBox());
  assert.equal(await video.evaluate(node => getComputedStyle(node).objectFit), 'cover');
  assert.equal(await page.evaluate(() => !document.fullscreenEnabled || !matchMedia('(pointer: coarse)').matches ||
    document.fullscreenElement === document.querySelector('.call-panel')), true);
  const cameraButton = await camera.boundingBox();
  const end = await leave.boundingBox();
  assert.equal(Math.abs(end.x + end.width / 2 - (frame.x + frame.width / 2)) < 1, true);
  assert.ok(cameraButton.y + cameraButton.height < end.y);
  await page.screenshot({ path: path.join(process.env.OFFICIAL_TEST_OUTPUT, 'call-controls.png') });

  const beforeDrag = await avatar.boundingBox();
  await page.mouse.move(beforeDrag.x + 20, beforeDrag.y + 20);
  await page.mouse.down();
  await page.mouse.move(frame.x + frame.width - 20, 190, { steps: 4 });
  await page.mouse.up();
  const afterDrag = await avatar.boundingBox();
  assert.ok(afterDrag.x > beforeDrag.x && afterDrag.y > beforeDrag.y);
  assert.equal(afterDrag.width, beforeDrag.width);
  assert.equal(afterDrag.height, beforeDrag.height);
  await page.mouse.move(afterDrag.x + 20, afterDrag.y + 20);
  await page.mouse.down();
  await page.mouse.move(frame.x + 36, frame.y + 45, { steps: 4 });
  await page.mouse.up();
  const afterDragBack = await avatar.boundingBox();
  assert.ok(afterDragBack.x < afterDrag.x && afterDragBack.y < afterDrag.y);
  assert.equal(afterDragBack.width, beforeDrag.width);
  assert.equal(afterDragBack.height, beforeDrag.height);
  assert.equal(await leave.isVisible(), true);

  // The guide fixture starts continuous narration on the first audio input.
  await more.waitFor();
  const tapCenter = () => page.touchscreen.tap(frame.x + frame.width / 2, frame.y + frame.height / 2);
  await tapCenter();
  await page.getByRole('button', { name: '显示通话控件' }).waitFor();
  assert.equal(await avatar.isHidden(), true);
  assert.equal(await leave.isHidden(), true);
  assert.equal(await more.isHidden(), true);
  assert.deepEqual(await video.boundingBox(), frame);
  await page.screenshot({ path: path.join(process.env.OFFICIAL_TEST_OUTPUT, 'call-camera-only.png') });
  await tapCenter();
  await leave.waitFor();
  assert.deepEqual(await avatar.boundingBox(), afterDragBack);

  await page.locator('#guide-target-box').waitFor({ timeout: 15000 });
  assert.match(await page.locator('#scene-step').textContent(), /当前主体/);
  await more.tap();
  await selectSubject.waitFor();
  assert.equal(await more.getAttribute('aria-expanded'), 'true');
  await page.screenshot({ path: path.join(process.env.OFFICIAL_TEST_OUTPUT, 'call-more.png') });
  await selectSubject.tap();
  await selectHint.waitFor();
  assert.equal(await more.getAttribute('aria-expanded'), 'false');
  assert.match(await page.locator('#scene-step').textContent(), /当前主体/);
  await tapCenter();
  await selectHint.waitFor({ state: 'hidden' });
  assert.equal(await leave.isVisible(), true);
  await tapCenter();
  await leave.waitFor({ state: 'hidden' });
  await tapCenter();
  await leave.waitFor();
  await page.waitForFunction(() => !document.getElementById('guide-target-box').hidden);
  assert.match(await page.locator('#scene-step').textContent(), /当前主体/);

  await page.locator('#mic').click();
  await page.waitForFunction(() => document.getElementById('mic').getAttribute('aria-pressed') === 'true');
  await more.tap();
  await selectSubject.tap();
  await selectHint.waitFor();
  await page.locator('#switch-camera').click();
  await page.waitForFunction(() => document.querySelector('#preview video')?.dataset.mirrored === 'true');
  assert.equal(await selectHint.isHidden(), true);
  await tapCenter();
  await leave.waitFor({ state: 'hidden' });
  await tapCenter();
  await leave.waitFor();
  const track = await video.evaluateHandle(node => node.srcObject.getVideoTracks()[0]);
  await leave.click();
  await join.waitFor();
  assert.equal(await track.evaluate(node => node.readyState), 'ended');
  assert.equal(await page.evaluate(() => document.fullscreenElement), null);
  assert.equal(await page.locator('#preview video').count(), 0);

  await page.evaluate(() => {
    navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('', 'NotAllowedError'); };
  });
  await join.click();
  await page.getByText('请允许使用麦克风，再试一次。').waitFor();
  await join.waitFor();
  assert.deepEqual(errors, []);
  console.log('Mobile call view passed: fullscreen, fixed-size drag, one-shot guide selection, tap to hide and restore, mute, flip camera, hang up and permission recovery.');
} finally {
  await browser.close();
}
