// Local protocol fixture only. Never connects to a model account.
import { WebSocketServer } from 'ws';

export async function fakeProvider({ monitorReply, planReply, onRequest = () => {} } = {}) {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 });
  await new Promise((resolve) => server.once('listening', resolve));
  const peers = [], requests = [];
  let sequence = 0;
  const send = (peer, event) => peer.socket.send(JSON.stringify({ event_id: `e-${++sequence}`, ...event }));
  const created = (peer, item) => {
    send(peer, { type: 'conversation.item.created', previous_item_id: peer.lastItemId ?? null, item });
    peer.lastItemId = item.id;
  };
  server.on('connection', (socket, request) => {
    const peer = { socket, url: request.url, headers: request.headers, session: {}, audio: 0, images: 0,
      responses: 0, nextTool: undefined };
    peers.push(peer);
    send(peer, { type: 'session.created', session: { id: `session-${peers.length}` } });
    socket.on('message', (bytes) => {
      const event = JSON.parse(bytes.toString()); requests.push(event);
      onRequest(event, peer);
      if (event.type === 'session.update') {
        Object.assign(peer.session, event.session);
        send(peer, { type: 'session.updated', session: { ...peer.session, id: `session-${peers.indexOf(peer) + 1}` } });
      } else if (event.type === 'input_audio_buffer.append') peer.audio++;
      else if (event.type === 'input_image_buffer.append') peer.images++;
      else if (event.type === 'input_audio_buffer.commit') {
        const item = { id: `clip-${++sequence}`, type: 'message', role: 'user', status: 'completed', content: [{ type: 'input_audio' }] };
        created(peer, item);
        send(peer, { type: 'input_audio_buffer.committed', item_id: item.id });
      } else if (event.type === 'conversation.item.create') {
        created(peer, { ...event.item, id: event.item.id ?? `item-${++sequence}`, status: 'completed' });
      } else if (event.type === 'response.create') {
        const rid = `r-${peers.indexOf(peer)}-${++peer.responses}`;
        send(peer, { type: 'response.created', response: { id: rid, status: 'in_progress' } });
        let item;
        const output = [];
        if (peer.nextTool) {
          if (peer.beforeTool) {
            const preamble = { id: `p-${rid}`, type: 'message', role: 'assistant', status: 'completed',
              content: [{ type: 'audio', transcript: peer.beforeTool }] };
            created(peer, preamble);
            send(peer, { type: 'response.output_item.added', response_id: rid, output_index: 0, item: preamble });
            send(peer, { type: 'response.audio.delta', response_id: rid, item_id: preamble.id, delta: Buffer.alloc(4800).toString('base64') });
            send(peer, { type: 'response.audio_transcript.done', response_id: rid, item_id: preamble.id, transcript: peer.beforeTool });
            send(peer, { type: 'response.output_item.done', response_id: rid, output_index: 0, item: preamble });
            output.push(preamble);
            peer.beforeTool = undefined;
          }
          item = { type: 'function_call', id: `tool-${rid}`, call_id: `call-${rid}`, ...peer.nextTool, status: 'completed' };
          peer.nextTool = undefined;
          created(peer, item);
          send(peer, { type: 'response.output_item.added', response_id: rid, output_index: output.length, item });
          send(peer, { type: 'response.function_call_arguments.done', response_id: rid, item_id: item.id,
            call_id: item.call_id, name: item.name, arguments: item.arguments });
        } else if (peer.session.modalities?.length === 1) {
          const reply = peer.session.instructions?.startsWith('你为持续分步教学') ? (planReply?.() ?? JSON.stringify({ steps: [
            { instruction: '先画一个大圆作为头部', completion: '纸上出现闭合的大圆轮廓' },
            { instruction: '在大圆内画一个椭圆鼻子', completion: '大圆内部出现完整的椭圆鼻子' },
          ] })) : monitorReply?.(event, peer) ?? 'wait';
          item = { id: `m-${rid}`, type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'text', text: reply }] };
          created(peer, item);
          send(peer, { type: 'response.text.delta', response_id: rid, item_id: item.id, delta: reply });
          send(peer, { type: 'response.text.done', response_id: rid, item_id: item.id, text: reply });
        } else {
          item = { id: `m-${rid}`, type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'audio', transcript: '本地协议测试。' }] };
          created(peer, item);
          send(peer, { type: 'response.output_item.added', response_id: rid, output_index: 0, item });
          send(peer, { type: 'response.audio_transcript.delta', response_id: rid, item_id: item.id, delta: '本地协议测试。' });
          send(peer, { type: 'response.audio.delta', response_id: rid, item_id: item.id, delta: Buffer.alloc(4800).toString('base64') });
          send(peer, { type: 'response.audio_transcript.done', response_id: rid, item_id: item.id, transcript: '本地协议测试。' });
          send(peer, { type: 'response.audio.done', response_id: rid, item_id: item.id });
        }
        send(peer, { type: 'response.output_item.done', response_id: rid, output_index: output.length, item });
        send(peer, { type: 'response.done', response: { id: rid, status: 'completed', output: [...output, item], usage: { input_tokens: 12, output_tokens: 8 } } });
      }
    });
  });
  return { url: `ws://127.0.0.1:${server.address().port}/v1/realtime`, peers, requests, send,
    turn(peer, { text = '你好', tool, beforeTool } = {}) {
      const id = `user-${++sequence}`;
      peer.nextTool = tool;
      peer.beforeTool = beforeTool;
      created(peer, { id, type: 'message', role: 'user', status: 'completed', content: [{ type: 'input_audio' }] });
      send(peer, { type: 'input_audio_buffer.speech_started', item_id: id });
      send(peer, { type: 'input_audio_buffer.speech_stopped', item_id: id });
      send(peer, { type: 'input_audio_buffer.committed', item_id: id });
      send(peer, { type: 'conversation.item.input_audio_transcription.completed', item_id: id, transcript: text });
    }, async close() {
      for (const peer of peers) peer.socket.terminate();
      await new Promise((resolve) => server.close(resolve));
    } };
}

export async function until(predicate, timeout = 5000) {
  const end = Date.now() + timeout;
  while (!predicate()) {
    if (Date.now() > end) throw new Error('fixture_wait_timeout');
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}
