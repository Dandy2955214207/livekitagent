import { createInterface } from 'node:readline';

export function serveStdio(createRuntime) {
  let runtime, callId, closing = false, pendingBytes = 0;
  const emit = (event) => {
    if (closing) return false;
    const line = `${JSON.stringify(event)}\n`;
    if (event.type === 'test_trace' && (Buffer.byteLength(line) > 200000 ||
        process.stdout.writableLength > 256 * 1024)) return false;
    if (process.stdout.writableLength + line.length > 2 * 1024 * 1024) {
      void finish(1); return false;
    }
    process.stdout.write(line); return true;
  };
  async function finish(code = 0) {
    if (closing) return;
    closing = true;
    lines.close();
    await runtime?.close();
    process.exitCode = code;
    process.stdin.destroy();
  }
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  let chain = Promise.resolve();
  lines.on('line', (line) => {
    pendingBytes += line.length;
    if (pendingBytes > 2 * 1024 * 1024 || line.length > 280000) {
      emit({ type: 'fatal', code: 'bridge_input_backlog', callId, epoch: 1 }); void finish(1); return;
    }
    chain = chain.then(async () => {
      pendingBytes -= line.length;
      if (closing) return;
      const event = JSON.parse(line);
      if (!runtime) {
        if (event.type !== 'start') throw new Error('start_required');
        callId = event.callId;
        runtime = await createRuntime(event, emit);
        await runtime.start();
      } else if (event.type === 'stop') await finish();
      else await runtime.handle(event);
    }).catch(async () => {
      emit({ type: 'fatal', code: 'bridge_operation_failed', callId, epoch: 1 });
      await finish(1);
    });
  });
  lines.on('close', () => { if (!closing) void chain.finally(() => finish()); });
  process.on('SIGTERM', () => void finish());
  process.on('SIGINT', () => void finish());
}
