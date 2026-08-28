"""Serialized PipeWire/PulseAudio graph management."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
        self._stream_cache: dict[str, dict[str, str]] = {}
        self._stream_cache_at: dict[str, float] = {}
        self._errors: dict[str, str] = {}
        self._control_confirmed: dict[str, bool] = {}

    @property
    def last_error(self) -> str:
        return " · ".join(dict.fromkeys(self._errors.values()))

    def _set_error(self, key: str, message: str) -> None:
        self._errors[key] = message

    def _clear_error(self, key: str) -> None:
        self._errors.pop(key, None)

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = self.runtime_dir
        env["PULSE_SERVER"] = self.pulse_server
        return env

    def run(self, *args: str, timeout: float = 5) -> CommandResult:
        try:
            return self.runner(list(args), self.environment(), timeout)
        except Exception as exc:
            self._set_error("command", str(exc))
            LOG.warning("Command failed: %s: %s", " ".join(args), exc)
            return CommandResult(1, "", str(exc))

    def pactl(self, *args: str) -> CommandResult:
        return self.run("/usr/bin/pactl", *args)

    def pactl_json(self, *args: str) -> list[dict[str, Any]]:
        result = self.pactl("-f", "json", *args)
        if result.returncode != 0:
            self._set_error("pipewire-query", result.stderr.strip() or "pactl failed")
            raise RuntimeError(result.stderr.strip() or "pactl failed")
        self._clear_error("pipewire-query")
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
            self._set_error("inventory", str(exc))
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
            fingerprint_parts = [
                props.get("device.vendor.id", ""),
                props.get("device.product.id", ""),
                props.get("device.serial", ""),
                str(product),
            ]
            result.append({
                "id": bus_path,
                "card_name": card_name,
                "product": product,
                "description": props.get("device.description") or product,
                "source": source,
                "sink": sink,
                "sources": [item.get("name") for item in card_sources if item.get("name")],
                "sinks": [item.get("name") for item in card_sinks if item.get("name")],
                "fingerprint": "|".join(str(item) for item in fingerprint_parts),
                "has_input": bool(source),
                "has_output": bool(sink),
            })
        result = sorted(result, key=lambda item: (item["product"].lower(), item["id"]))
        with self._lock:
            self._cards_cache = [dict(card) for card in result]
            self._cards_cache_at = time.monotonic()
            self._clear_error("inventory")
        return result

    def selected_card(self, role: str, cards: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        cards = cards if cards is not None else self.cards()
        assigned = self.store.get("assignments", {}).get(role)
        selected = next((card for card in cards if assigned in (card["id"], card["card_name"])), None)
        if selected:
            return selected
        fingerprint = self.store.get("assignment_fingerprints", {}).get(role)
        matches = [card for card in cards if fingerprint and card.get("fingerprint") == fingerprint]
        return matches[0] if len(matches) == 1 else None

    def rebind_assignments(self, cards: list[dict[str, Any]] | None = None) -> bool:
        """Move stale port assignments when a stored hardware fingerprint is unique."""
        cards = cards if cards is not None else self.cards(force=True)
        assignments = self.store.get("assignments", {})
        fingerprints = self.store.get("assignment_fingerprints", {})
        changed = False
        for role in ("pc1", "pc2", "headset"):
            assigned = assignments.get(role)
            if not assigned or any(assigned in (card["id"], card["card_name"]) for card in cards):
                continue
            fingerprint = fingerprints.get(role)
            matches = [card for card in cards if fingerprint and card.get("fingerprint") == fingerprint]
            if len(matches) == 1 and matches[0]["id"] not in assignments.values():
                assignments[role] = matches[0]["id"]
                changed = True
                LOG.info("Rebound %s to moved USB device at %s", role, matches[0]["id"])
        if changed:
            self.store.update({"assignments": assignments})
        return changed

    def capture_assignment_fingerprints(self, cards: list[dict[str, Any]] | None = None) -> bool:
        """Seed or refresh fingerprints for assignments that currently resolve by port."""
        cards = cards if cards is not None else self.cards()
        assignments = self.store.get("assignments", {})
        fingerprints = self.store.get("assignment_fingerprints", {})
        changed = False
        for role, assigned in assignments.items():
            card = next((item for item in cards if assigned in (item["id"], item["card_name"])), None)
            fingerprint = card.get("fingerprint", "") if card else ""
            if fingerprint and fingerprints.get(role) != fingerprint:
                fingerprints[role] = fingerprint
                changed = True
        if changed:
            self.store.update({"assignment_fingerprints": fingerprints})
        return changed

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
    def _module_arguments(arguments: str) -> dict[str, str]:
        parsed = {}
        try:
            tokens = shlex.split(arguments)
        except ValueError:
            tokens = arguments.split()
        for token in tokens:
            if "=" in token:
                key, value = token.split("=", 1)
                parsed[key] = value
        return parsed

    def _matches(self, arguments: str, label: str, source: str, sink: str) -> bool:
        parsed = self._module_arguments(arguments)
        application = f"application.name=HAUDIO_{label}"
        return (
            parsed.get("source") == source
            and parsed.get("sink") == sink
            and parsed.get("latency_msec") == str(self.config.loopback_latency_ms)
            and parsed.get("source_output_properties") == application
            and parsed.get("sink_input_properties") == application
        )

    def role_health(self, cards: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
        cards = cards if cards is not None else self.cards()
        assignments = self.store.get("assignments", {})
        health = {}
        for role in ("pc1", "pc2", "headset"):
            assigned = bool(assignments.get(role))
            card = self.selected_card(role, cards)
            connected = bool(card and card.get("has_input") and card.get("has_output"))
            health[role] = {
                "assigned": assigned,
                "connected": connected,
                "card": card,
                "reason": "" if not assigned or connected else "assigned bidirectional device is unavailable",
            }
        return health

    def device_errors(self, cards: list[dict[str, Any]] | None = None) -> list[str]:
        health = self.role_health(cards)
        labels = {
            "pc1": "PC1 AUDIO DEVICE LOST",
            "pc2": "PC2 AUDIO DEVICE LOST",
            "headset": "HEADSET OR MICROPHONE DISCONNECTED",
        }
        return [labels[role] for role, value in health.items() if value["assigned"] and not value["connected"]]

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
                self._set_error("graph", "PipeWire/PulseAudio is not available")
                return False
            cards = self.cards(force=True)
            self.capture_assignment_fingerprints(cards)
            self.rebind_assignments(cards)
            nodes = self.nodes(cards)
            desired = self.desired_graph(nodes)
            existing = self._loopbacks()
            for label, entries in existing.items():
                expected = desired.get(label)
                keep = False
                for entry in entries:
                    valid = bool(expected and self._matches(entry["arguments"], label, *expected) and not keep)
                    if valid:
                        keep = True
                    else:
                        self.pactl("unload-module", entry["id"])
                        LOG.info("Removed stale audio route %s", label)
            existing = self._loopbacks()
            success = True
            for label, (source, sink) in desired.items():
                if any(self._matches(item["arguments"], label, source, sink) for item in existing.get(label, [])):
                    continue
                result = self.pactl(
                    "load-module", "module-loopback", f"source={source}", f"sink={sink}",
                    f"latency_msec={self.config.loopback_latency_ms}",
                    f"source_output_properties=application.name=HAUDIO_{label}",
                    f"sink_input_properties=application.name=HAUDIO_{label}",
                )
                if result.returncode != 0:
                    success = False
                    message = result.stderr.strip() or f"Unable to create {label}"
                    self._set_error("graph", message)
                    LOG.error("Unable to create audio route %s: %s", label, message)
                else:
                    LOG.info("Created audio route %s", label)
            self._stream_cache.clear()
            self._stream_cache_at.clear()
            controls_ready = self.apply_controls()
            ready = success and controls_ready and self.graph_ready(cards)
            if ready:
                self._clear_error("graph")
            return ready

    def graph_ready(self, cards: list[dict[str, Any]] | None = None) -> bool:
        cards = cards if cards is not None else self.cards()
        if any(value["assigned"] and not value["connected"] for value in self.role_health(cards).values()):
            return False
        desired = self.desired_graph(self.nodes(cards))
        existing = self._loopbacks()
        return all(
            any(self._matches(item["arguments"], label, source, sink) for item in existing.get(label, []))
            for label, (source, sink) in desired.items()
        )

    def signature(self) -> tuple:
        cards = self.cards(force=True)
        nodes = self.nodes(cards)
        return tuple(sorted((card["id"], card.get("source"), card.get("sink")) for card in cards)) + tuple(nodes.items())

    def _stream_indexes(self, kind: str, force: bool = False) -> dict[str, str]:
        with self._lock:
            if not force and time.monotonic() - self._stream_cache_at.get(kind, 0.0) < 1.0:
                return dict(self._stream_cache.get(kind, {}))
        try:
            streams = self.pactl_json("list", kind)
        except Exception:
            return {}
        indexes = {}
        for stream in streams:
            app_name = (stream.get("properties") or {}).get("application.name", "")
            if app_name.startswith("HAUDIO_"):
                indexes[app_name.removeprefix("HAUDIO_")] = str(stream.get("index"))
        with self._lock:
            self._stream_cache[kind] = dict(indexes)
            self._stream_cache_at[kind] = time.monotonic()
        return indexes

    def _control_command(self, key: str, *args: str) -> bool:
        result = self.pactl(*args)
        if result.returncode == 0:
            self._control_confirmed[key] = True
            self._clear_error(f"control-{key}")
            return True
        message = result.stderr.strip() or f"Unable to apply {key}"
        self._control_confirmed[key] = False
        self._set_error(f"control-{key}", message)
        LOG.error("Unable to apply audio control %s: %s", key, message)
        return False

    def control_status(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._control_confirmed)

    def _apply_volume(
        self,
        target: str,
        current: dict[str, Any],
        nodes: dict[str, str | None],
        sink_inputs: dict[str, str],
    ) -> bool:
        if target in {"pc1", "pc2"}:
            label = target.upper() + "_IN"
            index = sink_inputs.get(label)
            if not index:
                self._control_confirmed[f"{target}-volume"] = False
                return True
            return self._control_command(
                f"{target}-volume", "set-sink-input-volume", index, f"{current[target + '_volume']}%"
            )
        if target == "headset":
            if not nodes["headset"]:
                self._control_confirmed["headset-volume"] = False
                return True
            return self._control_command(
                "headset-volume", "set-sink-volume", nodes["headset"], f"{current['headset_volume']}%"
            )
        if target == "mic":
            if not nodes["mic_in"]:
                self._control_confirmed["mic-volume"] = False
                return True
            return self._control_command(
                "mic-volume", "set-source-volume", nodes["mic_in"], f"{current['mic_volume']}%"
            )
        return self._control_command(
            "soundboard-volume", "set-sink-volume", "HAUDIO_SOUNDBOARD", f"{current['soundboard_volume']}%"
        )

    def _apply_pc_mute(self, target: str, current: dict[str, Any], sink_inputs: dict[str, str]) -> bool:
        index = sink_inputs.get(target.upper() + "_IN")
        if not index:
            self._control_confirmed[f"{target}-mute"] = False
            return True
        return self._control_command(
            f"{target}-mute", "set-sink-input-mute", index, "1" if current[target + "_mute"] else "0"
        )

    def _apply_mic_route(
        self,
        target: str,
        current: dict[str, Any],
        sink_inputs: dict[str, str],
        source_outputs: dict[str, str],
    ) -> bool:
        muted = current["mic_mute"] or not current[f"mic_{target}"]
        success = True
        source_index = source_outputs.get(f"MIC_{target.upper()}")
        sink_index = sink_inputs.get(f"SOUNDBOARD_{target.upper()}")
        if source_index:
            success &= self._control_command(
                f"mic-{target}", "set-source-output-mute", source_index, "1" if muted else "0"
            )
        else:
            self._control_confirmed[f"mic-{target}"] = False
        if sink_index:
            success &= self._control_command(
                f"soundboard-{target}", "set-sink-input-mute", sink_index, "1" if muted else "0"
            )
        else:
            self._control_confirmed[f"soundboard-{target}"] = False
        return success

    def apply_controls(self) -> bool:
        with self._lock:
            current = self.store.snapshot()
            sink_inputs = self._stream_indexes("sink-inputs", force=True)
            source_outputs = self._stream_indexes("source-outputs", force=True)
            nodes = self.nodes()
            results = [self._apply_volume(target, current, nodes, sink_inputs) for target in (
                "pc1", "pc2", "headset", "mic", "soundboard"
            )]
            results.extend(self._apply_pc_mute(target, current, sink_inputs) for target in ("pc1", "pc2"))
            results.extend(
                self._apply_mic_route(target, current, sink_inputs, source_outputs) for target in ("pc1", "pc2")
            )
            return all(results)

    def set_volume(self, target: str, value: int) -> None:
        with self._lock:
            key = "mic_volume" if target == "mic" else f"{target}_volume"
            self.store.update({key: value})
            current = self.store.snapshot()
            sink_inputs = self._stream_indexes("sink-inputs") if target in {"pc1", "pc2"} else {}
            self._apply_volume(target, current, self.nodes(), sink_inputs)
        LOG.info("%s volume changed to %s", target, value)

    def set_mute(self, target: str, value: bool) -> None:
        with self._lock:
            self.store.update({f"{target}_mute": value, "mute_all_active": False, "mute_all_restore": {}})
            current = self.store.snapshot()
            sink_inputs = self._stream_indexes("sink-inputs")
            if target in {"pc1", "pc2"}:
                self._apply_pc_mute(target, current, sink_inputs)
            else:
                source_outputs = self._stream_indexes("source-outputs")
                for computer in ("pc1", "pc2"):
                    self._apply_mic_route(computer, current, sink_inputs, source_outputs)
        LOG.info("%s mute changed to %s", target, value)

    def set_route(self, target: str, value: bool) -> None:
        with self._lock:
            microphone_was_muted = bool(self.store.get("mic_mute"))
            changes = {f"mic_{target}": value, "mute_all_active": False, "mute_all_restore": {}}
            if value:
                changes["mic_mute"] = False
            self.store.update(changes)
            current = self.store.snapshot()
            sink_inputs = self._stream_indexes("sink-inputs")
            source_outputs = self._stream_indexes("source-outputs")
            affected = ("pc1", "pc2") if value and microphone_was_muted else (target,)
            for computer in affected:
                self._apply_mic_route(computer, current, sink_inputs, source_outputs)
        LOG.info("Microphone route to %s changed to %s", target, value)
