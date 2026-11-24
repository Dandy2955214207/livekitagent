// Synthetic media only. Credentials arrive through private stdin.
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { setTimeout as sleep } from 'node:timers/promises';
import assert from 'node:assert/strict';
import { createRuntime } from '../src/runtime.mjs';

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const config = JSON.parse(Buffer.concat(chunks).toString());
const report = { status: 'failed', componentOnly: true, physicalDevices: false, transcripts: [], diagnostics: [], tools: [] };
let now = Date.now();
const runtime = await createRuntime({ ...config, callId: randomUUID(), epoch: 1, memoryEnabled: true,
  proactiveEnabled: false, maxGenerations: 8, maxMonitorGenerations: 1 }, event => {
  if (event.type === 'transcript') report.transcripts.push({ role: event.role, text: event.text });
  if (event.type === 'memory' || event.type === 'fatal') report.diagnostics.push(event);
  return true;
}, { memoryOptions: { now: () => now } });
const originalQuery = runtime.memory.query.bind(runtime.memory);
runtime.memory.query = async args => {
  const result = await originalQuery(args);
  report.tools.push({ args, result });
  return result;
};

async function waitUntil(predicate, timeout = 22000) {
  const deadline = Date.now() + timeout;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error('probe_timeout');
    await sleep(50);
  }
}

try {
  await runtime.start();
  const images = {};
  for (const name of ['red', 'blue']) images[name] = (await readFile('.runtime/m0-fixtures/' + name + '.jpg')).toString('base64');
  // Feed 30 sampled frames only to memory. Use a controlled clock to cover
  // rollover without waiting through a full physical call.
  for (let frame = 1; frame <= 30; frame++) {
    now += 1000;
    runtime.memory.feed(images[frame >= 7 && frame <= 10 ? 'blue' : 'red']);
  }
  const pcm = await readFile('.runtime/m0-fixtures/call-memory.pcm');
  for (let at = 0; at < pcm.length + 96000; at += 640) {
    const audio = at < pcm.length ? pcm.subarray(at, at + 640) : Buffer.alloc(640);
    runtime.session.pushAudio({ epoch: 1, callId: runtime.host.callId, pcm16: audio });
    await sleep(20);
  }
  await waitUntil(() => report.tools.length && report.transcripts.some(row => row.role === 'assistant' && /蓝/.test(row.text)));
  assert.ok(report.tools.some(({ result }) => result.frames === 30 && result.records.some(row => row.source === 'window' && /蓝/.test(row.text))), 'all-window visual evidence');
  await waitUntil(() => !runtime.memory.activeBatch && !runtime.memory.indexing);
  assert.ok(runtime.memory.records.some(row => /蓝/.test(row.text) && row.vector), 'archived blue frame and indexed summary');
  now += 31000;
  runtime.memory.captureQuestion();
  const result = await originalQuery({ period: 'beginning', query: 'cool-toned shape' });
  report.archivedQuery = result;
  assert.equal(result.frames, 0);
  assert.equal(result.search, 'hybrid');
  assert.ok(result.records.some(row => row.source === 'summary' && /蓝/.test(row.text)), 'semantic history after raw frame expiry');
  report.status = 'passed_visual_memory_recall';
} catch (error) { report.failure = error.message; }
finally {
  await runtime.close();
  report.clearedOnClose = runtime.memory.frames.length === 0 && runtime.memory.records.length === 0;
  const directory = '.runtime/memory-probes';
  await mkdir(directory, { recursive: true });
  const path = directory + '/' + randomUUID() + '.json';
  await writeFile(path, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ path, ...report }));
}
process.exitCode = report.status.startsWith('passed') ? 0 : 1;
