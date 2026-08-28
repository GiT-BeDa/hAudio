import asyncio
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).parents[1]
APP_ROOT = PROJECT_ROOT / "opt" / "haudio"
sys.path.insert(0, str(APP_ROOT))

from haudio.app import Runtime, create_app  # noqa: E402
from haudio.audio import AudioController, CommandResult  # noqa: E402
from haudio.config import Config  # noqa: E402
from haudio.media import MediaManager, valid_recording_filename, valid_sound_filename  # noqa: E402
from haudio.state import DEFAULT_PRESETS, StateStore, validate_state  # noqa: E402


def test_state_store_is_atomic_and_round_trips(tmp_path):
    path = tmp_path / "state" / "state.json"
    store = StateStore(path)
    store.update({"pc1_volume": 42})
    assert json.loads(path.read_text())["pc1_volume"] == 42
    assert not list(path.parent.glob(".state-*.json"))

    restored = StateStore(path)
    restored.load()
    assert restored.get("pc1_volume") == 42


def test_fresh_install_and_builtin_presets_use_safe_baseline_volumes(tmp_path):
    state = StateStore(tmp_path / "state.json").snapshot()
    expected = {
        "pc1_volume": 50,
        "pc2_volume": 50,
        "headset_volume": 100,
        "mic_volume": 100,
    }
    assert {key: state[key] for key in expected} == expected
    for preset in DEFAULT_PRESETS.values():
        assert {key: preset[key] for key in expected} == expected


def test_invalid_state_values_are_replaced_without_losing_valid_choices(tmp_path):
    validated = validate_state({
        "pc1_volume": 500,
        "pc2_volume": 37,
        "mic_mute": "yes",
        "pc1_mute": True,
        "assignments": {"pc1": "usb-port-1", "unknown": 123},
    })
    assert validated["pc1_volume"] == 50
    assert validated["pc2_volume"] == 37
    assert validated["mic_mute"] is False
    assert validated["pc1_mute"] is True
    assert validated["assignments"] == {"pc1": "usb-port-1"}

    state_file = tmp_path / "state.json"
    state_file.write_text("not json")
    StateStore(state_file).load()
    assert list(tmp_path.glob("state.json.corrupt-*"))


def test_configuration_rejects_values_that_could_create_busy_loops():
    with pytest.raises(ValueError, match="intervals"):
        Config(status_interval_seconds=0)
    with pytest.raises(ValueError, match="bitrate"):
        Config(recording_bitrate="not a bitrate")


def test_filename_validation_allows_common_characters_but_blocks_paths():
    assert valid_sound_filename("meeting's intro.mp3")
    assert not valid_sound_filename("../escape.mp3")
    assert valid_recording_filename("Peter's session 01.opus")
    assert not valid_recording_filename("../session.opus")


def test_recording_playback_targets_only_the_assigned_headset(tmp_path, monkeypatch):
    calls = []

    class RunningProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    class PlaybackAudio:
        def nodes(self):
            return {
                "pc1_in": "pc1-source", "pc1_out": "pc1-sink",
                "pc2_in": "pc2-source", "pc2_out": "pc2-sink",
                "mic_in": "headset-source", "headset": "headset-only-sink",
            }

        def environment(self):
            return {}

    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return RunningProcess()

    monkeypatch.setattr("haudio.media.subprocess.Popen", popen)
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds",
    )
    config.ensure_directories()
    recording = config.recording_dir / "2026-08-28" / "session.opus"
    recording.parent.mkdir()
    recording.write_bytes(b"OggS")
    media = MediaManager(config, StateStore(config.state_file), PlaybackAudio())

    media.play_recording("2026-08-28/session.opus")

    args = calls[0][0]
    assert args[args.index("-device") + 1] == "headset-only-sink"
    assert "HAUDIO_SOUNDBOARD" not in args
    assert "pc1-sink" not in args
    assert "pc2-sink" not in args
    assert media.recording_playback_status()["active"] is True
    media.stop_recording_playback()
    assert media.recording_playback_status()["active"] is False


