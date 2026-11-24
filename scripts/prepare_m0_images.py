"""Deterministic, non-personal visual protocol fixtures (not model outputs)."""

import hashlib
import json
from pathlib import Path
import wave

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / ".runtime/m0-fixtures"


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, color in [("red", (230, 20, 20)), ("blue", (20, 40, 230))]:
        image = Image.new("RGB", (640, 480), "white")
        ImageDraw.Draw(image).ellipse((150, 70, 490, 410), fill=color)
        image.save(FIXTURES / f"{name}.jpg", quality=85)
    photo = FIXTURES / "dog-original.jpg"
    if photo.exists():
        with Image.open(photo) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((640, 480))
            image.save(FIXTURES / "dog.jpg", quality=80)
    manifest = {}
    for path in sorted(FIXTURES.iterdir()):
        if path.suffix not in {".wav", ".jpg"}:
            continue
        content = path.read_bytes()
        item = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        if path.suffix == ".wav":
            with wave.open(str(path)) as wav:
                assert (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) == (16000, 2, 1)
                item["duration_s"] = round(wav.getnframes() / wav.getframerate(), 3)
        elif path.name != "dog-original.jpg":
            assert 4 * ((len(content) + 2) // 3) <= 256 * 1024
        manifest[path.name] = item
    (FIXTURES / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("OK fixed PCM/JPEG fixtures and SHA256 manifest prepared")


if __name__ == "__main__":
    main()
