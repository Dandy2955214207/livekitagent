import { loadOfficial } from './official.mjs';
import { createGateway } from './gateway.mjs';
import { LiveKitHost } from './host.mjs';
import { createEvidenceMonitor } from './evidence-monitor.mjs';
import { IntentState } from './intent-state.mjs';
import { DeliveryWindow } from './delivery-window.mjs';
import { SceneTasks } from './scene-tasks.mjs';
import { loadSceneSkills, sceneRealtime } from './scene-tools.mjs';
import { GUIDE_PROMPT, validateGuide } from './guide-monitor.mjs';
import { createScenePlanner } from './scene-planner.mjs';

import { GuideMotion } from './guide-motion.mjs';
import { GuideOpening } from './guide-opening.mjs';
import { Requirements } from './requirements.mjs';
import { Latency } from './latency.mjs';
import { CallMemory, memoryRealtime } from './call-memory.mjs';
import { createMemoryObserver } from './memory-observer.mjs';
import { makeTestTrace, traceRealtime } from './test-trace.mjs';

export function displayTasks(snapshot) {
  const tasks = [];
  for (const task of snapshot.tasks.slice(0, 8)) {
    const row = { id: task.id, title: task.title.slice(0, 160), status: task.status,
      activity: task.activity.slice(0, 300), output: task.output.slice(0, 500),
      notification: task.notification, canStop: task.canStop ?? (task.id.startsWith('proactive:') &&
        ['starting', 'monitoring', 'running', 'waiting', 'delivering'].includes(task.status)) };
    if (Buffer.byteLength(JSON.stringify([...tasks, row]), 'utf8') > 11000) break;
    tasks.push(row);
  }
  return { revision: snapshot.revision, tasks, omitted: snapshot.tasks.length - tasks.length + (snapshot.omitted ?? 0) };
}

