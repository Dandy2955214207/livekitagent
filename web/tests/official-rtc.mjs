// Real browser, LiveKit, Python worker, Node core and loopback provider; no paid API.
import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { setTimeout as sleep } from 'node:timers/promises';

const out = process.env.OFFICIAL_TEST_OUTPUT;
if (!out) throw new Error('OFFICIAL_TEST_OUTPUT required');
const report = { status: 'failed', providerCalls: 0, physicalDevices: false, pageErrors: [], cases: [] };
const browser = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  headless: true, args: ['--use-fake-ui-for-media-stream', '--mute-audio'] });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
page.on('pageerror', (error) => report.pageErrors.push(error.message));
try {
  await page.addInitScript(() => {
    window.fixtureTracks = [];
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      let stream;
      if (constraints.video) {
        const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 240;
        const ctx = canvas.getContext('2d');
        const draw = () => {
          if (window.fixtureGuideScene === 'empty' || window.fixtureGuideScene === 'replacement') {
            ctx.fillStyle = window.fixtureGuideScene === 'empty' ? '#902424' : '#246090';
            ctx.fillRect(0, 0, 320, 240);
            if (window.fixtureGuideScene === 'replacement') {
              ctx.fillStyle = '#f3a450'; ctx.fillRect(80,36,160,168);
            }
            return;
          }
          ctx.fillStyle = '#e7e1d4'; ctx.fillRect(0, 0, 320, 240);
          ctx.fillStyle = '#407c36'; ctx.fillRect(80,36,160,168);
          ctx.fillStyle = '#3674ad'; ctx.fillRect(269,7,38,48);
        };
        draw(); setInterval(draw, 100);
        stream = canvas.captureStream(5);
      } else {
        const context = new AudioContext(); await context.resume();
        const destination = context.createMediaStreamDestination();
        const oscillator = context.createOscillator(), gain = context.createGain();
        gain.gain.value = .001; oscillator.connect(gain); gain.connect(destination); oscillator.start();
        stream = destination.stream;
      }
      window.fixtureTracks.push(...stream.getTracks()); return stream;
    };
  });
  await page.goto('http://127.0.0.1:8002/');
  await page.locator('#join').click();
  await page.waitForFunction(() => !document.getElementById('camera').disabled, {}, { timeout: 50000 });
  assert.equal(await page.locator('#official-panel').isVisible(), true);
  await page.waitForFunction(() => document.getElementById('official-panel').dataset.playback === 'playback_completed', {}, { timeout: 12000 });
  report.cases.push('real LiveKit audio input → official core → PCM data → browser render → matching completion receipt');
  assert.match(await page.locator('#messages').textContent(), /本地协议测试/);
  report.cases.push('identified input and output transcripts');
  if (!process.env.OFFICIAL_TEST_SCENE) {
    for (const code of ['fixture_input_muted', 'fixture_input_unmuted']) {
      await page.locator('#mic').click();
      const deadline = Date.now() + 5000;
      let observed = false;
      while (Date.now() < deadline) {
        const workerLog = await fs.readFile(path.join(out, 'worker.log'), 'utf8');
        if (workerLog.includes(code)) { observed = true; break; }
        await sleep(50);
      }
      assert.ok(observed, `Official core did not apply ${code} after clicking the microphone`);
    }
    report.cases.push('browser mute/unmute reaches the official core through LiveKit and the worker');
  }
  await page.locator('#camera').click();
  await page.waitForFunction(() => document.getElementById('video-status').textContent.includes('共'), {}, { timeout: 10000 });
  report.cases.push('existing camera path feeds official core');
  if (process.env.OFFICIAL_TEST_SCENE === 'guide') {
    await page.locator('#guide-target-box').waitFor({ timeout: 15000 });
    assert.match(await page.locator('#guide-target-label').textContent(), /中央绿色盆栽/);
    await page.screenshot({ path: path.join(out,'guide-before.png'),fullPage:true });
    const bounds=await page.locator('#preview').boundingBox();
    const width=Math.min(bounds.width,bounds.height*4/3),height=width*3/4;
    await page.mouse.click(bounds.x+(bounds.width-width)/2+width*.9,bounds.y+(bounds.height-height)/2+height*.12);
    await page.waitForFunction(()=>document.getElementById('guide-target-label').textContent==='右上蓝色瓶子'&&!document.getElementById('guide-target-box').hidden,{}, {timeout:15000});
    assert.match(await page.locator('#scene-title').textContent(), /只讲展示的主体物品/);
    report.cases.push('guide region displayed; explicit off-center tap travels through LiveKit and changes only the target');
    await page.locator('#switch-camera').click();
    await page.waitForFunction(()=>{
      const box=document.getElementById('guide-target-box'),preview=document.getElementById('preview');
      return preview.querySelector('video')?.dataset.mirrored==='true'&&!box.hidden&&box.getBoundingClientRect().left<preview.getBoundingClientRect().left+preview.clientWidth*.2;
    },{}, {timeout:15000});
    const mirrored=await page.locator('#preview').boundingBox();
    const mw=Math.min(mirrored.width,mirrored.height*4/3),mh=mw*3/4;
    await page.mouse.click(mirrored.x+(mirrored.width-mw)/2+mw*.1,mirrored.y+(mirrored.height-mh)/2+mh*.12);
    await page.waitForFunction(()=>document.getElementById('guide-target-label').textContent==='右上蓝色瓶子'&&!document.getElementById('guide-target-box').hidden,{}, {timeout:15000});
    report.cases.push('front-camera mirrored overlay and tap preserve image-space selection');
    await page.locator('#scene-pause').click();
    await page.waitForFunction(()=>document.getElementById('guide-target-box').hidden);
    await page.locator('#scene-pause').click();
    await page.locator('#guide-target-box').waitFor({timeout:15000});
    assert.match(await page.locator('#guide-target-label').textContent(),/右上蓝色瓶子/);
    report.cases.push('paused guide hides overlay; resumed guide revalidates the same target');
    await page.evaluate(() => { window.fixtureGuideScene = 'empty'; });
    await page.waitForFunction(() => document.getElementById('guide-target-box').hidden &&
      document.getElementById('scene-status').textContent === '等待清晰的主体画面。', {}, { timeout: 12000 });
    await page.evaluate(() => { window.fixtureGuideScene = 'replacement'; });
    await page.waitForFunction(() => !document.getElementById('guide-target-box').hidden &&
      document.getElementById('guide-target-label').textContent === '中央橙色盒子', {}, { timeout: 12000 });
    assert.match(await page.locator('#scene-step').textContent(), /当前主体/);
    assert.equal(await page.getByRole('button', { name: '恢复自动选择', exact: true }).count(), 0);
    report.cases.push('selected object leaves the captured canvas; guide clears its box and automatically selects the next object');
  } else if (process.env.OFFICIAL_TEST_SCENE === 'teach') {
    await page.locator('#scene-confirm').waitFor({ timeout: 10000 });
    assert.match(await page.locator('#scene-step').textContent(), /1\/2/);
    assert.equal(await page.getByRole('button', { name: '改为每次出现提醒', exact: true }).count(), 0);
    await page.locator('#scene-pause').click();
    await page.waitForFunction(() => document.getElementById('scene-status').textContent === '任务已暂停');
    await page.locator('#scene-pause').click();
    await page.locator('#scene-confirm').waitFor({ timeout: 10000 });
    await page.locator('#scene-confirm').click();
    await page.waitForFunction(() => document.getElementById('scene-step').textContent.includes('2/2') && !document.getElementById('scene-confirm').hidden);
    await page.locator('#scene-confirm').click();
    await page.waitForFunction(() => document.getElementById('scene-status').textContent === '全部完成');
    report.cases.push('persistent teaching parent: real browser receipts, pause/resume, step 1 → step 2 → completed through LiveKit controls');
  } else {
  await page.getByRole('button', { name: '改为只提醒一次', exact: true }).waitFor({ timeout: 10000 });
  assert.match(await page.locator('#official-tasks').textContent(), /每次独立事件/);
  await page.getByRole('button', { name: '改为只提醒一次', exact: true }).click();
  await page.getByRole('button', { name: '改为每次出现提醒', exact: true }).waitFor({ timeout: 10000 });
  assert.match(await page.locator('#official-tasks').textContent(), /只提醒一次/);
  report.cases.push('intent rule displayed and changed through browser → LiveKit → Python → official scheduler');
  await page.getByRole('button', { name: '暂停', exact: true }).click();
  await page.waitForFunction(() => document.getElementById('official-tasks').textContent.includes('已暂停'));
  await page.getByRole('button', { name: '继续', exact: true }).click();
  await page.waitForFunction(() => document.getElementById('official-tasks').textContent.includes('等待条件'));
  report.cases.push('auxiliary pause/resume retains requirement and reinstalls a fresh observer through LiveKit');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(out, 'phone.png'), fullPage: true });
  await page.getByRole('button', { name: '结束任务', exact: true }).click();
  await page.waitForFunction(() => document.getElementById('official-tasks').textContent.includes('已取消'));
  report.cases.push('visible cancellation of the observation task');
  }
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(out, 'phone.png'), fullPage: true });
  await page.locator('#leave').click();
  await page.waitForFunction(() => !document.getElementById('join').disabled, {}, { timeout: 10000 });
  assert.equal(await page.evaluate(() => window.fixtureTracks.filter((track) => track.readyState === 'live').length), 0);
  assert.equal(await page.locator('#official-panel').isHidden(), true);
  assert.deepEqual(report.pageErrors, []);
  report.cases.push('hangup stops all test tracks and clears the official panel');
  report.status = 'passed';
} catch (error) { report.error = String(error); report.pageText = (await page.locator('body').textContent()).slice(-1600); }
finally {
  await page.locator('#leave').click().catch(() => {});
  await browser.close();
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
}
console.log(JSON.stringify(report));
process.exitCode = report.status === 'passed' ? 0 : 1;
