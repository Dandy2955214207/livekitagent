"""Local settings. Loading settings does not connect to any service."""

from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_public_url: str = ""
    mobile_probe_only: bool = True
    livekit_api_key: SecretStr = SecretStr("")
    livekit_api_secret: SecretStr = SecretStr("")
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1024, le=65535)
    web_origin: str = "http://localhost:5173"
    model_provider: Literal["aliyun", "yibuapi"] = "aliyun"
    yibuapi_api_key: SecretStr = SecretStr("")
    qwen_region: Literal["cn-beijing", "ap-southeast-1"] = "cn-beijing"
    qwen_workspace_id: str = ""
    qwen_api_key: SecretStr = SecretStr("")
    qwen_realtime_url: str = ""
    qwen_model: str = "qwen3.8-omni-flash-realtime"
    qwen_voice: str = "Tina"
    qwen_turn_detection: Literal["semantic_vad", "semantic_vad_ec"] = "semantic_vad_ec"
    harness_mode: Literal["legacy_auto", "controlled_manual", "direct_background", "qwen_live_official"] = "legacy_auto"
    qwen_live_proactive_enabled: bool = False
    qwen_live_memory_enabled: bool = True
    qwen_memory_vision_model: str = "qwen3.5-flash"
    qwen_memory_embedding_model: str = "text-embedding-v4"
    qwen_live_max_generations: int = Field(default=80, ge=1, le=80)
    qwen_live_max_monitor_generations: int = Field(default=60, ge=1, le=60)
    qwen_live_max_call_seconds: int = Field(default=600, ge=30, le=1800)
    background_observation_enabled: bool = False
    background_max_generations_per_call: int = Field(default=10, ge=1, le=20)
    harness_auto_input: bool = True
    harness_end_silence_seconds: float = Field(default=.75, ge=.4, le=2)
    audio_input_sample_rate: int = 16000
    audio_output_sample_rate: int = 24000
    audio_input_chunk_ms: int = Field(default=20, ge=10, le=100)
    video_fps: float = Field(default=1, gt=0, le=5)
    showcase_sample_fps: float = Field(default=3, ge=1, le=5)
    showcase_stable_seconds: float = Field(default=1.5, ge=0.5, le=5)
    showcase_early_observe_seconds: float = Field(default=.3, ge=.2, le=5)
    showcase_fast_path: bool = True
    showcase_pipeline: bool = True
    showcase_fast_dwell_seconds: float = Field(default=1.2, ge=.8, le=5)
    showcase_fast_match_threshold: float = Field(default=.12, ge=.03, le=.18)
    showcase_recheck_seconds: float = Field(default=12, ge=5, le=60)
    showcase_motion_threshold: float = Field(default=0.07, ge=0.01, le=0.5)
    video_max_width: int = Field(default=640, ge=32, le=1920)
    video_max_height: int = Field(default=480, ge=32, le=1080)
    video_max_frame_age_ms: int = Field(default=1500, ge=100, le=10000)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    record_raw_media: bool = False
    agent_name: str = "qwen-voice-agent"
    session_ttl_seconds: int = Field(default=1800, ge=60, le=7200)
    join_timeout_seconds: int = Field(default=45, ge=10, le=120)
    output_buffer_max_ms: int = Field(default=10000, ge=1000, le=30000)
    worker_port: int = 8081

    def qwen_endpoint(self) -> str:
        if self.model_provider == "yibuapi":
            return "wss://yibuapi.com/v1/realtime"
        workspace = self.qwen_workspace_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9-]+", workspace):
            raise ValueError("QWEN_WORKSPACE_ID is missing or malformed")
        expected = f"wss://{workspace}.{self.qwen_region}.maas.aliyuncs.com/api-ws/v1/realtime"
        endpoint = self.qwen_realtime_url.strip() or expected
        if urlsplit(endpoint) != urlsplit(expected):
            raise ValueError("QWEN_REALTIME_URL must match the configured workspace and region")
        return endpoint

    def realtime_api_key(self) -> str:
        key = self.yibuapi_api_key if self.model_provider == "yibuapi" else self.qwen_api_key
        return key.get_secret_value().strip()

    def realtime_headers(self) -> dict[str, str]:
        headers = {"Authorization": "Bearer " + self.realtime_api_key()}
        if self.model_provider == "yibuapi":
            headers["OpenAI-Beta"] = "realtime=v1"
        return headers

    def qwen_missing(self) -> list[str]:
        if self.model_provider == "yibuapi":
            return [] if self.realtime_api_key() else ["YIBUAPI_API_KEY"]
        missing = []
        if not self.qwen_api_key.get_secret_value().strip():
            missing.append("QWEN_API_KEY")
        if not self.qwen_workspace_id.strip():
            missing.append("QWEN_WORKSPACE_ID")
        return missing
