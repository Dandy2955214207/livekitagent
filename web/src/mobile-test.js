export function createMobileTest({ checkbox, mark, status }) {
  let current;
  const eventsPerBatch = 20;

  async function upload(run, keepalive = false) {
    if (!run.id || !run.queue.length || (run.uploading && !keepalive)) return;
    const batch = run.queue.slice(0, eventsPerBatch).map(event => ({
      ...event, alignedAt: event.at + run.offset,
    }));
    if (!keepalive) run.uploading = true;
    try {
      const response = await fetch(`/api/mobile-tests/${run.id}/events`, {
        method: 'POST', headers: { Authorization: `Bearer ${run.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(batch), ...(keepalive ? { keepalive } : { signal: AbortSignal.timeout(5000) }),
      });
      if (!response.ok) throw new Error('upload_failed');
      run.queue = run.queue.filter(event => event.seq > batch.at(-1).seq);
    } catch {
      // Leave the batch queued for the next attempt.
    } finally {
      if (!keepalive) run.uploading = false;
      if (!run.queue.length || run.ended && Date.now() - run.ended > 60000) {
        if (run.ended) clearInterval(run.timer);
      }
      if (current === run) show(run);
    }
  }

  function show(run) {
    if (!run.id) return;
    const expired = run.ended && Date.now() - run.ended > 60000;
    const state = run.ended ? run.queue.length ? expired ? '手机事件同步中断' : '正在同步' : '测试已保存' : '记录中';
    status.textContent = `${state} · ${run.id.slice(0, 8)}${run.dropped ? ' · 部分手机事件缺失' : ''}`;
  }

  function event(kind, detail = '', links = {}) {
    if (!current || current.ended) return;
    const seq = ++current.seq;
    if (seq > 10000 || current.queue.length >= 400) {
      current.dropped++;
      show(current);
      return;
    }
    current.queue.push({ seq, at: Date.now(), kind, detail: String(detail).slice(0, 500), ...links });
  }

  mark.addEventListener('click', () => {
    event('mark', '手机测试中标记的问题');
    if (current) status.textContent = `已标记当前时刻 · ${current.id?.slice(0, 8) ?? ''}`;
  });

  return {
    async start() {
      current = undefined;
      if (!checkbox.checked) { status.textContent = ''; return undefined; }
      const run = { queue: [], seq: 0, dropped: 0 };
      current = run;
      status.textContent = '正在开启测试记录…';
      event('join', '点击加入');
      const started = Date.now();
      try {
        const response = await fetch('/api/mobile-tests', { method: 'POST', signal: AbortSignal.timeout(5000) });
        if (response.status === 404 || response.status === 405) {
          throw new Error('当前服务缺少测试记录接口。请在电脑上重启官方 Harness 手机服务，再刷新本页。');
        }
        const { id, token, serverAt, detail } = await response.json();
        if (!response.ok) throw new Error(detail);
        Object.assign(run, { id, token, offset: Math.round(serverAt - (started + Date.now()) / 2) });
        if (current === run && !run.ended) mark.hidden = false;
        run.timer = setInterval(() => { void upload(run); }, 1000);
        void upload(run);
        return { id, token };
      } catch (error) {
        if (current === run) {
          current = undefined;
          status.textContent = '测试记录开启失败';
        }
        throw new Error(error.message || '无法开启手机测试记录。');
      }
    },
    event,
    end(detail, keepalive = false) {
      const run = current;
      if (!run || run.ended) return;
      if (run.queue.length >= 400) { run.queue.shift(); run.dropped++; }
      event('end', run.dropped ? `${detail} · 手机事件丢失 ${run.dropped} 条` : detail);
      run.ended = Date.now();
      checkbox.checked = false;
      mark.hidden = true;
      void upload(run, keepalive);
      show(run);
    },
  };
}