def test_audio_cards_use_real_nodes_instead_of_constructed_suffixes(tmp_path):
    payloads = {
        "cards": [{
            "name": "alsa_card.usb-example",
            "properties": {
                "device.bus": "usb",
                "device.bus_path": "usb-port-1.2",
                "device.product.name": "Example Audio",
            },
        }],
        "sources": [{
            "name": "alsa_input.usb-example.custom-input",
            "properties": {"device.name": "alsa_card.usb-example"},
        }],
        "sinks": [{
            "name": "alsa_output.usb-example.custom-output",
            "properties": {"device.name": "alsa_card.usb-example"},
        }],
    }

    def runner(args, _env, _timeout):
        return CommandResult(0, json.dumps(payloads[args[-1]]), "")

    config = Config(state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings", soundboard_dir=tmp_path / "sounds")
    audio = AudioController(config, StateStore(config.state_file), runner)
    assert audio.cards()[0]["source"] == "alsa_input.usb-example.custom-input"
    assert audio.cards()[0]["sink"] == "alsa_output.usb-example.custom-output"


def test_audio_controls_are_targeted_and_failed_commands_are_reported(tmp_path):
    calls = []

    def runner(args, _env, _timeout):
        calls.append(args)
        if "set-sink-input-volume" in args:
            return CommandResult(1, "", "simulated PipeWire failure")
        return CommandResult(0, "", "")

    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds",
    )
    audio = AudioController(config, StateStore(config.state_file), runner)
    audio._stream_indexes = lambda kind, force=False: {"PC1_IN": "41"} if kind == "sink-inputs" else {}
    audio.nodes = lambda cards=None: {"headset": None, "mic_in": None}

    audio.set_volume("pc1", 43)

    controls = [args[1] for args in calls if len(args) > 1 and args[1].startswith("set-")]
    assert controls == ["set-sink-input-volume"]
    assert "simulated PipeWire failure" in audio.last_error
    assert audio.control_status()["pc1-volume"] is False


def test_enabling_a_route_after_global_mic_mute_restores_all_desired_routes(tmp_path):
    calls = []

    def runner(args, _env, _timeout):
        calls.append(args)
        return CommandResult(0, "", "")

    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds",
    )
    store = StateStore(config.state_file)
    store.update({"mic_mute": True, "mic_pc1": True, "mic_pc2": True})
    audio = AudioController(config, store, runner)
    audio._stream_indexes = lambda kind, force=False: (
        {"SOUNDBOARD_PC1": "11", "SOUNDBOARD_PC2": "12"}
        if kind == "sink-inputs"
        else {"MIC_PC1": "21", "MIC_PC2": "22"}
    )

    audio.set_route("pc1", True)

    assert store.get("mic_mute") is False
    assert ["/usr/bin/pactl", "set-source-output-mute", "21", "0"] in calls
    assert ["/usr/bin/pactl", "set-source-output-mute", "22", "0"] in calls
    assert ["/usr/bin/pactl", "set-sink-input-mute", "11", "0"] in calls
    assert ["/usr/bin/pactl", "set-sink-input-mute", "12", "0"] in calls


def test_graph_matching_includes_latency_and_assigned_device_health(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", loopback_latency_ms=10,
    )
    store = StateStore(config.state_file)
    audio = AudioController(config, store)
    arguments = (
        "source=source-a sink=sink-a latency_msec=10 "
        "source_output_properties=application.name=HAUDIO_PC1_IN "
        "sink_input_properties=application.name=HAUDIO_PC1_IN"
    )
    assert audio._matches(arguments, "PC1_IN", "source-a", "sink-a")
    assert not audio._matches(
        arguments.replace("latency_msec=10", "latency_msec=20"), "PC1_IN", "source-a", "sink-a"
    )

    store.update({"assignments": {"pc1": "missing-device"}})
    audio._loopbacks = lambda: {}
    assert audio.graph_ready([]) is False
    assert audio.device_errors([]) == ["PC1 AUDIO DEVICE LOST"]


