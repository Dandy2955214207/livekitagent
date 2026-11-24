// Real RTC, actual independent monitor; synthetic media only. Max 2 background attempts + 1 QA.
import { chromium } from 'playwright-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const out=process.env.DIRECT_TEST_OUTPUT || path.join(root,'.runtime/direct-background-rtc');
const voice=(await fs.readFile(path.join(root,'.runtime/m0-fixtures/event-warmup.wav'))).toString('base64');
const report={status:'failed',physicalDevices:false,cases:[],pageErrors:[],directStates:[]};
const browser=await chromium.launch({executablePath:'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',headless:true,
  args:['--use-fake-ui-for-media-stream','--mute-audio']});
const page=await browser.newPage({viewport:{width:390,height:844}});
page.on('pageerror',e=>report.pageErrors.push(e.message));
try {
  await page.addInitScript(()=>{
    window.testTracks=[];
    const canvas=document.createElement('canvas'); canvas.width=480; canvas.height=360;
    const d=canvas.getContext('2d');
    const draw=()=>{d.fillStyle='white';d.fillRect(0,0,480,360);d.strokeStyle='gray';d.lineWidth=22;
      d.beginPath();d.arc(115,165,65,0,2*Math.PI);d.stroke();d.beginPath();d.moveTo(180,165);d.lineTo(390,165);
      d.moveTo(340,165);d.lineTo(340,220);d.moveTo(380,165);d.lineTo(380,220);d.stroke();};
    setInterval(draw,200);draw();
    let audio,dest;
    navigator.mediaDevices.getUserMedia=async constraints=>{
      let stream;
      if(constraints.video) stream=canvas.captureStream(5);
      else {audio=new AudioContext();await audio.resume();dest=audio.createMediaStreamDestination();stream=dest.stream;}
      window.testTracks.push(...stream.getTracks());return stream;
    };
    window.testSpeak=async b64=>{await audio.resume();const data=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
      const buffer=await audio.decodeAudioData(data.buffer);const src=audio.createBufferSource();src.buffer=buffer;src.connect(dest);src.start();
      await new Promise(resolve=>{src.onended=resolve;});};
  });
  await page.goto('http://127.0.0.1:8001/');
  await page.evaluate(()=>{window.domStates=[];new MutationObserver(()=>window.domStates.push({notice:document.getElementById('direct-notice').textContent,
    state:document.getElementById('observation-state').textContent,result:document.getElementById('observation-result').textContent}))
    .observe(document.getElementById('direct-panel'),{childList:true,subtree:true,characterData:true});});
  await page.locator('#join').click();
  await page.waitForFunction(()=>!document.getElementById('camera').disabled,{},{timeout:50000});
  await page.locator('#camera').click();
  await page.waitForFunction(()=>document.getElementById('video-status').textContent.includes('共'),{},{timeout:15000});
  await page.locator('#observation-condition').fill('看到画面中出现钥匙时报告。');
  await page.locator('#observation-create').click();
  await page.waitForFunction(()=>document.getElementById('observation-result').textContent.includes('观察结果'),{},{timeout:35000});
  const text=await page.locator('#observation-result').textContent();
  assert.match(text,/未语音播报/);report.cases.push({case:'key text notification',text});
  const first=text;await page.waitForTimeout(2500);
  assert.equal(await page.locator('#observation-result').textContent(),first);
  report.cases.push({case:'stationary target no second notification',status:'passed'});
  await page.evaluate(b64=>window.testSpeak(b64),voice);
  await page.waitForFunction(()=>document.querySelectorAll('#messages .agent p').length>0,{},{timeout:20000});
  report.cases.push({case:'direct QA after background completion',text:await page.locator('#messages .agent p').last().textContent()});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('#leave').click();
  await page.waitForFunction(()=>!document.getElementById('join').disabled,{},{timeout:10000});
  report.liveTracksAfterHangup=await page.evaluate(()=>window.testTracks.filter(t=>t.readyState==='live').length);
  assert.equal(report.liveTracksAfterHangup,0);assert.deepEqual(report.pageErrors,[]);
  report.status='passed';
} catch(e){report.error=String(e);} finally {
  report.directStates=await page.evaluate(()=>window.domStates || []).catch(()=>[]);
  await page.evaluate(()=>document.getElementById('leave')?.click()).catch(()=>{});
  await browser.close();
  await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));
}
console.log(JSON.stringify(report));
process.exitCode=report.status==='passed'?0:1;
