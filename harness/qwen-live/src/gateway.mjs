// Keep all provider quirks and hard cost limits outside the official scheduler.
import { randomBytes } from 'node:crypto';
import { WebSocket, WebSocketServer } from 'ws';
import { INTENT_PROMPT } from './evidence-monitor.mjs';
import { frameKey } from './test-trace.mjs';

const MAX_BUFFER = 2 * 1024 * 1024;
export function providerRoute(config) {
  const provider = config.provider ?? 'yibuapi';
  const endpoint = config.endpoint ?? (provider === 'yibuapi' ? 'wss://yibuapi.com/v1/realtime' : '');
  if (provider === 'aliyun') {
    if (!/^wss:\/\/[A-Za-z0-9-]+\.(cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com\/api-ws\/v1\/realtime$/.test(endpoint)) throw new Error('invalid_aliyun_endpoint');
  } else if (provider !== 'yibuapi' || endpoint !== 'wss://yibuapi.com/v1/realtime') {
    throw new Error('invalid_provider_endpoint');
  }
  return { endpoint, headers: { Authorization: `Bearer ${config.apiKey}`,
    ...(provider === 'yibuapi' ? { 'OpenAI-Beta': 'realtime=v1' } : {}) } };
}
export async function createGateway(config, { upstreamUrl, onUsage = () => {}, onLimit = () => {}, onDiagnostic = () => {}, onResponse = () => {}, trace } = {}) {
  const route = providerRoute(config);
  const target = new URL(upstreamUrl ?? route.endpoint);
  target.searchParams.set('model', config.model);
  const token = randomBytes(32).toString('hex');
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0, maxPayload: MAX_BUFFER,
    perMessageDeflate: false, verifyClient: ({ req }) => req.headers.authorization === `Bearer ${token}` });
  await new Promise((resolve, reject) => { server.once('listening', resolve); server.once('error', reject); });
  const pairs = new Set();
  const stats = { connections: 0, generations: 0, monitorGenerations: 0, planGenerations: 0, inputTokens: 0, outputTokens: 0, monitorInputTokens: 0 };
  let closed = false;
  let limited = false;
  let monitorBudgetReported = false;
  function limit(reason) {
    if (limited || closed) return;
    limited = true;
    onLimit(reason);
    for (const pair of pairs) { pair.local.close(1011, reason); pair.remote.terminate(); }
  }
  server.on('connection', (local, request) => {
    const planning = new URL(request.url, 'http://127.0.0.1').searchParams.get('purpose') === 'scene_plan';
    if (closed || limited || pairs.size >= 4 || stats.connections >= 64) {
      local.close(1013, 'bridge_connection_limit'); return;
    }
    stats.connections++;
    const socketId = stats.connections;
    onUsage({ ...stats });
    const remote = new WebSocket(target, { headers: route.headers,
      maxPayload: MAX_BUFFER, perMessageDeflate: false, handshakeTimeout: 15000 });
    const pair = { local, remote };
    pairs.add(pair);
    let pending = [], pendingBytes = 0, monitor = false, frames = [], frameCount = 0;
    const observe = (kind, data) => trace?.(kind, { socketId, route: planning ? 'planner' : monitor ? 'monitor' : 'foreground', ...data });
    const forward = ({ wire, event }) => {
      if (remote.bufferedAmount + wire.length > MAX_BUFFER) return limit('bridge_input_backlog');
      remote.send(wire);
      if (!trace) return;
      if (event.type === 'input_image_buffer.append' && typeof event.image === 'string') {
        const key = frameKey(event.image);
        frames.push(key);
        if (frames.length > 60) frames.shift();
        observe('provider.image_sent', { frameKey: key, ordinal: ++frameCount });
      } else if (event.type === 'input_audio_buffer.clear') {
        frames = []; frameCount = 0;
      } else if (event.type === 'conversation.item.create' && event.item?.type === 'function_call_output') {
        observe('provider.tool_output_sent', { toolCallId: event.item.call_id });
      }
    };
    remote.on('open', () => { for (const wire of pending) forward(wire); pending = []; pendingBytes = 0; });
    local.on('message', (bytes, binary) => {
      if (closed || limited || binary) return;
      let event;
      try { event = JSON.parse(bytes.toString()); } catch { local.close(1007, 'invalid_json'); return; }
      if (event.type === 'session.update') {
        if (Array.isArray(event.session?.modalities)) {
          monitor = event.session.modalities.length === 1 && event.session.modalities[0] === 'text';
        }
        // Explicit output ceiling for every independent worker, including fallback speech.
        const ceiling = planning ? 1600 : monitor ? 256 : 512;
        event.session.max_response_output_tokens = Math.min(
          Number(event.session.max_response_output_tokens) || ceiling, ceiling);
        if (!monitor && config.proactiveEnabled && typeof event.session.instructions === 'string') {
          event.session.instructions += INTENT_PROMPT;
        }
      }
      if (event.type === 'response.create') {
        if (planning && stats.planGenerations >= 3) { local.close(1013, 'scene_plan_budget'); remote.terminate(); return; }
        const reserve = config.proactiveEnabled && config.maxGenerations >= 6 ? 4 : 0;
        if (monitor && !planning && (stats.monitorGenerations >= config.maxMonitorGenerations || stats.monitorInputTokens >= 120000 ||
            (reserve && stats.generations >= config.maxGenerations - reserve))) {
          if (!monitorBudgetReported) {
            monitorBudgetReported = true;
            onDiagnostic({ stage: 'budget', code: 'monitor_generation_budget' });
          }
          local.close(1013, 'monitor_generation_budget'); remote.terminate(); return;
        }
        if (stats.generations >= config.maxGenerations) {
          limit('bridge_generation_budget'); return;
        }
        stats.generations++;
        if (planning) stats.planGenerations++;
        else if (monitor) stats.monitorGenerations++;
        onUsage({ ...stats });
      }
      const wire = JSON.stringify(event);
      const packet = { wire, event: trace ? event : undefined };
      if (remote.readyState === WebSocket.OPEN) forward(packet);
      else if (remote.readyState === WebSocket.CONNECTING && pendingBytes + wire.length <= MAX_BUFFER) {
        pending.push(packet); pendingBytes += wire.length;
      } else limit('bridge_input_backlog');
    });
    const usageSeen = new Set();
    remote.on('message', (bytes, binary) => {
      if (closed || local.readyState !== WebSocket.OPEN) return;
      if (local.bufferedAmount + bytes.length > MAX_BUFFER) return limit('bridge_output_backlog');
      try {
        const event = JSON.parse(bytes.toString());
        if (trace && event.type === 'input_audio_buffer.committed') {
          observe('provider.input_committed', { ...(monitor || planning ? { itemId: event.item_id } : { turnId: event.item_id }),
            frameKeys: frames, omittedFrames: frameCount - frames.length });
          frames = []; frameCount = 0;
        } else if (trace && event.type === 'conversation.item.created' && event.item?.type === 'function_call_output') {
          observe('provider.tool_output_ack', { toolCallId: event.item.call_id });
        }
        // Some Bailian permission failures arrive without a Realtime `type`.
        // Normalize the known denial instead of leaving the official client
        // waiting forever for session.updated. Never forward a raw error body.
        if (!event.type && event.code === 'AccessDenied.Unpurchased') {
          onDiagnostic({ stage: 'provider', source: 'foreground', code: 'model_access_denied', status: 403 });
          local.send(JSON.stringify({ type: 'error', error: { code: 'model_access_denied',
            type: 'invalid_request_error', status: 403, message: 'Model access denied (403): AccessDenied.Unpurchased' } }));
          return;
        }
        if (event.type === 'response.done') onResponse({ status: event.response?.status, details: event.response?.status_details, usage: event.response?.usage });
        if (event.type === 'response.done' && event.response?.usage && !usageSeen.has(event.response.id)) {
          usageSeen.add(event.response.id);
          if (usageSeen.size > 128) usageSeen.delete(usageSeen.values().next().value);
          stats.inputTokens += Number(event.response.usage.input_tokens) || 0;
          if (monitor && !planning) stats.monitorInputTokens += Number(event.response.usage.input_tokens) || 0;
          stats.outputTokens += Number(event.response.usage.output_tokens) || 0;
          onUsage({ ...stats });
        }
      } catch { /* Official protocol parser owns validation of provider events. */ }
      local.send(bytes, { binary });
    });
    remote.on('unexpected-response', (_request, response) => {
      onDiagnostic({ stage: 'websocket_upgrade', code: 'provider_http_error', status: response.statusCode });
      response.resume(); local.close(1011, `provider_http_${response.statusCode}`); remote.terminate();
    });
    remote.on('error', (error) => {
      onDiagnostic({ stage: 'websocket_transport', code: /^[A-Z0-9_]{1,64}$/.test(error.code ?? '') ? error.code : 'provider_transport_error' });
      local.close(1011, 'provider_transport_error');
    });
    local.on('error', () => remote.terminate());
    local.on('close', () => { remote.terminate(); pairs.delete(pair); pending = []; });
    remote.on('close', closeCode => {
      if (!closed && !limited && local.readyState === WebSocket.OPEN)
        onDiagnostic({ stage: 'websocket_close', source: planning ? 'planner' : monitor ? 'monitor' : 'foreground', code: 'provider_closed', closeCode });
      local.close(1011, 'provider_closed'); pairs.delete(pair); });
  });
  return { endpoint: `ws://127.0.0.1:${server.address().port}`, token, stats, maxGenerations: config.maxGenerations,
    async close() {
      if (closed) return;
      closed = true;
      for (const { local, remote } of pairs) { local.terminate(); remote.terminate(); }
      pairs.clear();
      await new Promise((resolve) => server.close(resolve));
    } };
}
