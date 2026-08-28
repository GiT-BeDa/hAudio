"""Thread-safe and atomic persistent state."""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

LOG = logging.getLogger("haudio.state")
STATE_VERSION = 1

PRESET_KEYS = (
    "pc1_volume", "pc2_volume", "headset_volume", "mic_volume", "soundboard_volume",
    "pc1_mute", "pc2_mute", "mic_mute", "mic_pc1", "mic_pc2",
)

DEFAULT_PRESETS: dict[str, dict[str, Any]] = {
    "normal": {
        "pc1_volume": 50, "pc2_volume": 50, "headset_volume": 100,
        "mic_volume": 100, "soundboard_volume": 100,
        "pc1_mute": False, "pc2_mute": False, "mic_mute": False,
        "mic_pc1": True, "mic_pc2": True,
    },
    "pc1-only": {
        "pc1_volume": 50, "pc2_volume": 50, "headset_volume": 100,
        "mic_volume": 100, "soundboard_volume": 100,
        "pc1_mute": False, "pc2_mute": True, "mic_mute": False,
        "mic_pc1": True, "mic_pc2": False,
    },
    "pc2-only": {
        "pc1_volume": 50, "pc2_volume": 50, "headset_volume": 100,
        "mic_volume": 100, "soundboard_volume": 100,
        "pc1_mute": True, "pc2_mute": False, "mic_mute": False,
        "mic_pc1": False, "mic_pc2": True,
    },
    "meeting": {
        "pc1_volume": 50, "pc2_volume": 50, "headset_volume": 100,
        "mic_volume": 100, "soundboard_volume": 100,
        "pc1_mute": False, "pc2_mute": False, "mic_mute": False,
        "mic_pc1": True, "mic_pc2": True,
    },
}

DEFAULT_STATE: dict[str, Any] = {
    "state_version": STATE_VERSION,
    "pc1_volume": 50,
    "pc2_volume": 50,
    "headset_volume": 100,
    "mic_volume": 100,
    "soundboard_volume": 100,
    "pc1_mute": False,
    "pc2_mute": False,
    "mic_mute": False,
    "mic_pc1": True,
    "mic_pc2": True,
    "recording": {"session": False},
    "assignments": {},
    "assignment_fingerprints": {},
    "soundboard_playing": "",
    "mute_all_active": False,
    "mute_all_restore": {},
    "presets": copy.deepcopy(DEFAULT_PRESETS),
}

VOLUME_KEYS = {"pc1_volume", "pc2_volume", "headset_volume", "mic_volume", "soundboard_volume"}
BOOLEAN_KEYS = {"pc1_mute", "pc2_mute", "mic_mute", "mic_pc1", "mic_pc2", "mute_all_active"}


def _valid_volume(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 100


def _validated_preset(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return copy.deepcopy(fallback)
    result = copy.deepcopy(fallback)
    for key in PRESET_KEYS:
        candidate = value.get(key)
        if key in VOLUME_KEYS and _valid_volume(candidate) or key in BOOLEAN_KEYS and type(candidate) is bool:
            result[key] = candidate
    return result


def validate_state(value: Any) -> dict[str, Any]:
    """Return a complete, typed state while preserving valid persisted choices."""
    result = copy.deepcopy(DEFAULT_STATE)
    if not isinstance(value, dict):
        return result
    for key in VOLUME_KEYS:
        if _valid_volume(value.get(key)):
            result[key] = value[key]
    for key in BOOLEAN_KEYS:
        if type(value.get(key)) is bool:
            result[key] = value[key]
    recording = value.get("recording")
    if isinstance(recording, dict) and type(recording.get("session")) is bool:
        result["recording"] = {"session": recording["session"]}
    assignments = value.get("assignments")
    if isinstance(assignments, dict):
        result["assignments"] = {
            role: card_id
            for role, card_id in assignments.items()
            if role in {"pc1", "pc2", "headset"} and isinstance(card_id, str)
        }
    fingerprints = value.get("assignment_fingerprints")
    if isinstance(fingerprints, dict):
        result["assignment_fingerprints"] = {
            role: fingerprint
            for role, fingerprint in fingerprints.items()
            if role in {"pc1", "pc2", "headset"} and isinstance(fingerprint, str)
        }
    restore = value.get("mute_all_restore")
    if isinstance(restore, dict):
        result["mute_all_restore"] = {
            key: candidate
            for key, candidate in restore.items()
            if key in BOOLEAN_KEYS and type(candidate) is bool
        }
    presets = value.get("presets")
    if isinstance(presets, dict):
        result["presets"] = {
            name: _validated_preset(presets.get(name), defaults)
            for name, defaults in DEFAULT_PRESETS.items()
        }
    result["state_version"] = STATE_VERSION
    result["soundboard_playing"] = ""
    return result


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._state = copy.deepcopy(DEFAULT_STATE)

    def load(self) -> None:
        with self._lock:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return
            except Exception:
                LOG.exception("Unable to load state from %s; defaults remain active", self.path)
                try:
                    backup = self.path.with_name(f"{self.path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
                    shutil.copy2(self.path, backup)
                    LOG.error("Backed up unreadable state to %s", backup)
                except OSError:
                    LOG.exception("Unable to back up unreadable state")
                return
            self._state = validate_state(loaded)
            if self._state != loaded:
                LOG.warning("Persisted state required validation or migration")
                self._save_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._state.get(key, default))

    def update(self, values: dict[str, Any], persist: bool = True) -> dict[str, Any]:
        with self._lock:
            self._state.update(copy.deepcopy(values))
            if persist:
                self._save_locked()
            return copy.deepcopy(self._state)

    def mutate(self, callback: Callable[[dict[str, Any]], None], persist: bool = True) -> dict[str, Any]:
        with self._lock:
            callback(self._state)
            if persist:
                self._save_locked()
            return copy.deepcopy(self._state)

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self._state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o640)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
