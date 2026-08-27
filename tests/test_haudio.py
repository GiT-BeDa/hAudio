import asyncio
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).parents[1]
APP_ROOT = PROJECT_ROOT / "opt" / "haudio"
sys.path.insert(0, str(APP_ROOT))

from haudio.app import Runtime, create_app  # noqa: E402
from haudio.audio import AudioController, CommandResult  # noqa: E402
from haudio.config import Config  # noqa: E402
from haudio.media import valid_recording_filename, valid_sound_filename  # noqa: E402
from haudio.state import StateStore  # noqa: E402


def test_state_store_is_atomic_and_round_trips(tmp_path):
    path = tmp_path / "state" / "state.json"
    store = StateStore(path)
    store.update({"pc1_volume": 42})
    assert json.loads(path.read_text())["pc1_volume"] == 42
    assert not list(path.parent.glob(".state-*.json"))

    restored = StateStore(path)
    restored.load()
    assert restored.get("pc1_volume") == 42


def test_filename_validation_allows_common_characters_but_blocks_paths():
    assert valid_sound_filename("meeting's intro.mp3")
    assert not valid_sound_filename("../escape.mp3")
    assert valid_recording_filename("Peter's session 01.opus")
    assert not valid_recording_filename("../session.opus")


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
    audio._stream_indexes = lambda kind: ({"SOUNDBOARD_PC1": "11", "SOUNDBOARD_PC2": "12"} if kind == "sink-inputs" else {})
    audio.nodes = lambda cards=None: {"headset": None, "mic_in": None}
    audio.apply_controls()
    mute_calls = [args for args in calls if "set-sink-input-mute" in args]
    assert ["/usr/bin/pactl", "set-sink-input-mute", "11", "1"] in mute_calls
    assert ["/usr/bin/pactl", "set-sink-input-mute", "12", "0"] in mute_calls


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


class FakeMedia:
    def __init__(self):
        self.soundboard = {"playing": "", "active": False, "volume": 100}

    def soundboard_status(self):
        return dict(self.soundboard)

    def recording_active(self):
        return False


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
            "name": "hAudio", "version": "0.01", "online": True,
            "pc1": {"connected": True, "volume": state["pc1_volume"], "mute": state["pc1_mute"]},
            "pc2": {"connected": True, "volume": state["pc2_volume"], "mute": state["pc2_mute"]},
            "headset": {"connected": True, "volume": state["headset_volume"]},
            "microphone": {"connected": True, "volume": state["mic_volume"], "mute": state["mic_mute"],
                           "route_pc1": state["mic_pc1"], "route_pc2": state["mic_pc2"]},
            "recording": {"session": False}, "soundboard": self.media.soundboard_status(),
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
        assert '/static/app.js?v=0.01' in index.text
        assert '/static/style.css?v=0.01' in index.text
        assert "soundboardStop.classList.toggle('danger', soundboardActive)" in javascript.text
        assert "soundboardStop.disabled = !soundboardActive" in javascript.text


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
