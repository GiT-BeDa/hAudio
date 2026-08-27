"""Thread-safe and atomic persistent state."""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable


LOG = logging.getLogger("haudio.state")

DEFAULT_STATE: dict[str, Any] = {
    "pc1_volume": 70,
    "pc2_volume": 70,
    "headset_volume": 65,
    "mic_volume": 50,
    "soundboard_volume": 100,
    "pc1_mute": False,
    "pc2_mute": False,
    "mic_mute": False,
    "mic_pc1": True,
    "mic_pc2": True,
    "recording": {"session": False},
    "assignments": {},
    "soundboard_playing": "",
}

PRESETS: dict[str, dict[str, Any]] = {
    "normal": {
        "pc1_volume": 70, "pc2_volume": 70, "pc1_mute": False, "pc2_mute": False,
        "mic_mute": False, "mic_pc1": True, "mic_pc2": True,
    },
    "pc1-only": {
        "pc1_volume": 70, "pc1_mute": False, "pc2_mute": True,
        "mic_mute": False, "mic_pc1": True, "mic_pc2": False,
    },
    "pc2-only": {
        "pc2_volume": 70, "pc1_mute": True, "pc2_mute": False,
        "mic_mute": False, "mic_pc1": False, "mic_pc2": True,
    },
    "meeting": {
        "pc1_volume": 60, "pc2_volume": 60, "pc1_mute": False, "pc2_mute": False,
        "mic_mute": False, "mic_pc1": True, "mic_pc2": True,
    },
    "mute-all": {
        "pc1_mute": True, "pc2_mute": True, "mic_mute": True,
        "mic_pc1": False, "mic_pc2": False,
    },
}


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._state = copy.deepcopy(DEFAULT_STATE)

    def load(self) -> None:
        with self._lock:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._state.update(loaded)
            except FileNotFoundError:
                return
            except Exception:
                LOG.exception("Unable to load state from %s; defaults remain active", self.path)
            self._state.setdefault("assignments", {})
            self._state.setdefault("recording", {"session": False})
            self._state["soundboard_playing"] = ""

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
