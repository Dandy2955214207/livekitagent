// Internal entry points are intentionally tied to this exact upstream release.
// Do not import the package root: it is a CLI, not an embedding API.
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const root = dirname(require.resolve('qwen-live-harness/package.json'));
export async function loadOfficial() {
  const metadata = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'));
  if (metadata.version !== '1.0.0') throw new Error('official_version_mismatch');
  const module = (file) => import(pathToFileURL(join(root, 'dist', file)).href);
  const [{ LiveSession }, { BackendRegistry }, { DEFAULT_PROACTIVE_CONFIG }, { ProactiveScheduler }, { DashScopeRealtimeMonitor }, { openRecoveringQwenRealtimeSession }] = await Promise.all([
    module('orchestrator/live-session.js'), module('adaptor/registry.js'), module('config.js'),
    module('proactive/scheduler.js'),
    module('proactive/realtime-monitor.js'),
    module('realtime/recovering-session.js'),
  ]);
  const build = JSON.parse(await readFile(join(root, 'dist/build-info.json'), 'utf8'));
  if (build.sourceSha256 !== 'ddfc13c2796f31181c731c49aaba510b6bd87aac7b0fd2357fff69f10693e0a8') throw new Error('official_build_mismatch');
  return { LiveSession, BackendRegistry, DEFAULT_PROACTIVE_CONFIG, ProactiveScheduler, DashScopeRealtimeMonitor, openRecoveringQwenRealtimeSession, build };
}