export async function createRuntime(config, emit, dependencies = {}) {
  if (!config.callId || !config.apiKey || !/^qwen[\w.-]+$/.test(config.model) ||
      !Number.isInteger(config.maxGenerations) || config.maxGenerations < 1 || config.maxGenerations > 80 ||
      !Number.isInteger(config.maxMonitorGenerations) || config.maxMonitorGenerations < 1 || config.maxMonitorGenerations > 60) {
    throw new Error('invalid_bridge_config');
  }
  const official = await loadOfficial();
  const trace = makeTestTrace(config, emit);
  const host = new LiveKitHost(emit, config);
  host.trace = trace;
  const latency = new Latency(data => host.send('latency', data)); host.latency = latency;
  const intents = new IntentState((state) => host.send('intent', state));
  let scheduler;
  let requirements;
  let memory;
  const guideMonitors = new Map();
  const stepMonitors = new Map();
  let captureKey, burstUntil = 0, lastBurst = -Infinity, lastForegroundAt = -Infinity;
  let latestGrid, speechReference;
  function startCaptureBurst(now) {
    lastBurst = now; burstUntil = now + 2000;
    host.send('capture_burst', { enabled: true });
  }
  function captureForScene(state) {
    if (state.mode !== 'guide' || state.status !== 'watching' || state.target?.status === 'locked') {
      if (burstUntil) host.send('capture_burst', { enabled: false });
      burstUntil = 0;
      if (!state.active || state.status === 'paused') captureKey = undefined;
      return;
    }
    const checkingExit = state.target?.status === 'confirming_exit';
    const key = `${state.id}:${state.target?.view}:${checkingExit}:${state.moving}`;
    if (key === captureKey) return;
    captureKey = key;
    const now = Date.now();
    if (!state.moving && !checkingExit && state.target?.status !== 'selecting' && now >= burstUntil && now - lastBurst < 3000) return;
    startCaptureBurst(now);
  }
  const scenes = new SceneTasks(() => scheduler, (state, completionSource) => {
    memory?.timeline.task(state, completionSource);
    captureForScene(state); host.send('scene', state); requirements?.publish();
  });
  requirements = new Requirements(scenes, state => {
    if (memory) for (const task of state.auxiliary) memory.timeline.task(task);
    host.send('requirements', state);
  });
  const skills = await loadSceneSkills();
  let stopped = false;
  const gateway = await createGateway(config, { ...dependencies.gateway,
    trace,
    onUsage: (usage) => host.send('usage', usage), onLimit: (code) => host.send('fatal', { code }),
    onDiagnostic: (details) => {
      if (details.code === 'model_access_denied') host.send('fatal', { code: details.code, status: 403 });
      if (details.code === 'monitor_generation_budget') { intents.budgetPaused(); scenes.pauseForBudget(); requirements.pauseForBudget(); }
      host.send('diagnostic', details);
    } });
  const planner = createScenePlanner(gateway);
  memory = config.memoryEnabled === true && (config.provider === 'aliyun' || dependencies.memoryObserver) ? new CallMemory(
    dependencies.memoryObserver ?? createMemoryObserver(config, { ...dependencies.memoryProvider,
      onUsage: usage => host.send('memory', { phase: 'usage', status: 'usage', ...usage }) }),
    { ...dependencies.memoryOptions, trace, onStatus: state => host.send('memory', state) }) : null;
  let openRealtime = official.openRecoveringQwenRealtimeSession;
  if (config.proactiveEnabled) openRealtime = sceneRealtime(openRealtime, scenes, skills, details => {
    dependencies.sceneDiagnostics?.(details);
    host.send('tool', { name: details.name, ...(details.code ? { code: details.code } : {}) });
  }, planner, requirements, {
    ended(itemId) {
      speechReference = itemId && latestGrid && Date.now() - latestGrid.at <= 1500 ?
        { itemId, grid: latestGrid.grid } : undefined;
    },
    reference(itemId) {
      if (speechReference && speechReference.itemId === itemId) return speechReference.grid;
      return latestGrid && Date.now() - latestGrid.at <= 1500 ? latestGrid.grid : undefined;
    },
  });
  if (memory) {
    host.timeline = memory.timeline;
    openRealtime = memoryRealtime(openRealtime, memory, host);
  }
  if (trace) openRealtime = traceRealtime(openRealtime, host, trace);
  const visual = { source: 'camera', mode: 'live-feed', fps: 1, liveWidth: 640, liveHeight: 480,
    cameraWidth: 640, cameraHeight: 480 };
  const motion = new GuideMotion(); let motionScene;
  let transcriptSequence = 0;
  const deliveryWindow = new DeliveryWindow((delivery, reason) => scheduler?.undeliverDelivery(delivery, reason));
  const proactive = structuredClone(official.DEFAULT_PROACTIVE_CONFIG);
  proactive.enabled = config.proactiveEnabled === true;
  // Two real frames remain mandatory. Short polling removes idle scheduling
  // delay; a short capture window prevents old queued clips trailing real time.
  proactive.scheduler.evalIntervalSec = 0.25;
  proactive.scheduler.repeat.cooldownSec = 1;
  proactive.scheduler.repeat.clearBufferOnResume = false;
  proactive.vision.windowSizeSec = 3;
  proactive.monitor.sessionRecycleEvals = 6;
  const allowEvaluation = () => {
    const reserve = config.maxGenerations >= 6 ? 4 : 0;
    const allowed = gateway.stats.monitorGenerations < config.maxMonitorGenerations &&
      gateway.stats.monitorInputTokens < 120000 &&
      gateway.stats.generations < config.maxGenerations - reserve;
    if (!allowed) { intents.budgetPaused(); scenes.pauseForBudget(); requirements.pauseForBudget(); }
    return allowed;
  };
  const log = { write(type, payload) {
    if (stopped) return;
    if (type === 'transcript.user' || type === 'transcript.assistant') {
      if (payload.audioSuppressed) return;
      host.send('transcript', { role: type === 'transcript.user' ? 'user' : 'assistant',
        id: payload.itemId ?? payload.responseId ?? `text-${++transcriptSequence}`, text: String(payload.text ?? '').slice(0, 8000) });
    } else if (type === 'vad.speech_started' || type === 'vad.speech_stopped') {
      if (type === 'vad.speech_started') {
        latency.userStarted();
      } else latency.userStopped();
      host.send(type === 'vad.speech_started' ? 'speech_started' : 'speech_stopped');
    } else if (type === 'realtime.protocol' && payload.type === 'transport.recovery') {
      host.send('recovery', { phase: payload.phase, code: payload.code, repeatInput: payload.inputReason === 'unavailable' });
    } else if (type === 'tool.call') {
      host.send('tool', { name: String(payload.name ?? payload.tool ?? '').slice(0, 80) });
    }
  }, async close() {} };
  // Explicit per-call adapters; no personal upstream configuration.
  const session = new official.LiveSession({ host, registry: new official.BackendRegistry([]),
    realtime: { endpoint: gateway.endpoint, apiKey: gateway.token, model: config.model, voice: config.voice },
    getLanguage: () => 'zh-CN', log, proactive, gracefulStopDrainMs: 500,
    openRealtime,
    createProactiveScheduler(options) {
      // LiveKit supplies real new frames; polling a cached image would fabricate
      // new evidence. Keep the official scheduler and Monitor, use feedImage only.
      scheduler = new official.ProactiveScheduler({ ...options, captureVision: undefined,
        onEvent(delivery) {
          if (memory) {
            const role = scenes.roleFor(delivery.taskId);
            const scene = role ? scenes.snapshot() : undefined;
            const nextStep = scene && scene.step < scene.total ? scene.step + 1 : undefined;
            const task = scene ? { id: scene.id, kind: scene.mode, label: scene.goal, subject: scene.subject,
              step: role === 'completion' ? nextStep : scene.step } :
              requirements.snapshot().auxiliary.find(row => row.taskId === delivery.taskId);
            memory.timeline.queue(delivery.deliveryId, task && { ...task, label: task.label ?? task.title });
          }
          latency.queued(delivery); requirements.onDelivery(delivery);
          scenes.onDelivery(delivery);
          // Timer reminders have no camera evidence and are not subject to this TTL.
          if (scheduler.listTasks().find(task => task.taskId === delivery.taskId)?.modalities?.includes('vision')) deliveryWindow.add(delivery);
          const accepted = options.onEvent(delivery);
          if (!accepted) {
            memory?.timeline.stopDelivery(delivery.deliveryId);
            deliveryWindow.remove(delivery); scenes.invalidated(delivery);
          }
          return accepted;
        },
        onDeliveryInvalidated(delivery) {
          memory?.timeline.stopDelivery(delivery.deliveryId);
          latency.retired(delivery); deliveryWindow.remove(delivery); scenes.invalidated(delivery); options.onDeliveryInvalidated?.(delivery);
        },
        onTaskChanged(task, notification) {
          intents.changed(task, notification);
          scenes.changed(task, notification);
          requirements.changed(task, notification);
          options.onTaskChanged?.(task, notification);
        },
        debug(event, details) { intents.debug(event, details); },
        createMonitor(monitorOptions, callbacks) {
          const role = scenes.roleFor(monitorOptions.taskId);
          const presentation = role === 'guide' ? scenes.state?.opening : undefined;
          const opening = presentation ? new GuideOpening(presentation.reference, Date.now()) : undefined;
          if (opening) scenes.state.opening = undefined;
          const monitor = createEvidenceMonitor(official.DashScopeRealtimeMonitor, monitorOptions, callbacks,
            { onObservation: observation => { intents.observed(observation); latency.observed(observation); }, allowEvaluation,
              trace,
              confirmations: role === 'completion' ? 2 : 1, intervalMs: role === 'completion' ? 4000 : role === 'guide' ? 2500 : 0,
              watchChanges: role === 'completion',
              ...(role === 'guide' ? { protocol: { prompt: GUIDE_PROMPT, validate: validateGuide,
                awaitingSubject: () => ['searching', 'candidate', 'selecting'].includes(scenes.target?.status),
                fastConfirmation: () => scenes.target?.status === 'confirming_exit' && Date.now() < burstUntil,
                view: () => opening ? (scenes.state?.view ?? 0) + ':' + opening.version : scenes.state?.view ?? 0,
                blocked: () => Boolean(scenes.state?.moving),
                ready() {
                  if (scenes.target?.selection || scenes.target?.status === 'selecting') opening?.finish();
                  return opening?.ready(Date.now());
                },
                requested: at => opening?.requested(at),
                context: () => scenes.monitorContext(monitorOptions.taskId) ?? {},
                observeUnknown: observation => scenes.acceptGuide(monitorOptions.taskId, observation),
                accept(observation) {
                  const accepted = scenes.acceptGuide(monitorOptions.taskId, observation);
                  if (['candidate', 'locked'].includes(scenes.target?.status)) opening?.finish();
                  return accepted;
                } } } : {}),
            });
          if (role === 'completion') {
            stepMonitors.set(monitorOptions.taskId, monitor);
            return { ...monitor, close() { stepMonitors.delete(monitorOptions.taskId); monitor.close(); } };
          }
          if (role !== 'guide') return monitor;
          // Only guide observers receive the burst frames. The official scheduler,
          // foreground model and teaching/reminder observers keep their 1 FPS feed.
          guideMonitors.set(monitorOptions.taskId, { monitor, opening });
          return { ...monitor, feedImage: () => true, close() {
            guideMonitors.delete(monitorOptions.taskId); monitor.close();
          } };
        },
      });
      requirements.install(scheduler);
      const announcementStarted = scheduler.announcementStarted.bind(scheduler);
      scheduler.announcementStarted = (delivery, ...args) => {
        memory?.timeline.linkDelivery(host.memoryResponse?.responseId, delivery.deliveryId);
        latency.preparing(delivery);
        return announcementStarted(delivery, ...args);
      };
      for (const method of ['playbackStarted', 'acknowledgeDelivery', 'failDelivery', 'undeliverDelivery']) {
        const original = scheduler[method].bind(scheduler);
        scheduler[method] = (delivery, ...args) => {
          deliveryWindow.remove(delivery);
          if (method !== 'playbackStarted') memory?.timeline.stopDelivery(delivery.deliveryId);
          if (method === 'acknowledgeDelivery') { scenes.acknowledged(delivery); requirements.delivered(delivery); latency.retired(delivery); }
          else if (method === 'playbackStarted') { scenes.playbackStarted(delivery); requirements.playbackStarted(delivery); }
          else if (method !== 'playbackStarted') scenes.invalidated(delivery);
          return original(delivery, ...args);
        };
      }
      return scheduler;
    },
    logger: { debugEnabled: false, debug() {}, info() {}, warn() {}, error() {} },
    onFailure(failure) {
      const safe = {};
      for (const key of ['source', 'code', 'stage', 'kind']) {
        if (/^[A-Za-z0-9_.-]{1,80}$/.test(failure[key] ?? '')) safe[key] = failure[key];
      }
      for (const key of ['status', 'closeCode']) if (Number.isInteger(failure[key])) safe[key] = failure[key];
      if (failure.impact === 'call' || failure.fatal) host.terminalDiagnostic = { ...safe, at: Date.now() };
      host.send('diagnostic', safe);
    },
    onSubagentsChanged(snapshot) {
      // Bound data packets and expose only display fields, not provider histories.
      host.send('tasks', displayTasks({ ...snapshot, tasks: snapshot.tasks.filter(task =>
        !scenes.ownedIds.has(task.id.replace(/^proactive:/, ''))) }));
    }, ...(dependencies.session ?? {}) });
  async function close() {
    if (stopped) return;
    stopped = true; host.closed = true; host.outputs.clear(); host.image = undefined;
    memory?.close();
    planner.close(); requirements.close(); scenes.close(); deliveryWindow.close(); session.dispose();
    await gateway.close();
  }
  return { session, host, gateway, scenes, requirements, memory, async start() {
    await session.start({ epoch: host.epoch, callId: config.callId, mode: 'new', visualInput: visual });
    // Scoped adapter for the pinned core's existing quiet-gap gate. All VAD,
    // response-in-flight and playback gates remain owned by the official Injector.
    const injector = session.active?.injector;
    if (injector) {
      const normalGap = injector.quietGapMs;
      Object.defineProperty(injector, 'quietGapMs', { get: () =>
        scenes.state?.mode === 'guide' && scenes.state.status === 'watching' && scenes.priorityUntil > Date.now() ? 150 : normalGap });
    }
    host.send('ready', { engine: 'qwen-live-harness', version: official.build.version,
      sourceSha256: official.build.sourceSha256, memory: memory ? 'call_visual' : 'disabled', proactiveEnabled: proactive.enabled });
  }, async handle(event) {
    if (stopped || event.callId !== config.callId || event.epoch !== host.epoch) return;
    if (event.type === 'audio') {
      if (typeof event.audio !== 'string' || event.audio.length > 12000) throw new Error('invalid_audio');
      const pcm = Buffer.from(event.audio, 'base64');
      if (!pcm.length || pcm.length % 2 || !session.pushAudio({ epoch: host.epoch, callId: config.callId, pcm16: pcm })) throw new Error('audio_input_backpressure');
    } else if (event.type === 'image') {
      if (typeof event.image !== 'string' || event.image.length > 260000) throw new Error('invalid_image');
      if (scenes.state?.mode === 'guide' && scenes.state.status === 'watching') {
        const key = scenes.state.id + ':' + scenes.state.revision;
        if (key !== motionScene) { motion.reset(); motionScene = key; }
        const transition = motion.sample(event.grid);
        if (transition === 'moving') latency.cameraChanged([...scenes.children].find(([, child]) => child.role === 'guide')?.[0]);
        if (transition || (scenes.state.moving && motion.last && !motion.moving)) scenes.guideMotion(transition === 'moving');
      }
      host.image = { image: event.image, width: event.width, height: event.height, at: Date.now() };
      const now = Date.now();
      if (typeof event.grid === 'string' && event.grid.length === 384) latestGrid = { grid: event.grid, at: now };
      trace?.('frame.harness', { frameId: event.frameId });
      memory?.feed(event.image, now, event.frameId);
      for (const [id, monitor] of stepMonitors) {
        if (scenes.current(id)) monitor.feedGrid(event.grid);
      }
      for (const [id, { monitor, opening }] of guideMonitors) {
        if (!scenes.current(id)) continue;
        if (opening?.sample(event.grid, now) && now >= burstUntil && !scenes.state.moving) startCaptureBurst(now);
        monitor.feedImage(event.image, now);
        if (now < burstUntil && scheduler.listTasks().some(t => t.taskId === id && t.status === 'running')) monitor.requestEvaluation();
      }
      if (now >= burstUntil || now - lastForegroundAt >= 1000) {
        lastForegroundAt = now;
        const accepted = session.pushImage({ epoch: host.epoch, callId: config.callId, source: 'camera', image: event.image });
        trace?.('frame.foreground_queue', { frameId: event.frameId, accepted });
        if (!accepted) throw new Error('image_input_backpressure');
      }
    } else if (event.type === 'camera_clear') {
      memory?.cameraCleared();
      burstUntil = 0; lastForegroundAt = -Infinity; host.send('capture_burst', { enabled: false });
      motion.reset(); scenes.guideMotion(true);
      latestGrid = speechReference = undefined;
      host.image = undefined;
      deliveryWindow.invalidateVisual();
      intents.cameraCleared();
      // Clear a foreground frame queued before its first audio, without
      // pretending that another real camera/display was selected.
      session.setVisualSettings({ epoch: host.epoch, callId: config.callId, visualInput: { ...visual, mode: 'on-demand' } });
      session.setVisualSettings({ epoch: host.epoch, callId: config.callId, visualInput: visual });
      scheduler?.resetVisualSource();
    } else if (event.type === 'input_mute') {
      host.inputMuted = event.muted === true;
      session.setInputMuted({ epoch: host.epoch, inputMuted: host.inputMuted });
    } else if (event.type.startsWith('playback_')) {
      if (host.receipt(event, session)) host.send('playback', {
        outputId: event.outputId, status: event.type, scope: 'browser_audio_context' });
    } else if (event.type === 'task_stop' && typeof event.taskId === 'string' && event.taskId.length <= 128) {
      if (scenes.current(event.taskId.replace(/^proactive:/, ''))) {
        scenes.control({ action: 'stop', revision: scenes.snapshot().revision }, { trustedUser: true }); return;
      }
      const result = await session.handleSubagentsRequest({ action: 'stop', taskId: event.taskId });
      host.send('control_result', { requestId: event.requestId, result });
    } else if (event.type === 'requirement_control') {
      try { host.send('control_result', { requestId: event.requestId, ...requirements.control(event, { trustedUser: true }) }); }
      catch { host.send('control_result', { requestId: event.requestId, ok: false, code: 'stale_requirement_control' }); }
    } else if (event.type === 'scene_control') {
      try {
        if (typeof event.id !== 'string') throw new Error('scene_id_required');
        if (event.action === 'target' && event.point && (!host.image || Date.now() - host.image.at > 1500)) throw new Error('camera_frame_unavailable');
        host.send('control_result', { requestId: event.requestId, ...scenes.control(event, { trustedUser: true }) });
      }
      catch { host.send('control_result', { requestId: event.requestId, ok: false, code: 'stale_scene_control' }); }
    } else if (event.type === 'task_repeat' && typeof event.taskId === 'string' &&
        event.taskId.length <= 128 && typeof event.repeat === 'boolean') {
      const task = scheduler?.listTasks().find(task => `proactive:${task.taskId}` === event.taskId);
      try {
        if (!task || scenes.current(task.taskId) || task.taskType !== 'perception_monitor' || task.monitorMode !== 'event' ||
            task.generation !== event.generation || task.status !== 'running') throw new Error('task_not_editable');
        scheduler.updateTask({ targetTitle: task.title, repeat: event.repeat });
        host.send('control_result', { requestId: event.requestId, ok: true });
      } catch {
        host.send('control_result', { requestId: event.requestId, ok: false, code: 'task_not_editable' });
      }
    }
  }, close };
}
