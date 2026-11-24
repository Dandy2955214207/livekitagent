import { createRuntime } from '../src/runtime.mjs';
import { serveStdio } from '../src/stdio.mjs';
import { fakeProvider } from './fake-provider.mjs';

serveStdio(async (config, emit) => {
  let asked = false;
  const provider = await fakeProvider({ onRequest(event, peer) {
    if (event.type !== 'input_image_buffer.append' || asked) return;
    asked = true;
    setTimeout(() => provider.turn(peer, { text: '刚才是什么颜色？', tool: {
      name: 'recall_call_history', arguments: '{"query":"方块","period":"recent"}',
    } }), 100);
  } });
  const runtime = await createRuntime({ ...config, memoryEnabled: true }, emit, {
    gateway: { upstreamUrl: provider.url },
    memoryObserver: { close() {}, async review(frames) {
      return [{ first: frames[0].id, last: frames.at(-1).id, certainty: 'observed', text: '绿色方块' }];
    } },
  });
  const close = runtime.close;
  runtime.close = async () => { await close(); await provider.close(); };
  return runtime;
});
