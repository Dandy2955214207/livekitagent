"""Bounded real-provider target-selection probe: four synthetic two-frame clips.

No physical camera/microphone or speech. Illustrations test the protocol and
selection behavior, not museum/plant identification accuracy on photographs.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def fixtures():
    from PIL import Image, ImageDraw
    out = ROOT / '.runtime/guide-target-fixtures'
    out.mkdir(parents=True, exist_ok=True)

    def plant(draw, x, y, scale):
        def xy(points):
            return [(int(x + a * scale), int(y + b * scale)) for a, b in points]
        draw.polygon(xy([(-65, 90), (65, 90), (48, 180), (-48, 180)]), fill='#af623f', outline='#593e30')
        draw.line(xy([(0, 95), (0, -130)]), fill='#315f30', width=max(2, int(9 * scale)))
        for sx, sy in [(-45, -80), (45, -105), (-42, -20), (44, 10), (-35, 48)]:
            draw.line(xy([(0, sy + 35), (sx, sy)]), fill='#315f30', width=max(2, int(5 * scale)))
            draw.ellipse([*xy([(sx-35,sy-20)])[0], *xy([(sx+35,sy+20)])[0]], fill='#407c36', outline='#245229')

    im = Image.new('RGB', (640,480), '#e7e1d4'); d = ImageDraw.Draw(im)
    d.rectangle((0,390,640,480),fill='#bca992'); plant(d,310,225,1.05)
    d.ellipse((557,43,611,109), fill='#3674ad', outline='#1d3d70')
    d.rectangle((573,27,595,64), fill='#3674ad', outline='#1d3d70')
    d.ellipse((570,24,598,35), fill='#18395a')
    im.save(out / 'foreground.jpg', quality=85)
    im = Image.new('RGB',(640,480),'#e7e1d4'); d=ImageDraw.Draw(im)
    d.rectangle((0,390,640,480),fill='#bca992'); plant(d,170,230,.9); plant(d,470,230,.9)
    im.save(out / 'ambiguous.jpg',quality=85)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--fast-burst', action='store_true', help='Verify two frames 260 ms apart on the same observer connection')
    args = parser.parse_args()
    fixtures()
    if not args.live:
        print('Synthetic fixtures prepared. --live permits at most 4 text-only visual generations / 65 seconds.')
        raise SystemExit(0)
    from livekit_vlm_agent.config import Settings
    settings=Settings()
    if settings.qwen_missing(): raise SystemExit('Missing selected provider configuration')
    config={'provider':settings.model_provider,'apiKey':settings.realtime_api_key(),
            'endpoint':settings.qwen_endpoint(),'model':settings.qwen_model,'fastBurst':args.fast_burst}
    result=subprocess.run(['node',str(ROOT/'harness/qwen-live/test/live-guide-target-probe.mjs')],
        input=json.dumps(config),text=True,encoding='utf-8',cwd=ROOT,timeout=65,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    raise SystemExit(result.returncode)