def test_moved_device_is_rebound_only_when_hardware_fingerprint_is_unique(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds",
    )
    store = StateStore(config.state_file)
    store.update({
        "assignments": {"pc1": "old-port"},
        "assignment_fingerprints": {"pc1": "vendor|product|serial|name"},
    })
    audio = AudioController(config, store)
    moved = {
        "id": "new-port", "card_name": "card", "fingerprint": "vendor|product|serial|name",
        "has_input": True, "has_output": True, "source": "source", "sink": "sink",
    }

    assert audio.rebind_assignments([moved]) is True
    assert store.get("assignments")["pc1"] == "new-port"


def test_existing_port_assignment_is_seeded_with_a_hardware_fingerprint(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds",
    )
    store = StateStore(config.state_file)
    store.update({"assignments": {"headset": "current-port"}})
    audio = AudioController(config, store)
    card = {
        "id": "current-port", "card_name": "card", "fingerprint": "vendor|product|serial|name",
        "has_input": True, "has_output": True, "source": "source", "sink": "sink",
    }

    assert audio.capture_assignment_fingerprints([card]) is True
    assert store.get("assignment_fingerprints")["headset"] == "vendor|product|serial|name"
    assert audio.capture_assignment_fingerprints([card]) is False


def test_partial_graph_only_requires_routes_for_assigned_devices(tmp_path):
    config = Config(state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings", soundboard_dir=tmp_path / "sounds")
    store = StateStore(config.state_file)
    store.update({"assignments": {"headset": "headset", "pc1": "pc1"}})
    audio = AudioController(config, store)
    nodes = {"pc1_in": "pc1-source", "pc1_out": "pc1-sink", "pc2_in": None, "pc2_out": None,
             "mic_in": "mic-source", "headset": "headset-sink"}
    desired = audio.desired_graph(nodes)
    assert "PC1_IN" in desired
    assert "MIC_PC1" in desired
    assert "PC2_IN" not in desired
    assert "MIC_PC2" not in desired


def test_soundboard_output_follows_microphone_routes(tmp_path):
    calls = []

    def runner(args, _env, _timeout):
        calls.append(args)
        return CommandResult(0, "", "")

    config = Config(state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings", soundboard_dir=tmp_path / "sounds")
    store = StateStore(config.state_file)
    store.update({"mic_mute": False, "mic_pc1": False, "mic_pc2": True})
    audio = AudioController(config, store, runner)
    audio._stream_indexes = lambda kind, force=False: ({"SOUNDBOARD_PC1": "11", "SOUNDBOARD_PC2": "12"} if kind == "sink-inputs" else {})
    audio.nodes = lambda cards=None: {"headset": None, "mic_in": None}
    audio.apply_controls()
    mute_calls = [args for args in calls if "set-sink-input-mute" in args]
    assert ["/usr/bin/pactl", "set-sink-input-mute", "11", "1"] in mute_calls
    assert ["/usr/bin/pactl", "set-sink-input-mute", "12", "0"] in mute_calls


def test_retention_skips_in_use_file_and_continues_with_other_files(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", recording_max_age_days=1,
        recording_min_free_gb=0, recording_max_disk_usage_percent=0,
    )
    config.ensure_directories()
    in_use = config.recording_dir / "in-use.opus"
    removable = config.recording_dir / "removable.opus"
    in_use.write_bytes(b"one")
    removable.write_bytes(b"two")
    old = time.time() - 3 * 86400
    os.utime(in_use, (old, old))
    os.utime(removable, (old, old))
    media = MediaManager(config, StateStore(config.state_file), object())
    media.recording_in_use = lambda path: path == in_use

    assert media.cleanup_recordings() == 1
    assert in_use.exists()
    assert not removable.exists()


def test_muted_microphone_is_not_added_to_recording(tmp_path):
    captured = []

    class RecordingAudio:
        def nodes(self):
            return {"headset": "headset-sink", "mic_in": None}

        def environment(self):
            return {}

    class Process:
        def poll(self):
            return None

    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", sample_rate=44_100,
    )
    config.ensure_directories()
    store = StateStore(config.state_file)
    store.update({"mic_mute": True})
    media = MediaManager(config, store, RecordingAudio())
    media._spawn = lambda key, args: captured.append(args) or Process()

    media.start_recording()

    args = captured[0]
    assert args.count("-i") == 1
    assert "aresample=44100" in args[args.index("-filter_complex") + 1]


def test_soundboard_upload_does_not_silently_overwrite(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds",
    )
    config.ensure_directories()
    existing = config.soundboard_dir / "alert.mp3"
    existing.write_bytes(b"existing")
    media = MediaManager(config, StateStore(config.state_file), object())

    with pytest.raises(HTTPException) as error:
        media.store_upload("alert.mp3", io.BytesIO(b"replacement"))
    assert error.value.status_code == 409
    assert existing.read_bytes() == b"existing"


def test_media_process_error_includes_recent_ffmpeg_output(tmp_path, monkeypatch):
    class Audio:
        def environment(self):
            return {}

    class FailedProcess:
        def poll(self):
            return 1

    def popen(_args, **kwargs):
        kwargs["stderr"].write(b"Pulse sink unavailable\n")
        kwargs["stderr"].flush()
        return FailedProcess()

    monkeypatch.setattr("haudio.media.subprocess.Popen", popen)
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds",
    )
    config.ensure_directories()
    (config.soundboard_dir / "alert.mp3").write_bytes(b"test")
    media = MediaManager(config, StateStore(config.state_file), Audio())

    media.play("alert.mp3")
    media.poll()

    assert "Pulse sink unavailable" in media.last_error


