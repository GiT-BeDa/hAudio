"""Serialized PipeWire/PulseAudio graph management."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config
from .state import StateStore


LOG = logging.getLogger("haudio.audio")
LABEL_PATTERN = re.compile(r"application\.name=HAUDIO_([A-Z0-9_]+)")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def default_runner(args: list[str], env: dict[str, str], timeout: float) -> CommandResult:
    result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=timeout, check=False)
    return CommandResult(result.returncode, result.stdout, result.stderr)


class AudioController:
    def __init__(self, config: Config, store: StateStore, runner: Callable = default_runner):
        self.config = config
        self.store = store
        self.runner = runner
        self.runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        self.pulse_server = os.environ.get("PULSE_SERVER", f"unix:{self.runtime_dir}/pulse/native")
        self._lock = threading.RLock()
        self._cards_cache: list[dict[str, Any]] = []
        self._cards_cache_at = 0.0
        self.last_error = ""

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = self.runtime_dir
        env["PULSE_SERVER"] = self.pulse_server
        return env

    def run(self, *args: str, timeout: float = 5) -> CommandResult:
        try:
            return self.runner(list(args), self.environment(), timeout)
        except Exception as exc:
            self.last_error = str(exc)
            LOG.warning("Command failed: %s: %s", " ".join(args), exc)
            return CommandResult(1, "", str(exc))

    def pactl(self, *args: str) -> CommandResult:
        return self.run("/usr/bin/pactl", *args)

    def pactl_json(self, *args: str) -> list[dict[str, Any]]:
        result = self.pactl("-f", "json", *args)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "pactl failed")
        value = json.loads(result.stdout or "[]")
        return value if isinstance(value, list) else []

    def available(self) -> bool:
        result = self.pactl("info")
        return result.returncode == 0

    def cards(self, force: bool = False) -> list[dict[str, Any]]:
        """Resolve real source/sink nodes for each USB PipeWire card."""
        with self._lock:
            if not force and time.monotonic() - self._cards_cache_at < 0.8:
                return [dict(card) for card in self._cards_cache]
        try:
            cards = self.pactl_json("list", "cards")
            sources = self.pactl_json("list", "sources")
            sinks = self.pactl_json("list", "sinks")
        except Exception as exc:
            self.last_error = str(exc)
            LOG.warning("Unable to enumerate audio cards: %s", exc)
            return []

        result = []
        for card in cards:
            props = card.get("properties") or {}
            if props.get("device.bus") != "usb":
                continue
            card_name = card.get("name", "")
            bus_path = props.get("device.bus_path") or props.get("sysfs.path")
            if not card_name or not bus_path:
                continue
            card_sources = [
                item for item in sources
                if (item.get("properties") or {}).get("device.name") == card_name
                and not str(item.get("name", "")).endswith(".monitor")
            ]
            card_sinks = [
                item for item in sinks
                if (item.get("properties") or {}).get("device.name") == card_name
            ]
            source = next((item.get("name") for item in card_sources if item.get("name")), None)
            sink = next((item.get("name") for item in card_sinks if item.get("name")), None)
            product = props.get("device.product.name") or props.get("device.description") or card_name
            result.append({
                "id": bus_path,
                "card_name": card_name,
                "product": product,
                "description": props.get("device.description") or product,
                "source": source,
                "sink": sink,
                "has_input": bool(source),
                "has_output": bool(sink),
            })
        result = sorted(result, key=lambda item: (item["product"].lower(), item["id"]))
        with self._lock:
            self._cards_cache = [dict(card) for card in result]
            self._cards_cache_at = time.monotonic()
        return result

    def selected_card(self, role: str, cards: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        cards = cards if cards is not None else self.cards()
        assigned = self.store.get("assignments", {}).get(role)
        return next((card for card in cards if assigned in (card["id"], card["card_name"])), None)

    def nodes(self, cards: list[dict[str, Any]] | None = None) -> dict[str, str | None]:
        cards = cards if cards is not None else self.cards()
        pc1 = self.selected_card("pc1", cards) or {}
        pc2 = self.selected_card("pc2", cards) or {}
        headset = self.selected_card("headset", cards) or {}
        return {
            "pc1_in": pc1.get("source"),
            "pc1_out": pc1.get("sink"),
            "pc2_in": pc2.get("source"),
            "pc2_out": pc2.get("sink"),
            "mic_in": headset.get("source"),
            "headset": headset.get("sink"),
        }

    def desired_graph(self, nodes: dict[str, str | None] | None = None) -> dict[str, tuple[str, str]]:
        nodes = nodes or self.nodes()
        candidates = {
            "PC1_IN": (nodes["pc1_in"], nodes["headset"]),
            "PC2_IN": (nodes["pc2_in"], nodes["headset"]),
            "MIC_PC1": (nodes["mic_in"], nodes["pc1_out"]),
            "MIC_PC2": (nodes["mic_in"], nodes["pc2_out"]),
            "SOUNDBOARD_HEADSET": ("HAUDIO_SOUNDBOARD.monitor", nodes["headset"]),
            "SOUNDBOARD_PC1": ("HAUDIO_SOUNDBOARD.monitor", nodes["pc1_out"]),
            "SOUNDBOARD_PC2": ("HAUDIO_SOUNDBOARD.monitor", nodes["pc2_out"]),
        }
        return {label: (source, sink) for label, (source, sink) in candidates.items() if source and sink}

    def _loopbacks(self) -> dict[str, list[dict[str, str]]]:
        found: dict[str, list[dict[str, str]]] = {}
        output = self.pactl("list", "short", "modules").stdout
        for line in output.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3 or parts[1] != "module-loopback":
                continue
            match = LABEL_PATTERN.search(parts[2])
            if not match:
                continue
            found.setdefault(match.group(1), []).append({"id": parts[0], "arguments": parts[2]})
        return found

    @staticmethod
    def _matches(arguments: str, source: str, sink: str) -> bool:
        return f"source={source}" in arguments and f"sink={sink}" in arguments

    def _ensure_soundboard_sink(self) -> bool:
        output = self.pactl("list", "short", "sinks").stdout
        if any(line.split("\t")[1:2] == ["HAUDIO_SOUNDBOARD"] for line in output.splitlines()):
            return True
        result = self.pactl(
            "load-module", "module-null-sink", "sink_name=HAUDIO_SOUNDBOARD",
            "sink_properties=device.description=hAudio_Soundboard",
        )
        return result.returncode == 0

    def reconcile_graph(self) -> bool:
        """Change only stale hAudio loopbacks; healthy links remain uninterrupted."""
        with self._lock:
            if not self.available() or not self._ensure_soundboard_sink():
                self.last_error = "PipeWire/PulseAudio is not available"
                return False
            desired = self.desired_graph()
            existing = self._loopbacks()
            for label, entries in existing.items():
                expected = desired.get(label)
                keep = False
                for entry in entries:
                    valid = bool(expected and self._matches(entry["arguments"], *expected) and not keep)
                    if valid:
                        keep = True
                    else:
                        self.pactl("unload-module", entry["id"])
                        LOG.info("Removed stale audio route %s", label)
            existing = self._loopbacks()
            success = True
            for label, (source, sink) in desired.items():
                if any(self._matches(item["arguments"], source, sink) for item in existing.get(label, [])):
                    continue
                result = self.pactl(
                    "load-module", "module-loopback", f"source={source}", f"sink={sink}",
                    f"latency_msec={self.config.loopback_latency_ms}",
                    f"source_output_properties=application.name=HAUDIO_{label}",
                    f"sink_input_properties=application.name=HAUDIO_{label}",
                )
                if result.returncode != 0:
                    success = False
                    self.last_error = result.stderr.strip() or f"Unable to create {label}"
                    LOG.error("Unable to create audio route %s: %s", label, self.last_error)
                else:
                    LOG.info("Created audio route %s", label)
            self.apply_controls()
            ready = success and self.graph_ready()
            if ready:
                self.last_error = ""
            return ready

    def graph_ready(self) -> bool:
        desired = self.desired_graph()
        existing = self._loopbacks()
        return all(
            any(self._matches(item["arguments"], source, sink) for item in existing.get(label, []))
            for label, (source, sink) in desired.items()
        )

    def signature(self) -> tuple:
        cards = self.cards(force=True)
        nodes = self.nodes(cards)
        return tuple(sorted((card["id"], card.get("source"), card.get("sink")) for card in cards)) + tuple(nodes.items())

    def _stream_indexes(self, kind: str) -> dict[str, str]:
        try:
            streams = self.pactl_json("list", kind)
        except Exception:
            return {}
        indexes = {}
        for stream in streams:
            app_name = (stream.get("properties") or {}).get("application.name", "")
            if app_name.startswith("HAUDIO_"):
                indexes[app_name.removeprefix("HAUDIO_")] = str(stream.get("index"))
        return indexes

    def apply_controls(self) -> None:
        with self._lock:
            current = self.store.snapshot()
            sink_inputs = self._stream_indexes("sink-inputs")
            source_outputs = self._stream_indexes("source-outputs")
            for label, key in (("PC1_IN", "pc1"), ("PC2_IN", "pc2")):
                if index := sink_inputs.get(label):
                    self.pactl("set-sink-input-volume", index, f"{current[key + '_volume']}%")
                    self.pactl("set-sink-input-mute", index, "1" if current[key + "_mute"] else "0")
            nodes = self.nodes()
            if nodes["headset"]:
                self.pactl("set-sink-volume", nodes["headset"], f"{current['headset_volume']}%")
            if nodes["mic_in"]:
                self.pactl("set-source-volume", nodes["mic_in"], f"{current['mic_volume']}%")
            self.pactl("set-sink-volume", "HAUDIO_SOUNDBOARD", f"{current['soundboard_volume']}%")
            for label, route_key in (("MIC_PC1", "mic_pc1"), ("MIC_PC2", "mic_pc2")):
                if index := source_outputs.get(label):
                    muted = current["mic_mute"] or not current[route_key]
                    self.pactl("set-source-output-mute", index, "1" if muted else "0")
            # Soundboard follows microphone routing, not the PC playback mute controls.
            for label, route_key in (("SOUNDBOARD_PC1", "mic_pc1"), ("SOUNDBOARD_PC2", "mic_pc2")):
                if index := sink_inputs.get(label):
                    muted = current["mic_mute"] or not current[route_key]
                    self.pactl("set-sink-input-mute", index, "1" if muted else "0")

    def set_volume(self, target: str, value: int) -> None:
        key = "mic_volume" if target == "mic" else f"{target}_volume"
        self.store.update({key: value})
        self.apply_controls()
        LOG.info("%s volume changed to %s", target, value)

    def set_mute(self, target: str, value: bool) -> None:
        self.store.update({f"{target}_mute": value})
        self.apply_controls()
        LOG.info("%s mute changed to %s", target, value)

    def set_route(self, target: str, value: bool) -> None:
        self.store.update({f"mic_{target}": value})
        self.apply_controls()
        LOG.info("Microphone route to %s changed to %s", target, value)
