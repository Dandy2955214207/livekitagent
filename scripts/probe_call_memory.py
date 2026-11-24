"""Fixed synthetic video and audio probe of within-call memory."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from livekit_vlm_agent.config import Settings

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        print('Use --live for at most 8 generations using local synthetic media, without physical devices.')
        raise SystemExit(0)
    settings = Settings()
    if settings.model_provider != 'aliyun' or settings.qwen_missing():
        raise SystemExit('This probe requires an Aliyun workspace and API key')
    with wave.open(str(ROOT / '.runtime/m0-fixtures/call-memory.wav')) as sound:
        if (sound.getframerate(), sound.getnchannels(), sound.getsampwidth()) != (16000, 1, 2):
            raise SystemExit('Expected mono PCM16 at 16000 Hz')
        (ROOT / '.runtime/m0-fixtures/call-memory.pcm').write_bytes(sound.readframes(sound.getnframes()))
    config = {'provider': settings.model_provider, 'apiKey': settings.realtime_api_key(),
              'endpoint': settings.qwen_endpoint(), 'model': settings.qwen_model, 'voice': settings.qwen_voice,
              'memoryVisionModel': settings.qwen_memory_vision_model,
              'memoryEmbeddingModel': settings.qwen_memory_embedding_model}
    result = subprocess.run(['node', 'harness/qwen-live/test/live-memory-probe.mjs'],
        input=json.dumps(config), text=True, encoding='utf-8', cwd=ROOT, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    raise SystemExit(result.returncode)
