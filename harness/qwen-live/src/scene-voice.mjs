import { isStepCompletionTranscript } from './scene-tasks.mjs';

const teachingRequest = /(?:指导|教|带)(?:一?下|着)?我|一步一步|一[步点]一[步点]|分步|步步|逐步|分步骤|teach me|guide me|step by step/iu;
const guideAction = /讲解|介绍|解说|分析|describe|explain|narrate/iu;
const continuousGuide = /接下来|持续|一路|依次|逐[一个]|每[次个件]|跟着|随着|边.{1,16}边|as i show|each (?:item|object)|keep (?:describing|explaining|narrating)/iu;
const stepConfirmation = /^(?:好了|完成了|做好了|画好了|画完了|我好了|(?:我|这一步|这步|当前步骤)(?:已经|刚刚)?(?:画好了|画完了|做完了|做好了|完成了|好了))(?:[。！!，, ]*(?:可以)?(?:继续|下一步))?[。！!\s]*$/u;

// The official session still owns playback, response completion and interruption.
// Hold a possible scene reply until the task takes ownership of the narration.
export function sceneVoice(scenes, downstream) {
  const inputs = new Map();
  const responses = new Map();
  function input(id) {
    if (!inputs.has(id)) {
      inputs.set(id, {});
      if (inputs.size > 32) inputs.delete(inputs.keys().next().value);
    }
    return inputs.get(id);
  }
  function delegated(turn) {
    return turn?.receipt && !turn.tools?.some(name => name !== turn.receiptTool);
  }
  function state(response) {
    const turn = inputs.get(response.inputItemId);
    if (delegated(turn)) return 'silent';
    if (!response.unbuffered && response.authority === 'direct' && (!turn?.hasTranscript || turn.hold)) return 'hold';
    return 'live';
  }
  function flush(response) {
    for (const [name, event] of response.pending) downstream[name]?.(event);
    response.pending.length = 0;
    response.bytes = 0;
  }
  function forward(name) {
    return event => {
      const response = responses.get(event.responseId);
      const policy = response ? state(response) : 'live';
      if (policy === 'silent') return;
      if (policy === 'hold') {
        response.bytes += event.audio?.byteLength ?? (event.text?.length ?? 0) * 2;
        if (response.bytes <= 960000 && response.pending.length < 2048) {
          response.pending.push([name, event]);
          return;
        }
        response.unbuffered = true;
      }
      if (response) flush(response);
      downstream[name]?.(event);
    };
  }
  return {
    delegate(event, receipt) {
      if (event.inputItemId) {
        const turn = input(event.inputItemId);
        turn.receipt = receipt;
        turn.receiptTool = event.name;
      }
    },
    confirmation(event, args) {
      if (args.action !== 'confirm_step') return;
      const turn = inputs.get(event.inputItemId);
      if (turn?.receipt && args.id === turn.receipt.id) return turn.receipt;
      if (turn?.step && (args.id !== turn.step.id || args.revision !== turn.step.revision)) {
        throw new Error('stale_scene_revision');
      }
    },
    callbacks: {
      ...downstream,
      onFunctionCall(event) {
        if (event.inputItemId) {
          const turn = input(event.inputItemId);
          turn.tools ??= [];
          if (!turn.tools.includes(event.name)) turn.tools.push(event.name);
        }
        downstream.onFunctionCall?.(event);
      },
      onSpeechStarted(event) {
        if (event.itemId) {
          const scene = scenes.snapshot();
          if (scene.mode === 'teach' && scene.status === 'waiting_step') {
            input(event.itemId).step ??= { id: scene.id, revision: scene.revision, index: scene.step };
          }
        }
        downstream.onSpeechStarted?.(event);
      },
      onInputTranscriptDone(event) {
        downstream.onInputTranscriptDone?.(event);
        if (!event.itemId) return;
        const turn = input(event.itemId);
        turn.hasTranscript = true;
        const step = turn.step;
        const scene = scenes.snapshot();
        const startingScene = teachingRequest.test(event.text) || (guideAction.test(event.text) && continuousGuide.test(event.text));
        turn.hold = (!scene.active && startingScene) || Boolean(step && isStepCompletionTranscript(event.text));
        if (step && scene.id === step.id && isStepCompletionTranscript(event.text) && stepConfirmation.test(event.text.trim())) {
          turn.receiptTool = 'control_scene_task';
          if (scene.completedSteps >= step.index) turn.receipt = { ok: true, ...scene };
          else if (scene.status === 'waiting_step' && scene.revision === step.revision) {
            turn.receipt = scenes.control({ id: step.id, revision: step.revision, action: 'confirm_step' }, { transcript: event.text });
          }
        }
        for (const response of responses.values()) {
          if (response.inputItemId === event.itemId && state(response) === 'live') flush(response);
        }
      },
      onResponseCreated(event) {
        if (event.inputItemId && ['direct', 'tool_continuation'].includes(event.authority)) {
          responses.set(event.responseId, { inputItemId: event.inputItemId, authority: event.authority, bytes: 0, pending: [] });
        }
        downstream.onResponseCreated?.(event);
      },
      onOutputAudioDelta: forward('onOutputAudioDelta'),
      onOutputAudioDone: forward('onOutputAudioDone'),
      onOutputTextDelta: forward('onOutputTextDelta'),
      onOutputTextDone: forward('onOutputTextDone'),
      onDirectTranscript(event) {
        if (delegated(inputs.get(event.inputItemId))) {
          downstream.onDirectTranscript?.({ ...event, entries: event.entries.filter(entry => entry.role === 'user') });
        } else downstream.onDirectTranscript?.(event);
      },
      onDialogue(event) {
        if (event.role === 'user' || !delegated(inputs.get(event.inputItemId))) downstream.onDialogue?.(event);
      },
      onResponseDone(event) {
        const response = responses.get(event.responseId);
        responses.delete(event.responseId);
        if (response && state(response) !== 'silent' && event.status === 'completed') flush(response);
        downstream.onResponseDone?.(event);
      },
      onTransportRecovery(event) {
        if (event.phase === 'started') responses.clear();
        downstream.onTransportRecovery?.(event);
      },
    },
  };
}
