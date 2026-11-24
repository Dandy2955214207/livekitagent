"""Opt-in paid component probe: 7 visual clips, no physical devices or speech generation."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from livekit_vlm_agent.config import Settings

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--case', choices=['all', 'transition', 'guide'], default='all')
    args = parser.parse_args()
    if not args.live:
        print('Offline. --live: at most 7 text-only generations, 80 seconds; uses existing public/synthetic fixtures.')
        raise SystemExit(0)
    settings = Settings()
    if settings.qwen_missing():
        raise SystemExit('Missing selected provider configuration')
    config = {'provider': settings.model_provider, 'apiKey': settings.realtime_api_key(),
              'endpoint': settings.qwen_endpoint(), 'model': settings.qwen_model,
              'maxGenerations': 7, 'maxMonitorGenerations': 7, 'case': args.case}
    # Private stdin, not argv, shell history or a credential file.
    result = subprocess.run(['node', str(ROOT / 'harness/qwen-live/test/live-evidence-probe.mjs')],
        input=json.dumps(config), text=True, encoding='utf-8', cwd=ROOT, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    raise SystemExit(result.returncode)
