import './mobile-test-report.css';
import { createTimeline, inTimelineMode, isConversation } from './mobile-test-timeline.js';

const $ = id => document.getElementById(id);
const root = '/api/test-reports';
const labels = {
  'turn.started': '检测到用户开始说话', 'turn.transcript': '用户转写',
  'response.created': '模型开始响应', 'response.text': '模型输出文字', 'response.done': '模型响应结束',
  'response.barge_in': '用户插话打断响应',
  'response.tool': '模型调用工具', 'output.created': 'Worker 首段输出', 'output.end': 'Worker 输出结束',
  'output.cleared': 'Worker 清除输出', 'frame.bridge': 'Worker 交给 Harness 的帧', 'frame.harness': 'Harness 收到帧',
  'frame.foreground_queue': '前台图像入队', 'provider.image_sent': '图像已发往模型服务',
  'provider.input_committed': '模型服务确认输入轮次', 'provider.tool_output_sent': '工具结果已发往模型服务',
  'provider.tool_output_ack': '模型服务确认工具结果', 'vision.request': '视觉判断请求',
  'vision.result': '视觉判断结果', 'memory.frame': '采样帧进入记忆',
  'memory.summary_started': '开始生成视觉摘要', 'memory.summary_written': '视觉摘要写入',
  'memory.summary_gap': '视觉摘要缺口', 'memory.summary_evicted': '视觉摘要淘汰',
  'memory.indexed': '记忆向量索引完成', 'memory.index_failed': '记忆向量索引失败',
  'memory.question_snapshot': '发问时的记忆快照', 'memory.tool_called': '历史查询参数',
  'memory.query_range': '查找范围', 'memory.review_started': '回看近期帧',
  'memory.review_result': '近期回看结果', 'memory.text_search': '文本检索过程',
  'memory.vision_request': '记忆模型图像请求', 'memory.vision_response': '记忆模型原始回复',
  'memory.timeline_search': '通话时间线检索', 'memory.tool_result': '返回给模型的记忆内容',
  'memory.timeline_written': '通话记忆写入', 'memory.timeline_evicted': '通话记忆淘汰',
  'official.scene': '场景任务状态', 'official.intent': '提醒任务状态', 'official.latency': '分段耗时',
  'official.ready': 'Harness 已就绪', 'official.fatal': '通话严重错误',
  'phone.control_received': 'Worker 收到手机控制或回执', join: '点击加入',
  mark: '手机问题标记', end: '手机测试结束', error: '手机错误', playback: '手机播放状态',
  'audio.received': '手机收到首段音频', 'audio.end': '手机收到音频结束包', 'audio.clear': '手机清除音频',
  'camera.request': '摄像头操作', 'camera.state': '摄像头结果', 'camera.sampling': '视频采样状态',
  'room.connected': '手机已连接', 'room.reconnecting': '手机重连中', 'room.reconnected': '手机已重连',
  'room.disconnected': '手机连接中断', 'microphone.ready': '麦克风已就绪', 'microphone.change': '麦克风操作',
  control: '手机任务操作', 'call.worker_error': 'Worker 异常', 'call.worker_finished': 'Worker 通话结束',
};
const playbackLabels = {
  playback_started: '开始播放', playback_completed: '已播完', playback_blocked: '播放器受阻',
};
const recallLabels = { no_matching_history: '未找到匹配证据', scope_not_found: '未找到指代的范围',
  ambiguous_scope: '指代范围有歧义', invalid_query: '查询参数无效', query_failed: '查询失败', history: '已找到证据' };
const formatter = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' });
let report, requestVersion = 0;

function node(tag, text, cls) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (cls) element.className = cls;
  return element;
}

async function fetchReport(url, format = 'json') {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error('读取测试报告失败');
  return response[format]();
}

function decode(raw) {
  const unique = new Map();
  let partial = 0;
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue;
    try {
      const event = JSON.parse(line);
      unique.set(event.seq, { ...event, time: event.data?.nodeAt ?? event.alignedAt ?? event.at });
    } catch { partial++; }
  }
  const events = [...unique.values()].sort((a, b) => a.seq - b.seq);
  const gaps = events.reduce((sum, event, index) => sum + Math.max(0, event.seq - (events[index - 1]?.seq ?? 0) - 1), 0);
  return { events, incomplete: gaps + partial };
}