class FakeAudio:
    def __init__(self, store):
        self.store = store
        self.cards_value = [{
            "id": "usb-1", "product": "USB Audio", "description": "USB Audio",
            "has_input": True, "has_output": True,
        }]

    def cards(self):
        return self.cards_value

    def set_volume(self, target, value):
        key = "mic_volume" if target == "mic" else f"{target}_volume"
        self.store.update({key: value})

    def set_mute(self, target, value):
        self.store.update({f"{target}_mute": value, "mute_all_active": False, "mute_all_restore": {}})

    def set_route(self, target, value):
        changes = {f"mic_{target}": value, "mute_all_active": False, "mute_all_restore": {}}
        if value:
            changes["mic_mute"] = False
        self.store.update(changes)

    def reconcile_graph(self):
        return True

    def apply_controls(self):
        return None

    def control_status(self):
        return {}

    def device_errors(self, cards=None):
        return []


class FakeMedia:
    def __init__(self):
        self.soundboard = {"playing": "", "active": False, "volume": 100}
        self.recording_playback = {"active": False, "path": "", "name": ""}
        self.recording_query = None

    def soundboard_status(self):
        return dict(self.soundboard)

    def recording_active(self):
        return False

    def recording_playback_status(self):
        return dict(self.recording_playback)

    def play_recording(self, relative):
        self.recording_playback = {
            "active": True, "path": relative, "name": Path(relative).name,
        }

    def stop_recording_playback(self):
        self.recording_playback = {"active": False, "path": "", "name": ""}

    def recording_files(self, limit=None, offset=0):
        self.recording_query = (limit, offset)
        return []

    def recording_count(self):
        return 0


class FakeRuntime:
    def __init__(self, config):
        self.config = config
        self.store = StateStore(config.state_file)
        self.audio = FakeAudio(self.store)
        self.media = FakeMedia()
        self.started = False

    async def start(self):
        self.config.ensure_directories()
        self.started = True

    async def stop(self):
        self.started = False

    def device_payload(self, cards=None):
        cards = cards or self.audio.cards()
        assignments = self.store.get("assignments", {})
        return {"cards": cards, "assignments": assignments, "selected": assignments}

    async def status(self):
        state = self.store.snapshot()
        return {
            "name": "hAudio", "version": "0.02", "online": True,
            "pc1": {"connected": True, "volume": state["pc1_volume"], "mute": state["pc1_mute"]},
            "pc2": {"connected": True, "volume": state["pc2_volume"], "mute": state["pc2_mute"]},
            "headset": {"connected": True, "volume": state["headset_volume"]},
            "microphone": {"connected": True, "volume": state["mic_volume"], "mute": state["mic_mute"],
                           "route_pc1": state["mic_pc1"], "route_pc2": state["mic_pc2"]},
            "recording": {
                "session": False,
                "playback": self.media.recording_playback_status(),
            },
            "soundboard": self.media.soundboard_status(),
            "levels": {}, "system": {"pipewire": True, "graph_ready": True},
            "devices": self.device_payload(),
            "presets": {"mute_all_active": state.get("mute_all_active", False)},
            "errors": [],
        }

    async def refresh_status(self):
        return await self.status()


