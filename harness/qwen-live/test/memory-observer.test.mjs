import test from 'node:test';
import assert from 'node:assert/strict';
import { createMemoryObserver } from '../src/memory-observer.mjs';

const config = { provider: 'aliyun', endpoint: 'wss://fixture.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime', apiKey: 'fixture' };
const frames = Array.from({ length: 30 }, (_, i) => ({ id: i + 1, seconds: i, image: `image-${i + 1}` }));

test('vision sends all archive and recall frames in separate single HTTP requests', async () => {
  const requests = [];
  const observer = createMemoryObserver(config, { request: async (url, init) => {
    requests.push({ url, body: JSON.parse(init.body) });
    return Response.json({ choices: [{ finish_reason: 'stop', message: {
      content: '[{"events":[{"first":1,"last":2,"text":"红色圆形","certainty":"observed"}]}]',
    } }] });
  } });
  try {
    const archive = await observer.observe(frames.slice(10, 20));
    assert.deepEqual(archive.map(row => [row.first, row.last]), [[11, 12]]);
    await observer.review(frames, { question: '刚才展示了什么' });
    assert.deepEqual(requests.map(({ body }) => body.messages[1].content.filter(block => block.type === 'image_url').length), [10, 30]);
    assert.match(requests[1].url, /compatible-mode\/v1\/chat\/completions$/);
    assert.equal(requests[1].body.messages[1].content[58].text, '第 30 张，通话第 29.00 秒');
  } finally { observer.close(); }
});

test('embeddings preserve API indices, normalize vectors and separate query from document', async () => {
  const observer = createMemoryObserver(config, { request: async (_url, init) => {
    const body = JSON.parse(init.body);
    assert.equal(body.parameters.text_type, 'document');
    return Response.json({ output: { embeddings: [1, 0].map(text_index => ({ text_index,
      embedding: Array.from({ length: 512 }, (_, index) => index === text_index ? 2 : 0),
    })) } });
  } });
  try {
    const vectors = await observer.embed(['笔', '鼠标'], 'document');
    assert.deepEqual(vectors.map(vector => Array.from(vector.slice(0, 2))), [[1, 0], [0, 1]]);
  } finally { observer.close(); }
});

test('closing aborts both HTTP vision requests', async () => {
  const observer = createMemoryObserver(config, { request: (_url, { signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  }) });
  const pending = Promise.allSettled([observer.observe(frames.slice(0, 10)), observer.review(frames, { question: '回看' })]);
  observer.close();
  assert.deepEqual((await pending).map(result => result.status), ['rejected', 'rejected']);
});