function groupTurns(events) {
  const turns = new Map();
  const byResponse = new Map(), byOutput = new Map(), byTool = new Map();
  for (const event of events) {
    const data = event.data;
    if (!data?.turnId) continue;
    if (!turns.has(data.turnId)) turns.set(data.turnId, { id: data.turnId, events: [], start: event.time });
    if (data.responseId) byResponse.set(data.responseId, data.turnId);
    if (data.outputId) byOutput.set(data.outputId, data.turnId);
    if (data.toolCallId) byTool.set(data.toolCallId, data.turnId);
  }
  for (const { data } of events) {
    if (data?.outputId && byResponse.has(data.responseId)) byOutput.set(data.outputId, byResponse.get(data.responseId));
    if (data?.toolCallId && byResponse.has(data.responseId)) byTool.set(data.toolCallId, byResponse.get(data.responseId));
  }
  for (const event of events) {
    const data = event.data ?? event;
    const turn = turns.get(data.turnId ?? byResponse.get(data.responseId) ?? byTool.get(data.toolCallId) ?? byOutput.get(data.outputId));
    if (!turn) continue;
    turn.events.push(event);
    if (event.kind === 'turn.transcript') turn.text = data.text;
  }
  return [...turns.values()].sort((a, b) => a.start - b.start);
}

function seconds(time) {
  return `${(Math.max(0, time - report.startedAt) / 1000).toFixed(2)} 秒`;
}

function turnSummary(turn) {
  const grouped = kind => turn.events.filter(row => row.kind === kind);
  const recalls = grouped('memory.tool_result');
  const frames = grouped('provider.input_committed').reduce((total, row) => total + (row.data.frameKeys?.length ?? 0), 0);
  const outputs = grouped('response.text').filter(row => !row.data.suppressed);
  const playback = grouped('playback').at(-1);
  const cleared = grouped('output.cleared').at(-1);
  const outcome = playback?.detail === 'playback_completed' ? '已播完' :
    cleared?.data.status === 'interrupted' ? '播放中断' : cleared?.data.status === 'discarded' ? '已清除待播音频' :
      playbackLabels[playback?.detail] ?? '未确认手机播放结果';
  const recall = recalls.at(-1)?.data;
  const search = grouped('memory.tool_called').length;
  const matching = grouped('memory.text_search').at(-1)?.data;
  const status = recallLabels[recall?.result.status] ?? recall?.result.status;
  const evidenceGap = recall?.result.pending ? ' · 有待处理的摘要' :
    matching?.stored?.length && !matching.eligible.length ? ' · 已收录的视觉摘要均在查询范围外' :
      matching?.eligible?.length && !matching.ranked.length ? ' · 范围内的视觉摘要未匹配' : '';
  const notes = [
    ['图像输入', frames ? `输入轮次关联到 ${frames} 张提交帧` : '本轮没有带帧的输入提交记录'],
    ['历史查询', recall ? `${status} · 返回 ${recall.result.records.length} 条 · ${recall.submitted ? '应用提交成功' : '应用提交失败'}${evidenceGap}`
      : search ? '查询已触发，返回证据缺失' : '未观察到历史查询调用'],
    ['手机播放', outcome],
  ];
  const panel = node('div', undefined, 'facts');
  for (const [label, value] of notes) {
    const card = node('div'); card.append(node('strong', label), node('p', value)); panel.append(card);
  }
  const result = node('div', undefined, 'answer');
  result.append(node('strong', '回答转写'), node('p', outputs.map(row => row.data.text).join(' ') || '未取得回答转写'));
  return [panel, result];
}

function frameKeys(event) {
  const data = event.data ?? {};
  return data.frameKeys ?? (data.frameKey ? [data.frameKey] :
    data.frameId && event.kind.startsWith('frame.') ? [report.frames.get(data.frameId)] :
      data.frameIds && event.kind.startsWith('memory.') ? data.frameIds.map(id => report.memoryFrames.get(id)) : []);
}

function frameUrl(key) {
  return `${root}/${report.id}/frames/${key}`;
}

function previewFrames(event) {
  const data = event.data ?? {}, keys = frameKeys(event);
  if (!keys.length) return;
  const gallery = node('div', undefined, 'frames');
  keys.forEach((key, index) => {
    const tile = node('figure');
    if (key) {
      const source = frameUrl(key);
      const link = node('a'); link.href = source; link.target = '_blank'; link.rel = 'noopener';
      const img = node('img'); img.loading = 'lazy'; img.alt = `证据帧 ${index + 1}`; img.src = source;
      img.onerror = () => link.replaceWith(node('span', '图像文件缺失', 'missing'));
      link.append(img); tile.append(link);
    } else tile.append(node('span', '图像记录缺失', 'missing'));
    tile.append(node('figcaption', data.frameId ? `帧 ${data.frameId}` : `第 ${index + 1} 张`));
    gallery.append(tile);
  });
  return gallery;
}

