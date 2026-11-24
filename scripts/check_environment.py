"""Offline dependency/config check. Never prints credentials or calls Qwen."""

import importlib
from importlib.metadata import version
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from livekit_vlm_agent.config import Settings


def main() -> int:
    checks = [
        ("livekit", "livekit.rtc"),
        ("livekit-api", "livekit.api"),
        ("livekit-agents", "livekit.agents"),
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("pydantic-settings", "pydantic_settings"),
        ("websockets", "websockets"),
        ("pillow", "PIL.Image"),
        ("numpy", "numpy"),
        ("pyyaml", "yaml"),
    ]
    print(f"Python {sys.version.split()[0]}")
    for distribution, module in checks:
        importlib.import_module(module)
        print(f"OK {distribution} {version(distribution)}")

    settings = Settings()
    import yaml

    config = yaml.safe_load((ROOT / "config/livekit.local.yaml").read_text(encoding="utf-8"))
    key = settings.livekit_api_key.get_secret_value()
    secret = settings.livekit_api_secret.get_secret_value()
    if len(secret) < 32 or config["keys"].get(key) != secret:
        raise ValueError("LiveKit credentials missing or inconsistent between .env and server YAML")
    print("OK local LiveKit credentials match; values hidden")
    if settings.model_provider == "yibuapi" or settings.qwen_workspace_id:
        settings.qwen_endpoint()
        print("OK realtime endpoint configuration (" + settings.model_provider + ")")
    missing = settings.qwen_missing()
    print("PENDING " + ", ".join(missing) if missing else "OK Qwen required settings present")
    print("Offline check complete; no model request sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
