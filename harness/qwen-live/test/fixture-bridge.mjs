import { createRuntime } from '../src/runtime.mjs';
import { serveStdio } from '../src/stdio.mjs';
import { fakeProvider } from './fake-provider.mjs';

serveStdio(async (config, emit) => {
  const provider = await fakeProvider();
  const runtime = await createRuntime(config, emit, { gateway: { upstreamUrl: provider.url } });
  const originalHandle = runtime.handle;
  // The bridge child receives fixture settings over stdio, not its parent environment.
  const harmony = config.voice === 'harmony-rtc-fixture';
  let turned = false;
  runtime.handle = async (event) => {
    const verifyHarmonyReceipt = harmony && event.type === 'playback_completed'
      && runtime.host.outputs.has(event.outputId);
    await originalHandle(event);
    if (verifyHarmonyReceipt && !runtime.host.outputs.has(event.outputId)) {
      runtime.host.send('notice', { text: 'fixture_playback_completed' });
    }
    if (event.type === 'input_mute' && runtime.session.active?.inputMuted === event.muted) {
      runtime.host.send('diagnostic', { code: event.muted ? 'fixture_input_muted' : 'fixture_input_unmuted' });
    }
    if (event.type === 'audio' && !turned) {
      if (harmony) {
        const pcm = Buffer.from(event.audio, 'base64');
        let hasTone = false;
        for (let offset = 0; offset + 1 < pcm.length; offset += 2) {
          if (Math.abs(pcm.readInt16LE(offset)) > 500) {
            hasTone = true;
            break;
          }
        }
        if (!hasTone) return;
        runtime.host.send('notice', { text: 'fixture_input_audio' });
      }
      turned = true;
      // Synthetic local protocol events only; production has no fixture switch.
      setTimeout(() => provider.turn(provider.peers[0], config.proactiveEnabled ? {
        text: '每次看到钥匙时提醒我', tool: { name: 'create_proactive_monitor', arguments: JSON.stringify({
          title: '钥匙观察', modalities: ['vision'], condition: '[state] 画面中出现钥匙', trigger_response: '记得带钥匙', repeat: true,
        }) },
      } : undefined), 100);
    }
  };
  const originalClose = runtime.close;
  runtime.close = async () => { await originalClose(); await provider.close(); };
  return runtime;
});