function memoryCandidates(data) {
  const candidates = node('ul', undefined, 'evidence-list');
  const eligible = new Set(data.eligible), ranked = new Set(data.ranked), selected = new Set(data.selected);
  for (const id of data.stored ?? []) {
    const row = report.memory.get(id);
    if (!row) continue;
    const state = selected.has(id) ? '已选入' : ranked.has(id) ? '匹配后未选入' : eligible.has(id) ? '范围内未匹配' : '范围外';
    candidates.append(node('li', `${state} · ${row.start.toFixed(1)}–${row.end.toFixed(1)} 秒 · ${row.text}`));
  }
  return candidates;
}

function describe(event) {
  const data = event.data ?? {};
  const title = event.kind === 'turn.transcript' ? '用户提问' :
    event.kind === 'response.text' && !data.suppressed ? '模型回答' : labels[event.kind] ?? event.kind;
  const observation = [data.observation?.subject, data.observation?.summary || data.observation?.reason].filter(Boolean).join(' · ');
  const task = [data.subject, data.target?.status, data.delivery].filter(Boolean).join(' · ');
  const image = data.width && data.height ? `帧 ${data.frameId} · ${data.width} × ${data.height}` : '';
  const result = data.result?.status;
  const recall = result && `${recallLabels[result] ?? result} · ${data.result.records?.length ?? 0} 条证据`;
  const text = playbackLabels[event.detail] ?? (event.detail || undefined) ?? data.text ??
    (observation || recall || task || image || data.error || data.code ||
      (Number.isFinite(data.ms) && data.metric ? `${data.metric} · ${Math.round(data.ms)} 毫秒` : data.reason ?? data.phase ?? data.status));
  return { title, text: text === undefined ? '' : String(text) };
}

function displayEvent(event) {
  const data = event.data ?? {};
  const isBad = event.kind === 'error' || event.kind.endsWith('_failed') || data.observation?.state === 'unknown' ||
    data.result?.status === 'no_matching_history' || data.submitted === false;
  const item = node('article', undefined, `event${isBad ? ' attention' : ''}`);
  item.append(node('time', seconds(event.time)));
  const body = node('div');
  const { title, text } = describe(event);
  body.append(node('h3', title));
  if (text) body.append(node('p', text));
  if (event.kind === 'memory.text_search') body.append(memoryCandidates(data));
  if (event.kind === 'memory.question_snapshot') {
    body.append(node('p', `当时已收录 ${data.availableIds.length} 条视觉摘要 · ${data.pendingFrameIds.length} 帧仍待摘要`));
    if (data.availableIds.length) {
      const details = node('details');
      const list = node('ul');
      for (const id of data.availableIds) {
        const memory = report.memory.get(id);
        if (memory) list.append(node('li', `${memory.start.toFixed(1)}–${memory.end.toFixed(1)} 秒 · ${memory.text}`));
      }
      details.append(node('summary', '查看发问时已收录的摘要'), list);
      body.append(details);
    }
  }
  const records = data.records ?? data.result?.records;
  if (Array.isArray(records) && records.length) {
    const list = node('ul', undefined, 'evidence-list');
    for (const record of records) {
      if (!record.text) continue;
      const evidence = node('li', `${record.text}${record.certainty ? ' · ' + record.certainty : ''}`);
      if (record.firstFrame !== undefined) {
        const frames = Array.from({ length: Math.min(30, record.lastFrame - record.firstFrame + 1) },
          (_, index) => report.memoryFrames.get(record.firstFrame + index));
        const links = node('details');
        links.append(node('summary', `来源帧 ${record.firstFrame}–${record.lastFrame}`));
        const gallery = previewFrames({ data: { frameKeys: frames } });
        if (gallery) links.append(gallery);
        evidence.append(links);
      }
      list.append(evidence);
    }
    body.append(list);
  }
  const gallery = previewFrames(event);
  if (gallery) body.append(gallery);
  const raw = node('details', undefined, 'raw');
  raw.append(node('summary', '事件详情'),
    node('pre', JSON.stringify(event.data ?? event, null, 2)));
  body.append(raw);
  item.append(body);
  return item;
}

function eventPicker(events, first, title) {
  const detail = node('div', undefined, 'picked-event');
  detail.replaceChildren(displayEvent(first));
  if (events.length < 2) return detail;
  const label = node('label', title, 'event-picker');
  const select = node('select');
  events.forEach((event, index) => {
    const description = describe(event);
    const excerpt = description.text ? ` · ${description.text.slice(0, 60)}` : '';
    const option = node('option', `${seconds(event.time)} · ${description.title}${excerpt}`);
    option.value = index;
    select.append(option);
  });
  select.value = events.indexOf(first);
  select.onchange = () => detail.replaceChildren(displayEvent(events[Number(select.value)]));
  label.append(select);
  const pane = node('div');
  pane.append(label, detail);
  return pane;
}

