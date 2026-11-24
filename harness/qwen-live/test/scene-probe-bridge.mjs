// Selected only by the standalone synthetic-voice probe, never product config.
import { createRuntime } from '../src/runtime.mjs';
import { serveStdio } from '../src/stdio.mjs';
serveStdio((config, emit) => createRuntime(config, emit, {
  gateway: { onResponse(details) { emit({ type: 'probe_response', callId: config.callId, epoch: 1, ...details }); } },
  sceneDiagnostics(details) { emit({ type: 'probe_tool_arguments', callId: config.callId, epoch: 1, ...details }); },
}));
