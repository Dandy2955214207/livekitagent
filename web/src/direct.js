// Local task and memory controls never submit text to the speech model.
export function createDirectPanel(send) {
  const el = (id) => document.getElementById(id);
  const panel = el('direct-panel');
  let active = false, pending, timer;
  function buttons() {
    panel.querySelectorAll('button, input, textarea, select').forEach((node) => { node.disabled = !active || Boolean(pending); });
  }
  async function command(action, extra = {}) {
    if (!active || pending) return;
    const id = crypto.randomUUID(); pending = id; buttons();
    timer = setTimeout(() => {
      if (pending !== id) return;
      pending = undefined; buttons();
      el('direct-notice').textContent = '操作确认超时，可刷新本地状态核对。';
    }, 6000);
    try { await send({ request_id: id, action, ...extra }); }
    catch {
      if (pending !== id) return;
      clearTimeout(timer); pending = undefined; buttons(); el('direct-notice').textContent = '操作未能发送，请检查连接。';
    }
  }
  el('observation-create').onclick = () => command('observation_create', { content: el('observation-condition').value });
  el('observation-update').onclick = () => command('observation_update', { content: el('observation-condition').value });
  el('observation-cancel').onclick = () => command('observation_cancel');
  el('observation-pause').onclick = () => command('observation_pause');
  el('observation-resume').onclick = () => command('observation_resume');
  el('memory-save').onclick = () => command(el('memory-existing').value ? 'memory_correct' : 'memory_put',
    { content: el('memory-content').value, record_id: el('memory-existing').value });
  el('memory-select').onclick = () => command('memory_select', { library_id: el('memory-library').value.trim() || null });
  el('memory-toggle').onclick = () => command('memory_enabled', { enabled: el('memory-toggle').dataset.enabled !== 'true' });
  el('memory-clear').onclick = () => command('memory_clear');
  el('memory-query').onclick = () => command('memory_query', { content: el('memory-search').value });
  el('direct-refresh').onclick = () => command('status');
  return {
    activate(show, connected) {
      panel.hidden = !show;
      const wasActive = active;
      active = show && connected;
      if (!active) {
        clearTimeout(timer); pending = undefined;
        panel.querySelectorAll('input, textarea').forEach((node) => { node.value = ''; });
        el('memory-records').replaceChildren(); el('memory-results').textContent = '';
        el('observation-result').textContent = '';
        el('memory-existing').replaceChildren(new Option('保存新事实', ''));
      }
      buttons();
      if (active && !wasActive) void command('status');
    },
    update(data) {
      if (!active) return;
      if (!data.request_id && pending && !data.error) return;
      if (data.request_id && data.request_id !== pending) return;
      clearTimeout(timer); pending = undefined;
      el('direct-notice').textContent = data.error || data.notice || '';
      if (data.error) { buttons(); return; }
      const taskLabels = { active: '正在独立观察', paused: data.observation === 'experimental_text' ? '已暂停' : '已暂停，观察待接通', completed: '已检测目标', cancelled: '已取消', failed: '观察已停止' };
      el('observation-state').textContent = data.task ? `目标：${data.task.condition} · ${taskLabels[data.task.status] || data.task.status}` : '尚未登记目标';
      el('observation-create').textContent = data.observation === 'experimental_text' ? '开始文字观察' : '登记目标（暂停）';
      if (data.observation_budget) el('observation-budget').textContent = `独立观察会产生当前提供商用量，每通话最多${data.observation_budget}次后台生成；语音提醒尚未开放。`;
      const event = data.events?.at(-1);
      if (data.events) el('observation-result').textContent = event ? `${event.delivery === 'expired' ? '历史观察（已过期）' : '观察结果'}：${event.summary}。${event.evidence}（未语音播报）` : '';
      if (data.monitor) {
        const phase = { waiting_camera: '等待开启摄像头', waiting_frames: '等待新画面', unknown: '证据不足，继续观察', stale_result: '结果已过期，等待新画面', unavailable: '后台暂不可用', budget_paused: '本通话观察预算已用尽' }[data.monitor.phase];
        if (phase) el('observation-state').textContent += ` · ${phase}`;
      }
      const memory = data.memory;
      if (memory) {
        el('memory-state').textContent = `${memory.persistent ? '本地持久库：' : '本通话隔离记忆：'}${memory.library_id} · ${memory.enabled ? '启用' : '禁用'} · 尚未回注模型`;
        el('memory-toggle').dataset.enabled = String(memory.enabled);
        el('memory-toggle').textContent = memory.enabled ? '禁用记忆' : '启用记忆';
        // A query intentionally omits the management list to bound RTC packet size.
        if (!data.retrieval) {
          el('memory-records').replaceChildren();
          el('memory-existing').replaceChildren(new Option('保存新事实', ''));
          for (const record of memory.records) {
            el('memory-existing').add(new Option(record.content.slice(0, 40), record.memory_id));
            const row = document.createElement('li');
            const text = document.createElement('span'); text.textContent = record.content;
            const remove = document.createElement('button'); remove.className = 'text-button'; remove.textContent = '删除';
            remove.onclick = () => command('memory_delete', { record_id: record.memory_id });
            row.append(text, remove); el('memory-records').append(row);
          }
        }
      }
      if (data.retrieval) el('memory-results').textContent = data.retrieval.records.map((r) => r.content).join('\n') || '无相关本地记录';
      buttons();
    },
  };
}
