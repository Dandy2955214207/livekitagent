import { readFile } from 'node:fs/promises';
import { sceneVoice } from './scene-voice.mjs';
import { waitsForPresentation } from './guide-opening.mjs';

export async function loadSceneSkills() {
  const files = ['guide', 'teach', 'requirements'];
  return (await Promise.all(files.map(name => readFile(new URL(`../skills/${name}.md`, import.meta.url), 'utf8')))).join('\n\n');
}
export const SCENE_TOOLS = [
  { type: 'function', continuesResponse: true, function: { name: 'start_scene_task',
    description: '开始持续主体讲解或完整分步教学。教学必须使用此工具，不能以单次提醒代替。保留一个主场景，可同时创建独立辅助提醒。普通追问不创建新任务。',
    parameters: { type: 'object', additionalProperties: false, required: ['mode', 'goal', 'depth'], properties: {
      mode: { type: 'string', enum: ['guide', 'teach'] }, goal: { type: 'string', description: '简短保留用户的教学目标或讲解主体范围；不要生成步骤。' },
      depth: { type: 'string', enum: ['brief', 'normal', 'deep'] },
    } } } },
  { type: 'function', continuesResponse: true, function: { name: 'control_scene_task',
    description: '操作已有主场景。id 和 revision 使用当前值；focus 修改讲解范围；target 根据用户纠正选择主体（如左边那株），保留原讲解需求。普通追问无需调用。',
    parameters: { type: 'object', additionalProperties: false, required: ['id', 'action', 'revision'], properties: {
      id: { type: 'string' }, action: { type: 'string', enum: ['pause', 'resume', 'repeat_step', 'confirm_step', 'stop', 'depth', 'focus', 'target'] },
      revision: { type: 'integer' }, depth: { type: 'string', enum: ['brief', 'normal', 'deep'] }, focus: { type: 'string' },
      targetHint: { type: 'string', description: '仅 target 时填写用户实际指定的方位/外观，不编造对象已被识别。' },
    } } } },
  { type: 'function', continuesResponse: true, function: { name: 'list_requirements',
    description: '查看主场景和辅助需求的编号、版本、进度与播放状态。目标不清楚时先查看；不要猜最近任务。',
    parameters: { type: 'object', additionalProperties: false, properties: {} } } },
  { type: 'function', continuesResponse: true, function: { name: 'control_requirement',
    description: '按编号和版本暂停/恢复/结束辅助需求，或 update 修改明确要求的字段。只操作这一项；不将停止说话理解成结束任务。',
    parameters: { type: 'object', additionalProperties: false, required: ['id', 'revision', 'action'], properties: {
      id: { type: 'string' }, revision: { type: 'integer' }, action: { type: 'string', enum: ['pause', 'resume', 'stop', 'update'] },
      condition: { type: 'string' }, triggerResponse: { type: 'string' }, repeat: { type: 'boolean' },
      durationSec: { type: 'number' }, reminderText: { type: 'string' },
    } } } },
];

// Extend the official realtime API at its supported injection seam. Retain its
// user-turn authority checks, function receipts and notification provenance.
export function sceneRealtime(openRealtime, scenes, skills, report = () => {}, planner, requirements, presentation) {
  return async (config, callbacks) => {
    let realtime;
    const tools = base => [...base.filter(t => !SCENE_TOOLS.some(s => s.function.name === t.function.name)), ...SCENE_TOOLS];
    const voice = sceneVoice(scenes, {
      ...callbacks,
      onSpeechStopped(event) {
        presentation?.ended(event.itemId);
        callbacks.onSpeechStopped?.(event);
      },
      onTransportRecovery(event) {
        callbacks.onTransportRecovery?.(event);
        if (event.phase === 'restoring' && scenes.snapshot().active) scenes.publish();
        if (event.phase === 'restoring') requirements?.publish(true);
      },
      onFunctionCall(event) {
        if (!SCENE_TOOLS.some(t => t.function.name === event.name)) return callbacks.onFunctionCall?.(event);
        report({ name: event.name, arguments: event.arguments });
        let result;
        try {
          const args = JSON.parse(event.arguments);
          if (event.name === 'list_requirements') result = requirements?.snapshot() ?? scenes.snapshot();
          else if (event.name === 'control_requirement') result = requirements.control(args, { transcript: event.inputTranscript ?? '' });
          else if (event.name === 'start_scene_task') {
            const input = { mode: args.mode, goal: args.goal, depth: args.depth, focus: args.goal, steps: [] };
            if (args.mode === 'teach') {
              result = scenes.beginTeaching(input, planner,
                JSON.stringify({ goal: args.goal, userRequest: (event.inputTranscript ?? '').slice(0, 2000) }));
            } else {
              const opening = args.mode === 'guide' && waitsForPresentation(event.inputTranscript) ?
                { reference: presentation?.reference(event.inputItemId) } : undefined;
              result = scenes.start(input, { opening });
            }
            voice.delegate(event, result);
          } else {
            if (typeof args.id !== 'string') throw new Error('scene_id_required');
            result = voice.confirmation(event, args) ?? scenes.control(args, { transcript: event.inputTranscript ?? '' });
            if (args.action === 'confirm_step') voice.delegate(event, result);
          }
        } catch (error) {
          const code = error instanceof SyntaxError ? 'invalid_scene_json' : /^[a-z_]+$/.test(error.message) ? error.message : 'scene_tool_failed';
          result = { ok: false, error: code }; report({ name: event.name, code });
        }
        realtime.submitFunctionOutput(event, JSON.stringify(result));
      },
    });
    realtime = await openRealtime({ ...config, instructions: `${config.instructions}\n\n${skills}`, tools: tools(config.tools) }, voice.callbacks);
    const configure = realtime.configure.bind(realtime);
    realtime.configure = update => configure({ ...update, tools: tools(update.tools) });
    if (!requirements) scenes.attachContext(text => realtime.sendBackendContext(text));
    requirements?.attachContext(text => realtime.sendBackendContext(text));
    return realtime;
  };
}