def test_mic_mute_and_volume_api_are_reachable(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
    )
    runtime = FakeRuntime(config)
    app = create_app(config, runtime)
    with TestClient(app) as client:
        response = client.post("/api/mic/mute", json={"value": True})
        assert response.status_code == 200
        assert response.json()["microphone"]["mute"] is True
        response = client.post("/api/mic/volume", json={"value": 37})
        assert response.status_code == 200
        assert response.json()["microphone"]["volume"] == 37


def test_recording_listing_is_bounded_and_reports_total(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
    )
    runtime = FakeRuntime(config)
    app = create_app(config, runtime)
    with TestClient(app) as client:
        response = client.get("/api/recordings?limit=25&offset=10")
        assert response.status_code == 200
        assert response.json() == {"files": [], "total": 0, "limit": 25, "offset": 10}
        assert runtime.media.recording_query == (25, 10)
        assert client.get("/api/recordings?limit=501").status_code == 400


def test_optional_basic_auth_protects_controls_but_not_health_checks(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
        auth_username="operator", auth_password="secret",
    )
    app = create_app(config, FakeRuntime(config))
    with TestClient(app) as client:
        assert client.get("/").status_code == 401
        assert client.get("/", auth=("operator", "wrong")).status_code == 401
        assert client.get("/", auth=("operator", "secret")).status_code == 200
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200


def test_recording_playback_api_reports_live_state(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
    )
    runtime = FakeRuntime(config)
    app = create_app(config, runtime)
    with TestClient(app) as client:
        started = client.post("/api/recordings/2026-08-28/session.opus/play")
        assert started.status_code == 200
        assert started.json()["recording"]["playback"] == {
            "active": True,
            "path": "2026-08-28/session.opus",
            "name": "session.opus",
        }

        stopped = client.post("/api/recordings/playback/stop")
        assert stopped.status_code == 200
        assert stopped.json()["recording"]["playback"]["active"] is False


def test_duplicate_device_assignment_is_rejected(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
    )
    runtime = FakeRuntime(config)
    runtime.store.update({"assignments": {"pc1": "usb-1"}})
    app = create_app(config, runtime)
    with TestClient(app) as client:
        response = client.post("/api/devices/assign", json={"role": "pc2", "card_id": "usb-1"})
        assert response.status_code == 409


def test_preset_updates_routes_and_mutes(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
    )
    runtime = FakeRuntime(config)
    app = create_app(config, runtime)
    with TestClient(app) as client:
        response = client.post("/api/preset/pc1-only")
        assert response.status_code == 200
        payload = response.json()
        assert payload["pc1"]["mute"] is False
        assert payload["pc2"]["mute"] is True
        assert payload["microphone"]["route_pc1"] is True
        assert payload["microphone"]["route_pc2"] is False


def test_mute_all_restores_previous_state(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
    )
    runtime = FakeRuntime(config)
    runtime.store.update({
        "pc1_mute": False, "pc2_mute": True, "mic_mute": False,
        "mic_pc1": False, "mic_pc2": True,
    })
    app = create_app(config, runtime)
    with TestClient(app) as client:
        muted = client.post("/api/preset/mute-all").json()
        assert muted["pc1"]["mute"] is True
        assert muted["pc2"]["mute"] is True
        assert muted["microphone"]["mute"] is True
        assert muted["presets"]["mute_all_active"] is True

        restored = client.post("/api/preset/mute-all").json()
        assert restored["pc1"]["mute"] is False
        assert restored["pc2"]["mute"] is True
        assert restored["microphone"]["mute"] is False
        assert restored["microphone"]["route_pc1"] is False
        assert restored["microphone"]["route_pc2"] is True
        assert restored["presets"]["mute_all_active"] is False


