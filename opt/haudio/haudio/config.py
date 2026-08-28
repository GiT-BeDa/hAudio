"""Configuration loading without import-time filesystem side effects."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Config:
    state_dir: Path = Path("/var/lib/haudio")
    recording_dir: Path = Path("/data/haudio/recordings")
    soundboard_dir: Path = Path("/data/haudio/soundboard")
    frontend_dir: Path = Path(__file__).resolve().parents[1] / "frontend"
    sample_rate: int = 48_000
    loopback_latency_ms: int = 10
    recording_bitrate: str = "128k"
    recording_segment_seconds: int = 3_600
    recording_max_age_days: int = 30
    recording_max_disk_usage_percent: float = 90.0
    recording_min_free_gb: float = 5.0
    soundboard_max_bytes: int = 200 * 1024 * 1024
    status_interval_seconds: float = 5.0
    websocket_interval_seconds: float = 0.25
    device_interval_seconds: float = 3.0
    device_health_interval_seconds: float = 30.0
    meter_enabled: bool = True
    meter_sample_rate: int = 8_000
    auth_username: str = ""
    auth_password: str = ""

    def __post_init__(self) -> None:
        if not 8_000 <= self.sample_rate <= 192_000:
            raise ValueError("sample_rate must be between 8000 and 192000")
        if not 1 <= self.loopback_latency_ms <= 500:
            raise ValueError("loopback_latency_ms must be between 1 and 500")
        if not re.fullmatch(r"[1-9][0-9]*[kKmM]?", self.recording_bitrate):
            raise ValueError("recording_bitrate must be an FFmpeg bitrate such as 128k")
        if self.recording_segment_seconds < 1:
            raise ValueError("recording_segment_seconds must be positive")
        if self.recording_max_age_days < 0 or self.recording_min_free_gb < 0:
            raise ValueError("recording retention values cannot be negative")
        if not 0 <= self.recording_max_disk_usage_percent <= 100:
            raise ValueError("recording_max_disk_usage_percent must be between 0 and 100")
        if self.soundboard_max_bytes < 1:
            raise ValueError("soundboard_max_bytes must be positive")
        if min(
            self.status_interval_seconds,
            self.websocket_interval_seconds,
            self.device_interval_seconds,
            self.device_health_interval_seconds,
        ) <= 0:
            raise ValueError("monitoring intervals must be positive")
        if not 1_000 <= self.meter_sample_rate <= 48_000:
            raise ValueError("meter_sample_rate must be between 1000 and 48000")
        if bool(self.auth_username) != bool(self.auth_password):
            raise ValueError("auth_username and auth_password must be configured together")

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    def ensure_directories(self) -> None:
        for path in (self.state_dir, self.recording_dir, self.soundboard_dir):
            path.mkdir(parents=True, exist_ok=True)


PATH_FIELDS = {"state_dir", "recording_dir", "soundboard_dir", "frontend_dir"}
ENV_FIELDS = {
    "HAUDIO_STATE_DIR": "state_dir",
    "HAUDIO_RECORDING_DIR": "recording_dir",
    "HAUDIO_SOUNDBOARD_DIR": "soundboard_dir",
    "HAUDIO_FRONTEND_DIR": "frontend_dir",
    "HAUDIO_SAMPLE_RATE": "sample_rate",
    "HAUDIO_LOOPBACK_LATENCY_MS": "loopback_latency_ms",
    "HAUDIO_RECORDING_BITRATE": "recording_bitrate",
    "HAUDIO_RECORDING_SEGMENT_SECONDS": "recording_segment_seconds",
    "HAUDIO_RECORDING_MAX_AGE_DAYS": "recording_max_age_days",
    "HAUDIO_RECORDING_MAX_DISK_USAGE_PERCENT": "recording_max_disk_usage_percent",
    "HAUDIO_RECORDING_MIN_FREE_GB": "recording_min_free_gb",
    "HAUDIO_SOUNDBOARD_MAX_BYTES": "soundboard_max_bytes",
    "HAUDIO_STATUS_INTERVAL_SECONDS": "status_interval_seconds",
    "HAUDIO_WEBSOCKET_INTERVAL_SECONDS": "websocket_interval_seconds",
    "HAUDIO_DEVICE_HEALTH_INTERVAL_SECONDS": "device_health_interval_seconds",
    "HAUDIO_METER_ENABLED": "meter_enabled",
    "HAUDIO_METER_SAMPLE_RATE": "meter_sample_rate",
    "HAUDIO_AUTH_USERNAME": "auth_username",
    "HAUDIO_AUTH_PASSWORD": "auth_password",
}
SENSITIVE_CONFIG_FIELDS = {"auth_username", "auth_password"}


def _coerce(name: str, value: Any, current: Any) -> Any:
    if name in PATH_FIELDS:
        return Path(value)
    if isinstance(current, bool):
        return str(value).lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return str(value)


def load_config(path: Path | None = None) -> Config:
    """Load optional JSON configuration, then apply HAUDIO_* overrides."""
    config = Config()
    config_path = path or Path(os.environ.get("HAUDIO_CONFIG", "/etc/haudio/haudio.json"))
    values: dict[str, Any] = {}
    if config_path.is_file():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        sensitive = set(raw) & SENSITIVE_CONFIG_FIELDS
        if sensitive:
            raise ValueError("authentication credentials must be supplied through environment variables")
        allowed = {item.name for item in fields(Config)}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
        values.update(raw)
    for env_name, field_name in ENV_FIELDS.items():
        if env_name in os.environ:
            values[field_name] = os.environ[env_name]
    for name, value in values.items():
        config = replace(config, **{name: _coerce(name, value, getattr(config, name))})
    return config
