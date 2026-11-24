// Dedicated local RTC test entry point; unavailable through product config.
import { createRuntime } from '../src/runtime.mjs';
import { serveStdio } from '../src/stdio.mjs';
import { fakeProvider } from './fake-provider.mjs';
serveStdio(async (config, emit) => {
  const provider = await fakeProvider();
  const runtime = await createRuntime(config, emit, { gateway: { upstreamUrl: provider.url } });
  const handle = runtime.handle, close = runtime.close;
  let turned = false;
  runtime.handle = async event => {
    await handle(event);
    if (event.type === 'audio' && !turned) {
      turned = true;
      setTimeout(() => provider.turn(provider.peers[0], { text: '一步一步指导我画小猪，直到完成',
        tool: { name: 'start_scene_task', arguments: JSON.stringify({ mode: 'teach', goal: '画小猪', depth: 'normal' }) } }), 100);
    }
  };
  runtime.close = async () => { await close(); await provider.close(); };
  return runtime;
});
