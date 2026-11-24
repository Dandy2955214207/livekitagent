import { loadOfficial } from './official.mjs';
import { createRuntime } from './runtime.mjs';
import { serveStdio } from './stdio.mjs';

if (process.argv.includes('--check')) {
  const { build } = await loadOfficial();
  process.stdout.write(`${JSON.stringify({ engine: 'qwen-live-harness', ...build })}\n`);
} else serveStdio(createRuntime);