def test_enabling_microphone_route_clears_global_mute(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
    )
    runtime = FakeRuntime(config)
    runtime.store.update({"mic_mute": True, "mic_pc1": False})
    app = create_app(config, runtime)
    with TestClient(app) as client:
        response = client.post("/api/mic/route/pc1", json={"value": True})
        assert response.status_code == 200
        assert response.json()["microphone"]["mute"] is False
        assert response.json()["microphone"]["route_pc1"] is True


def test_current_mix_can_be_saved_as_persistent_preset(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
    )
    runtime = FakeRuntime(config)
    runtime.store.update({"pc1_volume": 33, "mic_volume": 44, "mic_pc2": False})
    app = create_app(config, runtime)
    with TestClient(app) as client:
        saved = client.post("/api/presets/meeting/save")
        assert saved.status_code == 200
        assert saved.json()["values"]["pc1_volume"] == 33
        assert saved.json()["values"]["mic_volume"] == 44
        runtime.store.update({"pc1_volume": 99, "mic_volume": 99, "mic_pc2": True})
        applied = client.post("/api/preset/meeting").json()
        assert applied["pc1"]["volume"] == 33
        assert applied["microphone"]["volume"] == 44
        assert applied["microphone"]["route_pc2"] is False

    restored = StateStore(config.state_file)
    restored.load()
    assert restored.get("presets")["meeting"]["pc1_volume"] == 33


def test_frontend_is_complete_and_uses_stable_dom_updates(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
    )
    app = create_app(config, FakeRuntime(config))
    with TestClient(app) as client:
        index = client.get("/")
        javascript = client.get("/static/app.js")
        assert index.status_code == 200
        assert "SYSTEM STATUS" in index.text
        assert "SOUNDBOARD" in index.text
        assert javascript.status_code == 200
        assert "innerHTML" not in javascript.text
        assert "connectWebSocket" in javascript.text
        assert 'aria-pressed="false" disabled>■ STOP' in index.text
        assert '/static/app.js?v=0.02.5' in index.text
        assert '/static/style.css?v=0.02.5' in index.text
        assert "soundboardStop.classList.toggle('danger', soundboardActive)" in javascript.text
        assert "soundboardStop.disabled = !soundboardActive" in javascript.text
        assert "playRecording(file, playback)" in javascript.text


def test_live_status_uses_actual_soundboard_process_state(tmp_path):
    config = Config(
        state_dir=tmp_path / "state", recording_dir=tmp_path / "recordings",
        soundboard_dir=tmp_path / "sounds", frontend_dir=APP_ROOT / "frontend",
    )
    store = StateStore(config.state_file)
    media = FakeMedia()
    runtime = Runtime(config, store, FakeAudio(store), media)
    runtime._status = {"soundboard": {"playing": "stale.mp3", "active": True, "volume": 1}}
    media.soundboard = {"playing": "", "active": False, "volume": 75}

    status = asyncio.run(runtime.status())

    assert status["soundboard"] == {"playing": "", "active": False, "volume": 75}


def test_documentation_and_service_use_generic_user_runtime():
    install = (PROJECT_ROOT / "docs" / "INSTALL.md").read_text()
    service = (PROJECT_ROOT / "etc" / "systemd" / "user" / "haudio-control.service").read_text()
    api = (PROJECT_ROOT / "docs" / "API.md").read_text()
    assert "<raspberry-pi-address>" in install
    assert "User=" not in service
    assert "pipewire.service" in service
    assert "/api/mic/mute" in api


def test_manifest_only_lists_existing_distribution_files():
    manifest = (PROJECT_ROOT / "MANIFEST.txt").read_text().splitlines()
    included = manifest[manifest.index("Included:") + 1:manifest.index("Not included:")]
    paths = [line.strip() for line in included if line.strip()]

    assert "requirements-tested.txt" in paths
    assert "requirements-dev-tested.txt" in paths
    assert "tests/frontend.test.js" in paths
    assert not [path for path in paths if not (PROJECT_ROOT / path).exists()]
