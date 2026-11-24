import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { loadOfficial } from '../src/official.mjs';
import { createGateway } from '../src/gateway.mjs';
import { createEvidenceMonitor } from '../src/evidence-monitor.mjs';
import { GUIDE_PROMPT, validateGuide } from '../src/guide-monitor.mjs';
import { GuideTarget } from '../src/guide-target.mjs';

const chunks=[]; for await(const c of process.stdin) chunks.push(c);
const input=JSON.parse(Buffer.concat(chunks).toString());
const report={status:'failed',syntheticIllustrations:true,physicalDevices:false,maxGenerations:4,cases:[],observations:[],usage:{},diagnostics:[]};
const official=await loadOfficial();
const gateway=await createGateway({...input,maxGenerations:4,maxMonitorGenerations:4},{
  onUsage:u=>report.usage=u,onDiagnostic:d=>report.diagnostics.push(d),onLimit:code=>report.diagnostics.push({code})});
let target=new GuideTarget(), decision, allowed=0, spoken=[], monitor, ready=false;
report.fastBurst=Boolean(input.fastBurst);
const config=structuredClone(official.DEFAULT_PROACTIVE_CONFIG);
config.scheduler.evalIntervalSec=.25;config.scheduler.repeat.cooldownSec=1;config.scheduler.repeat.clearBufferOnResume=false;config.vision.windowSizeSec=3;
const deliveries=[];
const scheduler=new official.ProactiveScheduler({config,realtime:{endpoint:gateway.endpoint,apiKey:gateway.token,model:input.model},
  onEvent:d=>(deliveries.push(d),true),
  createMonitor:(o,cb)=>(monitor=createEvidenceMonitor(official.DashScopeRealtimeMonitor,o,{...cb,onDebug(event,details){
    (report.transportEvents??=[]).push(event);
    if(event==='proactive.monitor_ready')ready=true;
    cb.onDebug?.(event,details);
  }},{
    allowEvaluation:()=>gateway.stats.generations<allowed,
    onObservation:o=>report.observations.push(o),
    protocol:{prompt:GUIDE_PROMPT,validate:(raw,clip,now)=>{
      (report.rawSyntheticResults??=[]).push({raw,clip});return validateGuide(raw,clip,now);
    },
      context:()=>({focus:'持续介绍当前重点展示的物品，只讲主体',depth:'brief',alreadySpoken:spoken,segmentLimit:1,...target.context()}),
      observeUnknown:o=>{decision=target.observe(o);},
      accept:o=>{decision=target.observe(o);if(decision.accepted&&o.summary)spoken.push(o.summary);return decision.accepted&&Boolean(o.summary);}}
  }))});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const started=Date.now();
async function until(predicate, timeout=12000){const end=Date.now()+timeout;while(!predicate()){if(Date.now()>end||Date.now()-started>55000)throw Error('probe_timeout');await sleep(30);}}
async function clip(name,image){
  report.phase=name;
  const n=report.observations.length;decision=undefined;allowed++;
  const began=Date.now();
  if(input.fastBurst){
    monitor.resetPendingCapture();monitor.feedImage(image,Date.now());await sleep(260);monitor.feedImage(image,Date.now());
    await until(()=>monitor.requestEvaluation());
  }else{
    await sleep(1050);scheduler.feedImage(image);await sleep(1050);scheduler.feedImage(image);
  }
  await until(()=>report.observations.length>n);
  for(const d of deliveries.splice(0)){scheduler.announcementStarted(d);scheduler.playbackStarted(d);scheduler.acknowledgeDelivery(d);}
  const o=report.observations.at(-1);report.cases.push({name,decision,target:target.snapshot(),subject:target.current?.subject,
    observation:o.state,reason:o.reason,evaluationMs:o.evaluationMs,captureToResultMs:Date.now()-began});
}
try{
  const a=(await readFile('.runtime/guide-target-fixtures/foreground.jpg')).toString('base64');
  const b=(await readFile('.runtime/guide-target-fixtures/ambiguous.jpg')).toString('base64');
  const task=scheduler.createLiveNarration({title:'主体区域试测',modalities:['vision'],narrationFocus:'只讲当前主体',narrationStyle:'简短'});
  report.phase='connecting';
  await until(()=>scheduler.listTasks().some(t=>t.taskId===task.taskId&&t.status==='running'),30000);
  await until(()=>ready);
  await clip('foreground-not-edge',a);const first=target.current?.id;
  await clip('same-subject-persists',a);const retained=Boolean(first&&first===target.current?.id);
  target.select({point:{x:.91,y:.15}});await clip('explicit-edge-selection',a);
  target=new GuideTarget();spoken=[];await clip('equal-candidates',b);
  report.status=report.cases[0].decision?.accepted&&retained&&report.cases[2].decision?.accepted&&
    report.cases[3].observation==='unknown'?'passed_bounded_synthetic_sample':'failed_behavior';
}catch(error){report.error=error.message;}
finally{
  scheduler.dispose();await gateway.close();report.elapsedMs=Date.now()-started;
  const dir='.runtime/guide-target-probes';await mkdir(dir,{recursive:true});
  const file=`${dir}/${randomUUID()}.json`;await writeFile(file,JSON.stringify(report,null,2));
  console.log(JSON.stringify({report:file,status:report.status,usage:report.usage,cases:report.cases}));
}
process.exitCode=report.status.startsWith('passed')?0:1;
