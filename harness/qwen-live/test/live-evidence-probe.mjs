// Explicit opt-in via Python wrapper. This is a component probe, NOT a mobile/ASR test.
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { loadOfficial } from '../src/official.mjs';
import { createGateway } from '../src/gateway.mjs';
import { createEvidenceMonitor } from '../src/evidence-monitor.mjs';
import { GUIDE_PROMPT, validateGuide } from '../src/guide-monitor.mjs';

const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
const input = JSON.parse(Buffer.concat(chunks).toString());
const maxGenerations = ['transition', 'guide'].includes(input.case) ? 2 : 7;
const report = { status: 'failed', provider: input.provider, model: input.model, componentOnly: true,
  physicalDevices: false, audioPlayback: false, maxGenerations, cases: [], observations: [], usage: {}, diagnostics: [] };
const official = await loadOfficial();
const gateway = await createGateway({ ...input, maxGenerations, maxMonitorGenerations: maxGenerations }, {
  onUsage: usage => { report.usage = usage; }, onLimit: code => report.diagnostics.push({ code }),
  onDiagnostic: details => report.diagnostics.push(details),
});
const config = structuredClone(official.DEFAULT_PROACTIVE_CONFIG);
config.scheduler.evalIntervalSec = .25; config.scheduler.repeat.cooldownSec = 1;
config.scheduler.repeat.clearBufferOnResume = false; config.vision.windowSizeSec = 3;
config.monitor.sessionRecycleEvals = 6;
const deliveries = [];
const scheduler = new official.ProactiveScheduler({ config,
  realtime: { endpoint: gateway.endpoint, apiKey: gateway.token, model: input.model },
  onEvent: delivery => (deliveries.push(delivery), true),
  createMonitor: (options, callbacks) => createEvidenceMonitor(official.DashScopeRealtimeMonitor, options, callbacks,
    { onObservation: o => report.observations.push(o), allowEvaluation: () => gateway.stats.generations < maxGenerations,
      ...(input.case === 'guide' ? { protocol: { prompt: GUIDE_PROMPT, validate: validateGuide,
        context: () => ({ focus: '只介绍画面中的狗，不介绍环境', depth: 'brief', subject: '', alreadySpoken: [], segmentLimit: 1 }),
        accept: () => true } } : {}) }),
});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const started = Date.now();
async function until(predicate, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (!predicate()) { if (Date.now() > deadline || Date.now() - started > 80000) throw new Error('probe_timeout'); await sleep(30); }
}
const fixtures = {};
async function clip(name, first, second = first) {
  const n = report.observations.length, before = deliveries.length;
  await sleep(1050); scheduler.feedImage(fixtures[first]);
  await sleep(1050); scheduler.feedImage(fixtures[second]);
  await until(() => report.observations.length > n, 12000);
  const result = report.observations.at(-1);
  report.cases.push({ name, state: result.state, reason: result.reason, newEvents: deliveries.length - before,
    evaluationMs: result.evaluationMs, evidenceAgeMs: result.evidenceAgeMs });
  for (const delivery of deliveries.slice(before)) {
    // Component bookkeeping only, never represented as a real browser receipt.
    scheduler.announcementStarted(delivery); scheduler.playbackStarted(delivery); scheduler.acknowledgeDelivery(delivery);
  }
}
try {
  for (const name of ['dog', 'red', 'blue']) fixtures[name] = (await readFile(`.runtime/m0-fixtures/${name}.jpg`)).toString('base64');
  if (input.case === 'guide') {
    const guide = scheduler.createLiveNarration({ title: '狗主体讲解', modalities: ['vision'], narrationFocus: '只介绍狗', narrationStyle: '简短，不说环境' });
    await until(() => scheduler.listTasks().some(t => t.taskId === guide.taskId && t.status === 'running'));
    await clip('guide-request-dog-absent', 'red');
    await clip('guide-dog-present', 'dog');
  } else {
  if (input.case !== 'transition') {
  const recurring = scheduler.createPerceptionMonitor({ title: '每次看到狗', condition: '[state] 当前画面中清楚出现狗',
    triggerResponse: '有狗出现', modalities: ['vision'], repeat: true });
  await until(() => scheduler.listTasks().some(t => t.taskId === recurring.taskId && t.status === 'running'));
  await clip('negative-before', 'red'); await clip('first-occurrence', 'dog');
  await clip('continuous-presence', 'dog'); await clip('clear-absence', 'red'); await clip('second-occurrence', 'dog');
  scheduler.cancelTaskById(recurring.taskId);
  const negative = scheduler.createPerceptionMonitor({ title: '水杯反例', condition: '[state] 当前画面中出现水杯',
    triggerResponse: '喝水', modalities: ['vision'], repeat: false });
  await until(() => scheduler.listTasks().some(t => t.taskId === negative.taskId && t.status === 'running'));
  await clip('cup-request-dog-image', 'dog'); scheduler.cancelTaskById(negative.taskId);
  }
  const transition = scheduler.createPerceptionMonitor({ title: '红变蓝', condition: '[transition] 画面中央的圆形从红色变为蓝色',
    triggerResponse: '变蓝了', modalities: ['vision'], repeat: false });
  await until(() => scheduler.listTasks().some(t => t.taskId === transition.taskId && t.status === 'running'));
  await clip('red-to-blue-transition', 'red', 'blue');
  if (input.case === 'transition') {
    scheduler.cancelTaskById(transition.taskId);
    const reverse = scheduler.createPerceptionMonitor({ title: '蓝变红', condition: '[transition] 画面中央的圆形从蓝色变为红色',
      triggerResponse: '变红了', modalities: ['vision'], repeat: false });
    await until(() => scheduler.listTasks().some(t => t.taskId === reverse.taskId && t.status === 'running'));
    await clip('blue-to-red-transition', 'blue', 'red');
  }
  }
  const expected = input.case === 'guide' ? [0, 1] : input.case === 'transition' ? [1, 1] : [0, 1, 0, 0, 1, 0, 1];
  report.status = report.cases.every((c, i) => c.newEvents === expected[i]) ? 'passed_bounded_component_sample' : 'failed_behavior';
} catch (error) { report.error = error instanceof Error ? error.message : 'probe_failed'; }
finally {
  scheduler.dispose(); await gateway.close(); report.elapsedMs = Date.now() - started;
  const dir = '.runtime/explicit-intent-probes'; await mkdir(dir, { recursive: true });
  const path = `${dir}/${randomUUID()}.json`; await writeFile(path, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ report: path, ...report }));
}
process.exitCode = report.status.startsWith('passed') ? 0 : 1;
