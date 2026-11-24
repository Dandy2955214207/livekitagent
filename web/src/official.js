import { createOfficialAudio } from './official-audio.js';
import { createGuideTarget } from './guide-target.js';

export function createOfficialPanel(send, { onTranscript, onState, onError, onBlocked, onTargetAvailable }) {
  const panel = document.getElementById('official-panel');
  const audio = createOfficialAudio(send, { onBlocked });
  let callId, caps, scene, requirements;
  const guideTarget = createGuideTarget((point, view) => sceneControl('target', { point, view }), onTargetAvailable);
  const scenePanel = document.getElementById('scene-panel');
  async function sceneControl(action, extra = {}) {
    if (!scene?.active) return;
    try { await send({ type: 'scene_control', callId, epoch: 1, requestId: crypto.randomUUID(), id: scene.id, revision: scene.revision, action, ...extra }); }
    catch { onError('任务操作未发送，请检查连接。'); }
  }
  document.getElementById('scene-confirm').onclick = () => sceneControl('confirm_step');
  document.getElementById('scene-repeat').onclick = () => sceneControl('repeat_step');
  document.getElementById('scene-pause').onclick = () => sceneControl(scene?.status === 'paused' ? 'resume' : 'pause');
  document.getElementById('scene-stop').onclick = () => sceneControl('stop');
  document.getElementById('scene-depth').onchange = event => sceneControl('depth', { depth: event.target.value });
  function renderScene(event) {
    guideTarget.update(event);
    scene = event; scenePanel.hidden = !event.id;
    document.getElementById('scene-title').textContent = `${event.mode === 'teach' ? '分步教学' : '主体讲解'}：${event.goal ?? ''}`;
    document.getElementById('scene-step').textContent = event.mode === 'teach' ?
      event.total ? `第 ${event.step}/${event.total} 步：${event.instruction ?? ''}` : '正在制定完整教学步骤' :
      `${event.target?.status === 'locked' ? event.target.preferred ? '优先讲解' : '当前主体' : '上次确认主体'}：${event.subject || '等待清晰画面'} · 已讲 ${event.segments ?? 0} 段`;
    const statuses = { starting: '正在准备', planning: '正在生成教学计划，请稍候', instructing: '正在安排当前步骤指引', waiting_step: '等待你完成当前步骤；看不清时也可点击“这一步已完成”',
      advancing: '当前步骤已满足，正在安排下一段指导', watching: '持续关注当前主体，换主体后更新讲解', paused: '任务已暂停', completed: '全部完成', cancelled: '已结束', failed: '任务启动失败' };
    const targetStatus = event.mode === 'guide' && event.status === 'watching' ? {
      ambiguous: '目标不明确，请点选画面或说出要介绍的物品。', candidate: '正在确认新的讲解主体。',
      selecting: '正在确认您指定的物品，请保持镜头稳定。',
      confirming_exit: '正在确认原物品是否已离开画面。',
      searching: event.target?.reason === 'selection_expired' ? '未确认点选物品，已恢复自动选择。' : '等待清晰的主体画面。',
    }[event.target?.status] : undefined;
    document.getElementById('scene-status').textContent = event.reason === 'budget' ? '观察额度已用完，任务暂停，进度未标记完成。' :
      event.reason === 'delivery_or_observer_failed' ? `观察或播报中断，任务已暂停；已完成 ${event.completedSteps ?? 0} 步，继续时从保留的进度接上。` : event.moving && event.status === 'watching' ? '镜头已移动，等待主体稳定后再讲解' : targetStatus ?? statuses[event.status] ?? event.status;
    document.getElementById('scene-confirm').hidden = event.mode !== 'teach' || event.status !== 'waiting_step';
    document.getElementById('scene-repeat').hidden = event.mode !== 'teach' || !event.active || event.status === 'planning';
    document.getElementById('scene-repeat').disabled = event.reason === 'budget';
    document.getElementById('scene-pause').hidden = !event.active || event.status === 'planning';
    document.getElementById('scene-pause').textContent = event.status === 'paused' ? '继续' : '暂停';
    document.getElementById('scene-pause').disabled = event.reason === 'budget';
    document.getElementById('scene-stop').hidden = !event.active;
    document.getElementById('scene-depth-label').hidden = event.mode !== 'guide' || !event.active;
    document.getElementById('scene-depth').value = event.depth ?? 'normal';
  }
  const intents = new Map(), intentViews = new Map();
  function renderRequirements() {
    const list = document.getElementById('official-tasks'); list.replaceChildren(); intentViews.clear();
    const statuses = { provisioning: '准备中', running: '等待条件', delivering: '提醒中', paused: '已暂停', completed: '已完成', cancelled: '已取消', failed: '失败' };
    for (const task of requirements?.auxiliary ?? []) {
      const row = document.createElement('li'), label = document.createElement('p'), detail = document.createElement('p');
      label.textContent = `${task.title} · ${statuses[task.status] ?? task.status}`;
      detail.textContent = `${task.kind === 'timer' ? '定时提醒' : task.repeat ? '每次独立事件' : '只提醒一次'} · 已播放 ${task.deliveredCount} 次${task.delivery === 'undelivered' ? ' · 上次播报中断' : ''}`;
      if (task.reason === 'budget') detail.textContent += ' · 观察额度已用完';
      const observation = intents.get(`proactive:${task.taskId}`);
      if (observation && task.status === 'running') detail.textContent += ` · ${phases[observation.phase] ?? '观察中'}`;
      row.append(label, detail);
      if (task.condition) { const p = document.createElement('p'); p.textContent = `条件：${task.condition}`; row.append(p); }
      if (!['completed', 'cancelled', 'failed'].includes(task.status)) {
        const add = (label, action, patch = {}) => {
          const button = document.createElement('button'); button.className = 'secondary'; button.textContent = label;
          button.onclick = async () => {
            button.disabled = true;
            try { await send({ type: 'requirement_control', callId, epoch: 1, requestId: crypto.randomUUID(),
              id: task.id, revision: task.revision, action, ...patch }); }
            catch { onError('任务操作未发送，请检查连接。'); }
            finally { button.disabled = false; }
          }; row.append(button);
        };
        if (task.reason !== 'budget') add(task.status === 'paused' ? '继续' : '暂停', task.status === 'paused' ? 'resume' : 'pause');
        add('结束任务', 'stop');
        if (task.kind === 'monitor' && ['running', 'paused'].includes(task.status)) add(task.repeat ? '改为只提醒一次' : '改为每次出现提醒', 'update', { repeat: !task.repeat });
      }
      list.append(row);
    }
  }
  const phases = { waiting_for_media: '等待新画面', uncertain: '画面不确定，继续观察',
    invalid_evidence: '证据格式无效，继续观察', stale_evidence: '证据已过时，等待新画面',
    missing_before: '尚无明确前态，继续观察', observer_error: '观察暂时失败',
    cooldown: '本次事件已触发，短暂冷却', suppressed_awaiting_false: '等待条件解除后再次出现',
    rearmed_false: '条件已解除，等待下一次出现', no_trigger: '条件尚未满足',
    notification_accepted: '本次事件提醒已排队', notification_rejected: '提醒投递失败',
    budget_paused: '本次观察额度已用完，观察暂停；可继续对话或重新加入', error: '观察暂时失败' };
  function renderIntent(id) {
    const state = intents.get(id), view = intentViews.get(id);
    if (!state || !view) return;
    const rule = state.kind === 'narration' ? '连续解说' : state.kind === 'timer' ? '定时提醒' :
      state.repeat ? '每次独立事件（解除后再出现）' : '只提醒一次';
    view.detail.textContent = `${rule} · 已触发 ${state.triggerCount ?? 0} 次 · 已确认播放 ${state.deliveredCount ?? 0} 次`;
    if (['running', 'provisioning'].includes(state.status)) view.detail.textContent += ` · ${phases[state.phase] ?? '观察中'}`;
    view.condition.textContent = state.condition ? `条件：${state.condition.replace(/^\[(state|transition)\]\s*/i, '')}` : '';
    view.timing.textContent = Number.isFinite(state.evaluationMs) ? `最近一次观察判定 ${(state.evaluationMs / 1000).toFixed(1)} 秒` : '';
    view.repeat.hidden = !['state', 'transition', 'auto'].includes(state.kind) || !['running', 'provisioning'].includes(state.status);
    view.repeat.disabled = state.status !== 'running' || state.phase === 'budget_paused';
    view.repeat.textContent = state.repeat ? '改为只提醒一次' : '改为每次出现提醒';
  }
  return {
    selectTarget: guideTarget.selectAt,
    prepare: () => audio.prepare(),
    activate(call) {
      callId = call?.harness_mode === 'qwen_live_official' ? call.session_id : undefined;
      panel.hidden = !callId;
      intents.clear(); intentViews.clear(); delete panel.dataset.playback;
      scene = undefined; requirements = undefined; scenePanel.hidden = true; guideTarget.reset();
      document.getElementById('official-tasks').replaceChildren();
      if (callId) {
        audio.activate(callId);
        caps = call.official_harness_capabilities;
        document.getElementById('official-notice').textContent = caps?.proactive_enabled ?
          '可请求持续讲解物品，或一步一步指导直到完成；当前模式和步骤会显示在这里。也支持看到物品时提醒。' : '官方核心直接对话。观察与提醒未开启。';
        document.getElementById('official-notice').textContent =
          `当前模型服务：${caps?.provider === 'aliyun' ? '百炼' : '一步 API'}。` + document.getElementById('official-notice').textContent;
        document.getElementById('official-budget').textContent = `本次最多 ${caps?.max_generations} 次生成，其中后台最多 ${caps?.max_monitor_generations} 次，${caps?.max_call_seconds} 秒后结束。`;
      }
    },
    handle(event) {
      if (!callId || event.callId !== callId || event.epoch !== 1) return;
      if (['audio', 'audio_end', 'audio_clear'].includes(event.type)) audio.handle(event);
      else if (event.type === 'playback') panel.dataset.playback = event.status;
      else if (event.type === 'transcript') onTranscript(event);
      else if (event.type === 'state') onState(event.state === 'starting' ? 'connecting' : event.state);
      else if (event.type === 'fatal') onError(event.message || '本次通话中断，请重新加入。');
      else if (event.type === 'recovery') document.getElementById('official-notice').textContent =
        event.phase === 'completed' ? (event.repeatInput ? '连接已恢复，请再说一次刚才的请求。' : '模型连接已恢复，可以继续。') : '正在恢复模型连接，当前任务进度已保留。';
      else if (event.type === 'usage') {
        document.getElementById('official-budget').textContent = `已用 ${event.generations}/${caps?.max_generations} 次生成，后台 ${event.monitorGenerations}/${caps?.max_monitor_generations} 次。通话上限 ${caps?.max_call_seconds} 秒。`;
      } else if (event.type === 'scene') renderScene(event);
      else if (event.type === 'requirements') { requirements = event; renderRequirements(); }
      else if (event.type === 'intent') {
        intents.set(event.taskId, event);
        while (intents.size > 64) intents.delete(intents.keys().next().value);
        renderIntent(event.taskId);
        if (requirements) renderRequirements();
      } else if (event.type === 'control_result' && event.ok === false) onError('任务状态已改变，规则未修改，请稍后重试。');
      else if (event.type === 'tasks') {
        if (requirements) return;
        const list = document.getElementById('official-tasks'); list.replaceChildren();
        intentViews.clear();
        const states = { queued: '排队中', monitoring: '观察中', running: '运行中', waiting: '等待中', delivering: '投递中', completed: '任务完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' };
        const notifications = { queued: '提醒排队中', preparing: '准备提醒', speaking: '正在播放提醒', delivered: '浏览器已确认播放完成', undelivered: '提醒未确认播放完成' };
        for (const task of event.tasks ?? []) {
          const row = document.createElement('li'), label = document.createElement('p');
          label.textContent = `${task.title} · ${states[task.status] ?? task.status}${task.notification ? ` · ${notifications[task.notification] ?? task.notification}` : ''}`;
          row.append(label);
          const detail = document.createElement('p'), condition = document.createElement('p'), timing = document.createElement('small');
          const repeat = document.createElement('button'); repeat.className = 'secondary'; repeat.hidden = true;
          repeat.onclick = async () => {
            const state = intents.get(task.id); if (!state) return;
            repeat.disabled = true;
            try { await send({ type: 'task_repeat', callId, epoch: 1, requestId: crypto.randomUUID(),
              taskId: task.id, generation: state.generation, repeat: !state.repeat }); }
            catch { onError('规则修改未发送，请检查连接。'); }
            finally { renderIntent(task.id); }
          };
          row.append(condition, detail, timing, repeat);
          intentViews.set(task.id, { detail, condition, timing, repeat }); renderIntent(task.id);
          if (task.output) { const output = document.createElement('p'); output.textContent = task.output; row.append(output); }
          if (task.canStop) {
            const button = document.createElement('button'); button.className = 'secondary'; button.textContent = '结束任务';
            button.onclick = async () => {
              button.disabled = true;
              try { await send({ type: 'task_stop', callId, epoch: 1, requestId: crypto.randomUUID(), taskId: task.id }); }
              catch { button.disabled = false; onError('任务操作未发送，请检查连接。'); }
            };
            row.append(button);
          }
          list.append(row);
        }
      }
    },
    async reset() { callId = undefined; scene = undefined; requirements = undefined; scenePanel.hidden = true; guideTarget.reset(); intents.clear(); intentViews.clear(); panel.hidden = true; await audio.reset(); },
  };
}
