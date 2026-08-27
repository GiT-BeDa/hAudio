"""FastAPI application and non-blocking runtime orchestration."""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import os
import re
import shutil
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .audio import AudioController
from .config import Config, load_config
from .media import MediaManager, valid_recording_filename, valid_sound_filename
from .state import DEFAULT_PRESETS, PRESET_KEYS, StateStore


logging.basicConfig(
    level=os.environ.get("HAUDIO_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOG = logging.getLogger("haudio")


class VolumeRequest(BaseModel):
    value: int


class BooleanRequest(BaseModel):
    value: bool


class RenameRequest(BaseModel):
    name: str


class AssignmentRequest(BaseModel):
    role: str
    card_id: str


class MeterManager:
    def __init__(self, audio: AudioController):
        self.audio = audio
        self.levels = {"pc1": -60.0, "pc2": -60.0, "microphone": -60.0, "headset": -60.0}
        self.tasks: dict[str, asyncio.Task] = {}
        self.current: dict[str, str | None] = {}

    async def meter(self, key: str, source: str) -> None:
        while True:
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    "/usr/bin/pw-cat", "--record", "--target", source, "--rate", "8000",
                    "--channels", "1", "--format", "s16", "--latency", "100ms", "-",
                    env=self.audio.environment(), stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                while True:
                    # Half-second blocks avoid busy Python loops while retaining
                    # a responsive 2 Hz diagnostic meter.
                    data = await process.stdout.readexactly(8000)
                    samples = memoryview(data).cast("h")
                    step = max(1, len(samples) // 256)
                    sampled = samples[::step]
                    rms = math.sqrt(sum(value * value for value in sampled) / len(sampled)) / 32768.0
                    decibels = 20 * math.log10(max(rms, 1e-5))
                    self.levels[key] = round(max(-60.0, min(0.0, decibels)), 1)
            except asyncio.CancelledError:
                raise
            except asyncio.IncompleteReadError:
                pass
            except Exception:
                LOG.exception("Level meter failed for %s", key)
            finally:
                if process and process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 2)
                    except (asyncio.TimeoutError, ProcessLookupError):
                        if process.returncode is None:
                            process.kill()
                self.levels[key] = -60.0
            await asyncio.sleep(1)

    async def monitor(self) -> None:
        while True:
            try:
                cards = await asyncio.to_thread(self.audio.cards)
                nodes = self.audio.nodes(cards)
                wanted = {
                    "pc1": nodes["pc1_in"],
                    "pc2": nodes["pc2_in"],
                    "microphone": nodes["mic_in"],
                    "headset": f"{nodes['headset']}.monitor" if nodes["headset"] else None,
                }
                for key, source in wanted.items():
                    if self.current.get(key) == source:
                        continue
                    old = self.tasks.pop(key, None)
                    if old:
                        old.cancel()
                    self.levels[key] = -60.0
                    if source:
                        self.tasks[key] = asyncio.create_task(self.meter(key, source), name=f"meter-{key}")
                    self.current[key] = source
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Unable to update level meters")
            await asyncio.sleep(3)

    async def stop(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()


class Runtime:
    def __init__(self, config: Config, store: StateStore, audio: AudioController, media: MediaManager):
        self.config = config
        self.store = store
        self.audio = audio
        self.media = media
        self.meters = MeterManager(audio)
        self._status: dict = {}
        self._status_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._last_signature: tuple | None = None

    def device_payload(self, cards: list[dict] | None = None) -> dict:
        cards = cards if cards is not None else self.audio.cards()
        assignments = self.store.get("assignments", {})
        selected = {
            role: (self.audio.selected_card(role, cards) or {}).get("id", "")
            for role in ("pc1", "pc2", "headset")
        }
        return {
            "cards": [
                {
                    "id": card["id"], "product": card["product"],
                    "description": card["description"], "bus_path": card["id"],
                    "has_input": card["has_input"], "has_output": card["has_output"],
                    "roles": [role for role, value in assignments.items() if value == card["id"]],
                }
                for card in cards
            ],
            "assignments": assignments,
            "selected": selected,
        }

    @staticmethod
    def _system_metrics(recording_dir: Path) -> dict:
        result = {
            "disk_free_gb": None, "cpu_load": None, "ram_used_percent": None,
            "temperature_c": None, "uptime_seconds": None,
            "network_interface": "", "connection_type": "", "primary_ip": "",
            "wlan_connected": False, "wlan_primary": False,
            "wlan_signal_dbm": None, "ip_addresses": [],
        }
        try:
            result["disk_free_gb"] = round(shutil.disk_usage(recording_dir).free / 1e9, 1)
        except OSError:
            pass
        try:
            result["cpu_load"] = round(os.getloadavg()[0], 2)
            result["uptime_seconds"] = round(float(Path("/proc/uptime").read_text().split()[0]))
        except (OSError, ValueError):
            pass
        try:
            memory = Path("/proc/meminfo").read_text()
            total_match = re.search(r"MemTotal:\s+(\d+)", memory)
            available_match = re.search(r"MemAvailable:\s+(\d+)", memory)
            if total_match and available_match:
                total = int(total_match.group(1))
                available = int(available_match.group(1))
                result["ram_used_percent"] = round((1 - available / total) * 100, 1)
        except (OSError, ValueError):
            pass
        try:
            result["temperature_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
        except (OSError, ValueError):
            pass
        try:
            routes = []
            for line in Path("/proc/net/route").read_text().splitlines()[1:]:
                fields = line.split()
                if len(fields) >= 8 and fields[1] == "00000000":
                    routes.append((int(fields[6]), fields[0]))
            if routes:
                result["network_interface"] = min(routes)[1]
                wireless_path = Path("/sys/class/net") / result["network_interface"] / "wireless"
                result["connection_type"] = "Wi-Fi" if wireless_path.exists() else "LAN"
        except (OSError, ValueError):
            pass
        try:
            wireless = Path("/proc/net/wireless").read_text().splitlines()[2:]
            for line in wireless:
                fields = line.split()
                interface = fields[0].rstrip(":")
                operstate = (Path("/sys/class/net") / interface / "operstate").read_text().strip()
                carrier_path = Path("/sys/class/net") / interface / "carrier"
                carrier = carrier_path.read_text().strip() if carrier_path.exists() else "1"
                if operstate == "up" and carrier == "1":
                    result["wlan_connected"] = True
                    result["wlan_primary"] = interface == result["network_interface"]
                    result["wlan_signal_dbm"] = float(fields[3].rstrip("."))
                    break
        except (OSError, IndexError, ValueError):
            pass
        try:
            result["ip_addresses"] = sorted({item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
            route_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                route_socket.connect(("192.0.2.1", 9))
                result["primary_ip"] = route_socket.getsockname()[0]
                result["ip_addresses"].append(result["primary_ip"])
                result["ip_addresses"] = sorted(set(result["ip_addresses"]))
            finally:
                route_socket.close()
        except OSError:
            pass
        return result

    def build_status(self) -> dict:
        cards = self.audio.cards()
        nodes = self.audio.nodes(cards)
        current = self.store.snapshot()
        system = self._system_metrics(self.config.recording_dir)
        pipewire = self.audio.available()
        graph_ready = pipewire and self.audio.graph_ready()
        system.update({"pipewire": pipewire, "graph_ready": graph_ready})
        errors = []
        if not pipewire:
            errors.append("PIPEWIRE ERROR")
        if self.audio.last_error:
            errors.append(self.audio.last_error)
        if self.media.last_error:
            errors.append(self.media.last_error)
        if system.get("disk_free_gb") is not None and system["disk_free_gb"] < self.config.recording_min_free_gb:
            errors.append("DISK SPACE LOW")
        return {
            "name": "hAudio", "version": __version__, "online": True,
            "pc1": {"connected": bool(nodes["pc1_in"]), "volume": current["pc1_volume"], "mute": current["pc1_mute"]},
            "pc2": {"connected": bool(nodes["pc2_in"]), "volume": current["pc2_volume"], "mute": current["pc2_mute"]},
            "headset": {"connected": bool(nodes["headset"]), "volume": current["headset_volume"]},
            "microphone": {
                "connected": bool(nodes["mic_in"]), "volume": current["mic_volume"],
                "mute": current["mic_mute"], "route_pc1": current["mic_pc1"],
                "route_pc2": current["mic_pc2"],
            },
            "recording": {
                "session": self.media.recording_active(),
                "playback": self.media.recording_playback_status(),
            },
            "soundboard": self.media.soundboard_status(),
            "levels": copy.deepcopy(self.meters.levels),
            "system": system,
            "devices": self.device_payload(cards),
            "presets": {"mute_all_active": bool(current.get("mute_all_active"))},
            "errors": list(dict.fromkeys(errors)),
        }

    async def refresh_status(self) -> dict:
        value = await asyncio.to_thread(self.build_status)
        async with self._status_lock:
            self._status = value
        return copy.deepcopy(value)

    async def status(self) -> dict:
        async with self._status_lock:
            cached = copy.deepcopy(self._status)
        if not cached:
            cached = await self.refresh_status()
        cached["levels"] = copy.deepcopy(self.meters.levels)
        # Process state changes faster than the comparatively expensive system
        # status refresh. Keep playback controls synchronized with the actual
        # FFmpeg process on every lightweight WebSocket update.
        cached["soundboard"] = self.media.soundboard_status()
        cached["recording"] = {
            "session": self.media.recording_active(),
            "playback": self.media.recording_playback_status(),
        }
        return cached

    async def device_monitor(self) -> None:
        health_counter = 0
        while True:
            try:
                signature = await asyncio.to_thread(self.audio.signature)
                health_counter += 1
                needs_health_check = health_counter >= 4
                if signature != self._last_signature or needs_health_check:
                    healthy = await asyncio.to_thread(self.audio.graph_ready) if signature == self._last_signature else False
                    if not healthy:
                        await asyncio.to_thread(self.audio.reconcile_graph)
                    self._last_signature = signature
                    health_counter = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Device monitor failed")
            await asyncio.sleep(self.config.device_interval_seconds)

    async def status_monitor(self) -> None:
        while True:
            try:
                await self.refresh_status()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Status refresh failed")
            await asyncio.sleep(self.config.status_interval_seconds)

    async def media_monitor(self) -> None:
        retention_at = 0.0
        while True:
            try:
                await asyncio.to_thread(self.media.poll)
                if time.monotonic() - retention_at > 3600:
                    await asyncio.to_thread(self.media.cleanup_recordings)
                    retention_at = time.monotonic()
            except asyncio.CancelledError:
                raise
            except HTTPException:
                # Retention skips an active recording and retries later.
                retention_at = time.monotonic()
            except Exception:
                LOG.exception("Media monitor failed")
            await asyncio.sleep(1)

    async def start(self) -> None:
        self.config.ensure_directories()
        self.store.load()
        await asyncio.to_thread(self.audio.reconcile_graph)
        await self.refresh_status()
        self._tasks = [
            asyncio.create_task(self.device_monitor(), name="device-monitor"),
            asyncio.create_task(self.status_monitor(), name="status-monitor"),
            asyncio.create_task(self.media_monitor(), name="media-monitor"),
            asyncio.create_task(self.meters.monitor(), name="level-monitor"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.meters.stop()
        await asyncio.to_thread(self.media.stop_all)


def create_app(config: Config | None = None, runtime: Runtime | None = None) -> FastAPI:
    config = config or load_config()
    if runtime is None:
        store = StateStore(config.state_file)
        audio = AudioController(config, store)
        media = MediaManager(config, store, audio)
        runtime = Runtime(config, store, audio, media)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="hAudio", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    app.mount("/static", StaticFiles(directory=config.frontend_dir), name="static")

    async def fresh_status() -> dict:
        return await runtime.refresh_status()

    @app.get("/api/status")
    async def status():
        return await runtime.status()

    @app.get("/api/devices")
    async def devices():
        cards = await asyncio.to_thread(runtime.audio.cards)
        return runtime.device_payload(cards)

    @app.post("/api/devices/assign")
    async def assign_device(request: AssignmentRequest):
        if request.role not in {"pc1", "pc2", "headset"}:
            raise HTTPException(400, "invalid role")
        cards = await asyncio.to_thread(runtime.audio.cards)
        if request.card_id and not any(card["id"] == request.card_id for card in cards):
            raise HTTPException(404, "audio card not found")
        assignments = runtime.store.get("assignments", {})
        duplicate_role = next(
            (role for role, card_id in assignments.items() if role != request.role and card_id == request.card_id and card_id),
            None,
        )
        if duplicate_role:
            raise HTTPException(409, f"audio card is already assigned to {duplicate_role}")
        await asyncio.to_thread(runtime.media.stop_recording_playback)
        assignments[request.role] = request.card_id
        runtime.store.update({"assignments": assignments})
        await asyncio.to_thread(runtime.audio.reconcile_graph)
        if runtime.store.get("recording", {}).get("session"):
            await asyncio.to_thread(runtime.media.restart_recording_for_device_change)
        await runtime.refresh_status()
        return runtime.device_payload(await asyncio.to_thread(runtime.audio.cards))

    @app.post("/api/{target}/volume")
    async def volume(target: str, request: VolumeRequest):
        if target not in {"pc1", "pc2", "headset", "mic", "soundboard"} or not 0 <= request.value <= 100:
            raise HTTPException(400, "invalid volume")
        await asyncio.to_thread(runtime.audio.set_volume, target, request.value)
        return await fresh_status()

    @app.post("/api/{target}/mute")
    async def mute(target: str, request: BooleanRequest):
        if target not in {"pc1", "pc2", "mic"}:
            raise HTTPException(400, "invalid target")
        await asyncio.to_thread(runtime.audio.set_mute, target, request.value)
        return await fresh_status()

    @app.post("/api/mic/route/{computer}")
    async def route_microphone(computer: str, request: BooleanRequest):
        if computer not in {"pc1", "pc2"}:
            raise HTTPException(400, "invalid computer")
        await asyncio.to_thread(runtime.audio.set_route, computer, request.value)
        return await fresh_status()

    @app.post("/api/preset/{name}")
    async def apply_preset(name: str):
        if name != "mute-all" and name not in DEFAULT_PRESETS:
            raise HTTPException(404, "preset not found")
        if name == "mute-all":
            restore_keys = ("pc1_mute", "pc2_mute", "mic_mute", "mic_pc1", "mic_pc2")

            def toggle_mute_all(current: dict) -> None:
                if current.get("mute_all_active"):
                    saved = current.get("mute_all_restore") or {}
                    for key in restore_keys:
                        if key in saved:
                            current[key] = saved[key]
                    current["mute_all_active"] = False
                    current["mute_all_restore"] = {}
                else:
                    current["mute_all_restore"] = {key: current[key] for key in restore_keys}
                    current.update({
                        "pc1_mute": True, "pc2_mute": True, "mic_mute": True,
                        "mic_pc1": False, "mic_pc2": False, "mute_all_active": True,
                    })

            runtime.store.mutate(toggle_mute_all)
        else:
            saved_presets = runtime.store.get("presets", {})
            values = saved_presets.get(name, DEFAULT_PRESETS[name])
            runtime.store.update({
                **{key: values[key] for key in PRESET_KEYS if key in values},
                "mute_all_active": False,
                "mute_all_restore": {},
            })
        await asyncio.to_thread(runtime.audio.apply_controls)
        LOG.info("Preset applied: %s", name)
        return await fresh_status()

    @app.get("/api/presets")
    async def presets():
        return {"presets": runtime.store.get("presets", {})}

    @app.post("/api/presets/{name}/save")
    async def save_preset(name: str):
        if name not in DEFAULT_PRESETS:
            raise HTTPException(404, "editable preset not found")

        def capture(current: dict) -> None:
            current.setdefault("presets", {})[name] = {key: current[key] for key in PRESET_KEYS}

        saved = runtime.store.mutate(capture)["presets"][name]
        LOG.info("Preset saved: %s", name)
        return {"name": name, "values": saved}

    @app.get("/api/soundboard")
    async def soundboard():
        result = runtime.media.soundboard_status()
        result["files"] = await asyncio.to_thread(runtime.media.soundboard_files)
        return result

    @app.post("/api/soundboard/upload")
    async def upload_sound(file: UploadFile = File(...)):
        try:
            await asyncio.to_thread(runtime.media.store_upload, file.filename or "", file.file)
        finally:
            await file.close()
        return await soundboard()

    @app.post("/api/soundboard/stop")
    async def stop_soundboard():
        await asyncio.to_thread(runtime.media.stop_soundboard)
        return await soundboard()

    @app.post("/api/soundboard/{name}/play")
    async def play_soundboard(name: str):
        await asyncio.to_thread(runtime.media.play, name)
        return await soundboard()

    @app.get("/api/soundboard/{name}")
    async def download_sound(name: str):
        path = runtime.media.soundboard_path(name)
        return FileResponse(path, media_type="audio/mpeg", filename=path.name)

    @app.post("/api/soundboard/{name}/rename")
    async def rename_sound(name: str, request: RenameRequest):
        source = runtime.media.soundboard_path(name)
        new_name = request.name.strip()
        if not valid_sound_filename(new_name):
            raise HTTPException(400, "invalid filename")
        target = source.with_name(new_name)
        if target.exists() and target != source:
            raise HTTPException(409, "file exists")
        source.rename(target)
        if runtime.store.get("soundboard_playing") == name:
            runtime.store.update({"soundboard_playing": new_name})
        return await soundboard()

    @app.delete("/api/soundboard/{name}")
    async def delete_sound(name: str):
        if runtime.store.get("soundboard_playing") == name:
            await asyncio.to_thread(runtime.media.stop_soundboard)
        runtime.media.soundboard_path(name).unlink()
        return await soundboard()

    @app.post("/api/recording/toggle")
    async def toggle_recording():
        action = runtime.media.stop_recording if runtime.media.recording_active() else runtime.media.start_recording
        await asyncio.to_thread(action)
        return await fresh_status()

    @app.post("/api/recording/session/{action}")
    async def recording_action(action: str):
        if action == "start":
            await asyncio.to_thread(runtime.media.start_recording)
        elif action == "stop":
            await asyncio.to_thread(runtime.media.stop_recording)
        else:
            raise HTTPException(400, "invalid recording action")
        return await fresh_status()

    @app.post("/api/recording/{action}-all")
    async def recording_all(action: str):
        return await recording_action(action)

    @app.get("/api/recordings")
    async def recordings():
        return await asyncio.to_thread(runtime.media.recording_files)

    @app.post("/api/recordings/playback/stop")
    async def stop_recording_playback():
        await asyncio.to_thread(runtime.media.stop_recording_playback)
        return await fresh_status()

    @app.post("/api/recordings/{relative:path}/play")
    async def play_recording(relative: str):
        await asyncio.to_thread(runtime.media.play_recording, relative)
        return await fresh_status()

    @app.get("/api/recordings/{relative:path}")
    async def download_recording(relative: str):
        path = runtime.media.recording_path(relative)
        return FileResponse(path, media_type="audio/ogg", filename=path.name)

    @app.post("/api/recordings/{relative:path}/rename")
    async def rename_recording(relative: str, request: RenameRequest):
        source = runtime.media.recording_path(relative)
        runtime.media.ensure_recording_not_in_use(source)
        new_name = request.name.strip()
        if not valid_recording_filename(new_name):
            raise HTTPException(400, "invalid filename")
        target = source.with_name(new_name)
        if target.exists() and target != source:
            raise HTTPException(409, "file exists")
        source.rename(target)
        return await recordings()

    @app.delete("/api/recordings/{relative:path}")
    async def delete_recording(relative: str):
        path = runtime.media.recording_path(relative)
        runtime.media.ensure_recording_not_in_use(path)
        path.unlink()
        return await recordings()

    @app.websocket("/ws")
    async def websocket_status(websocket: WebSocket):
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host")
        if origin and urlparse(origin).netloc != host:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(await runtime.status())
                await asyncio.sleep(config.websocket_interval_seconds)
        except Exception:
            return

    @app.get("/")
    async def index():
        return FileResponse(config.frontend_dir / "index.html", media_type="text/html")

    return app


APP = create_app()