function inspect(point, mode) {
  $('inspector').hidden = !point;
  if (!point) return;
  const first = point.events[0], mark = point.type === 'mark';
  const turn = isConversation(first) ? report.turnByEvent.get(first) : undefined;
  const heading = mark ? point.label : point.events.length > 1 ? '邻近事件' : describe(first).title;
  const note = mark ? `${seconds(first.time)} · 标记前后 7 秒` : seconds(first.time);
  $('selection').replaceChildren(node('h2', heading), node('p', note));
  const summary = $('summary');
  summary.replaceChildren();
  if (turn) summary.append(...turnSummary(turn));
  const events = mark ? report.events.filter(row => Math.abs(row.time - first.time) <= 7000 && inTimelineMode(row, mode)) :
    turn ? turn.events.filter(row => inTimelineMode(row, mode)) : point.events;
  $('event-details').replaceChildren(eventPicker(events, first, mark ? '标记附近的事件' : turn ? '同一轮的事件' : '选择事件'));
}

const timeline = createTimeline({ describe, framePreview: event => {
  const key = frameKeys(event).find(Boolean);
  return key ? frameUrl(key) : undefined;
}, onSelect: inspect });

async function openRun(id, version) {
  const prefix = `${root}/${id}`;
  $('workspace').hidden = true;
  $('status').textContent = '正在读取测试记录…';
  try {
    const [worker, phone, state] = await Promise.all([fetchReport(prefix + '/events/worker', 'text'),
      fetchReport(prefix + '/events/phone', 'text'), fetchReport(prefix)]);
    if (version !== requestVersion) return;
    const left = decode(worker), right = decode(phone);
    const events = [...left.events, ...right.events].sort((a, b) => a.time - b.time || a.seq - b.seq);
    const frames = new Map(), memoryFrames = new Map(), memory = new Map();
    for (const row of left.events) {
      if (row.kind === 'frame.bridge') frames.set(row.data.frameId, row.data.frameKey);
      if (row.kind === 'memory.summary_written') row.data.records.forEach(record => memory.set(record.id, record));
    }
    for (const row of left.events) {
      if (row.kind === 'memory.frame') memoryFrames.set(row.data.memoryFrameId, frames.get(row.data.frameId));
    }
    const turnByEvent = new Map(groupTurns(events).flatMap(turn => turn.events.map(event => [event, turn])));
    report = { id, startedAt: Number($('run').selectedOptions[0].dataset.time), events, frames, memoryFrames, memory,
      turnByEvent, marks: events.filter(row => row.kind === 'mark') };
    const ended = right.events.some(row => row.kind === 'end');
    const workerEnded = left.events.some(row => row.kind === 'capture.worker_end');
    const dropped = left.events.some(row => row.data?.dropped || row.data?.traceDropped || row.data?.imageMissing);
    const incomplete = Boolean(left.incomplete || right.incomplete || dropped || state.writeError);
    const messages = [!ended || state.bound && !workerEnded ? '测试进行中或结束记录缺失' : '测试结束'];
    if (incomplete) messages.push('检测到记录缺口');
    if (!state.bound) messages.push('未绑定通话');
    messages.push('手机时间为估算对齐');
    $('status').textContent = messages.join(' · ');
    $('worker-file').href = prefix + '/events/worker';
    $('phone-file').href = prefix + '/events/phone';
    $('workspace').hidden = false;
    timeline.load(report);
  } catch (error) {
    if (version === requestVersion) $('status').textContent = error.message;
  }
}

async function refresh() {
  const version = ++requestVersion;
  const previous = $('run').value || new URLSearchParams(location.search).get('run');
  try {
    const runs = await fetchReport(root);
    if (version !== requestVersion) return;
    $('run').replaceChildren(...runs.map(run => {
      const option = node('option', `${formatter.format(run.startedAt)} · ${run.id.slice(0, 8)}`);
      option.value = run.id; option.dataset.time = run.startedAt;
      return option;
    }));
    if (runs.some(run => run.id === previous)) $('run').value = previous;
    if ($('run').value) await openRun($('run').value, version);
    else {
      $('workspace').hidden = true;
      $('status').textContent = '暂无记录。请在手机加入前勾选「记录本次测试」。';
    }
  } catch (error) {
    if (version === requestVersion) $('status').textContent = error.message;
  }
}

$('refresh').onclick = refresh;
$('run').onchange = () => { void openRun($('run').value, ++requestVersion); };
$('close-inspector').onclick = () => timeline.clear();
void refresh();
