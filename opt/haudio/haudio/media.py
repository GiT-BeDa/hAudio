"""Soundboard playback, combined recording, and retention."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException

from .audio import AudioController
from .config import Config
from .state import StateStore

LOG = logging.getLogger("haudio.media")


def valid_sound_filename(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.,'()&+\-]{0,120}\.mp3", name, re.I))


def valid_recording_filename(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.'()&+\-]{0,120}\.opus", name, re.I))


class MediaManager:
    def __init__(self, config: Config, store: StateStore, audio: AudioController):
        self.config = config
        self.store = store
        self.audio = audio
        self._lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.process_logs: dict[str, BinaryIO] = {}
        self.recording_prefix = ""
        self.recording_playback_path = ""
        self.last_error = ""
        self.last_recording_retry = 0.0

    def soundboard_path(self, name: str) -> Path:
        path = (self.config.soundboard_dir / name).resolve()
        root = self.config.soundboard_dir.resolve()
        if root not in path.parents or not path.is_file() or path.suffix.lower() != ".mp3":
            raise HTTPException(404, "soundboard file not found")
        return path

    def recording_path(self, relative: str) -> Path:
        try:
            path = (self.config.recording_dir / relative).resolve()
        except Exception as exc:
            raise HTTPException(400, "invalid path") from exc
        root = self.config.recording_dir.resolve()
        if root not in path.parents or not path.is_file() or path.suffix.lower() != ".opus":
            raise HTTPException(404, "recording not found")
        return path

    def soundboard_files(self) -> list[dict]:
        return [
            {"name": path.name, "size": path.stat().st_size, "modified": path.stat().st_mtime}
            for path in sorted(self.config.soundboard_dir.glob("*.mp3"), key=lambda item: item.name.lower())
        ]

    def recording_files(self, limit: int | None = None, offset: int = 0) -> list[dict]:
        playback = self.recording_playback_status()
        files = sorted(
            self.config.recording_dir.rglob("*.opus"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if offset:
            files = files[offset:]
        if limit is not None:
            files = files[:limit]
        return [
            {
                "path": str(path.relative_to(self.config.recording_dir)),
                "name": path.name,
                "size": path.stat().st_size,
                "modified": path.stat().st_mtime,
                "active": bool(self.recording_prefix and path.name.startswith(self.recording_prefix)),
                "playing": bool(playback["active"] and playback["path"] == str(path.relative_to(self.config.recording_dir))),
            }
            for path in files
        ]

    def recording_count(self) -> int:
        return sum(1 for _ in self.config.recording_dir.rglob("*.opus"))

    def validate_mp3(self, path: Path) -> bool:
        result = self.audio.run(
            "/usr/bin/ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name", "-of", "default=nk=1:nw=1", str(path),
            timeout=15,
        )
        return result.returncode == 0 and "mp3" in result.stdout.lower()

    def store_upload(self, filename: str, source: BinaryIO) -> None:
        original = Path(filename).name
        if not valid_sound_filename(original):
            raise HTTPException(400, "only MP3 files with a safe filename are allowed")
        target = self.config.soundboard_dir / original
        if target.exists():
            raise HTTPException(409, "a soundboard file with this name already exists")
        temporary: Path | None = None
        total = 0
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.config.soundboard_dir, prefix=".upload-", suffix=".tmp", delete=False
            ) as output:
                temporary = Path(output.name)
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > self.config.soundboard_max_bytes:
                        raise HTTPException(400, "file exceeds configured upload limit")
                    output.write(chunk)
            if total == 0:
                raise HTTPException(400, "file is empty")
            if not self.validate_mp3(temporary):
                raise HTTPException(400, "file does not contain a valid MP3 audio stream")
            os.replace(temporary, target)
            temporary = None
            LOG.info("Soundboard file uploaded: %s", original)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _stop_process(process: subprocess.Popen | None, timeout: float = 2.0) -> None:
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _spawn(self, key: str, args: list[str]) -> subprocess.Popen:
        error_log = tempfile.TemporaryFile(mode="w+b")
        try:
            process = subprocess.Popen(
                args,
                env=self.audio.environment(),
                stdout=subprocess.DEVNULL,
                stderr=error_log,
            )
        except Exception:
            error_log.close()
            raise
        self.processes[key] = process
        self.process_logs[key] = error_log
        return process

    def _read_process_log(self, key: str) -> str:
        handle = self.process_logs.pop(key, None)
        if not handle:
            return ""
        try:
            handle.flush()
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 8192))
            value = handle.read().decode(errors="replace").strip()
            return " | ".join(line.strip() for line in value.splitlines()[-3:] if line.strip())
        finally:
            handle.close()

    def _stop_named_process(self, key: str) -> None:
        self._stop_process(self.processes.pop(key, None))
        self._read_process_log(key)

    def play(self, name: str) -> None:
        path = self.soundboard_path(name)
        with self._lock:
            self._stop_named_process("soundboard")
            args = [
                "/usr/bin/ffmpeg", "-hide_banner", "-loglevel", "warning", "-re", "-i", str(path),
                "-vn", "-af", "apad=pad_dur=2", "-ac", "2", "-ar", str(self.config.sample_rate),
                "-flush_packets", "1", "-f", "pulse", "-buffer_duration", "2000",
                "-device", "HAUDIO_SOUNDBOARD", "-",
            ]
            self._spawn("soundboard", args)
            self.store.update({"soundboard_playing": name})
            self.last_error = ""
            LOG.info("Soundboard playback started: %s", name)

    def stop_soundboard(self) -> None:
        with self._lock:
            self._stop_named_process("soundboard")
            self.store.update({"soundboard_playing": ""})
            LOG.info("Soundboard playback stopped")

    def soundboard_status(self) -> dict:
        with self._lock:
            process = self.processes.get("soundboard")
            active = bool(process and process.poll() is None)
            if not active and self.store.get("soundboard_playing", ""):
                self.store.update({"soundboard_playing": ""})
            return {
                "playing": self.store.get("soundboard_playing", ""),
                "active": active,
                "volume": self.store.get("soundboard_volume", 100),
            }

    def start_recording(self, requested_by_user: bool = True) -> None:
        with self._lock:
            process = self.processes.get("session")
            if process and process.poll() is None:
                return
            nodes = self.audio.nodes()
            headset = nodes["headset"]
            microphone = nodes["mic_in"]
            microphone_required = not self.store.get("mic_mute")
            if not headset or (microphone_required and not microphone):
                if requested_by_user:
                    detail = "a headset must be assigned before recording"
                    if headset and microphone_required:
                        detail = "a microphone must be assigned before recording while it is unmuted"
                    raise HTTPException(409, detail)
                return
            directory = self.config.recording_dir / time.strftime("%Y-%m-%d")
            directory.mkdir(parents=True, exist_ok=True)
            self.recording_prefix = time.strftime("%Y-%m-%d_%H-%M-%S_headset-session")
            output = directory / f"{self.recording_prefix}.%03d.opus"
            args = [
                "/usr/bin/ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-thread_queue_size", "512", "-f", "pulse", "-i", f"{headset}.monitor",
            ]
            if self.store.get("mic_mute"):
                audio_filter = f"[0:a]aresample={self.config.sample_rate},alimiter=limit=0.95[out]"
            else:
                assert microphone is not None
                args.extend([
                    "-thread_queue_size", "512", "-f", "pulse", "-i", microphone,
                ])
                audio_filter = (
                    f"[0:a]aresample={self.config.sample_rate}[a];"
                    f"[1:a]aresample={self.config.sample_rate},volume=0.70,"
                    "pan=stereo|c0=c0|c1=c0[b];"
                    "[a][b]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
                    "alimiter=limit=0.95[out]"
                )
            args.extend([
                "-filter_complex", audio_filter, "-map", "[out]", "-ac", "2",
                "-ar", str(self.config.sample_rate), "-c:a", "libopus",
                "-b:a", self.config.recording_bitrate, "-f", "segment",
                "-segment_time", str(self.config.recording_segment_seconds), str(output),
            ])
            self._spawn("session", args)
            self.store.mutate(lambda value: value.setdefault("recording", {}).update({"session": True}))
            self.last_error = ""
            LOG.info("Combined recording started")

    def stop_recording(self) -> None:
        with self._lock:
            self.store.mutate(lambda value: value.setdefault("recording", {}).update({"session": False}))
            self._stop_named_process("session")
            self.recording_prefix = ""
            LOG.info("Combined recording stopped")

    def restart_recording_for_device_change(self) -> None:
        """Reconnect an active/desired recording to newly assigned nodes."""
        with self._lock:
            desired = bool(self.store.get("recording", {}).get("session"))
            self._stop_named_process("session")
            self.recording_prefix = ""
            if desired:
                self.start_recording(requested_by_user=False)

    def recording_active(self) -> bool:
        with self._lock:
            process = self.processes.get("session")
            return bool(process and process.poll() is None)

    def play_recording(self, relative: str) -> None:
        path = self.recording_path(relative)
        self.ensure_not_active_recording(path)
        nodes = self.audio.nodes()
        if not nodes["headset"]:
            raise HTTPException(409, "a headset must be assigned before playback")
        with self._lock:
            self._stop_named_process("recording-playback")
            args = [
                "/usr/bin/ffmpeg", "-hide_banner", "-loglevel", "warning", "-re", "-i", str(path),
                "-vn", "-ac", "2", "-ar", str(self.config.sample_rate),
                "-flush_packets", "1", "-f", "pulse", "-buffer_duration", "2000",
                "-device", nodes["headset"], "-",
            ]
            self._spawn("recording-playback", args)
            self.recording_playback_path = str(path.relative_to(self.config.recording_dir))
            self.last_error = ""
            LOG.info("Recording playback started on headset: %s", self.recording_playback_path)

    def stop_recording_playback(self) -> None:
        with self._lock:
            self._stop_named_process("recording-playback")
            self.recording_playback_path = ""
            LOG.info("Recording playback stopped")

    def recording_playback_status(self) -> dict:
        with self._lock:
            process = self.processes.get("recording-playback")
            active = bool(process and process.poll() is None)
            if not active:
                self.recording_playback_path = ""
            return {
                "active": active,
                "path": self.recording_playback_path,
                "name": Path(self.recording_playback_path).name if self.recording_playback_path else "",
            }

    def ensure_not_active_recording(self, path: Path) -> None:
        if self.recording_active() and self.recording_prefix and path.name.startswith(self.recording_prefix):
            raise HTTPException(409, "stop the active recording before modifying this file")

    def ensure_recording_not_in_use(self, path: Path) -> None:
        self.ensure_not_active_recording(path)
        playback = self.recording_playback_status()
        relative = str(path.relative_to(self.config.recording_dir))
        if playback["active"] and playback["path"] == relative:
            raise HTTPException(409, "stop playback before modifying this file")

    def recording_in_use(self, path: Path) -> bool:
        try:
            self.ensure_recording_not_in_use(path)
        except HTTPException:
            return True
        return False

    def poll(self) -> None:
        """Update process state and retry desired recording after transient device loss."""
        with self._lock:
            for key, process in list(self.processes.items()):
                code = process.poll()
                if code is None:
                    continue
                self.processes.pop(key, None)
                details = self._read_process_log(key)
                suffix = f": {details}" if details else ""
                if key == "soundboard":
                    self.store.update({"soundboard_playing": ""})
                    if code != 0:
                        self.last_error = f"Soundboard process exited with code {code}{suffix}"
                elif key == "session" and code != 0:
                    self.last_error = f"Recording process exited with code {code}{suffix}; retrying when possible"
                elif key == "recording-playback":
                    self.recording_playback_path = ""
                    if code != 0:
                        self.last_error = f"Recording playback exited with code {code}{suffix}"
                log = LOG.info if code == 0 else LOG.warning
                log("%s process exited with code %s%s", key, code, suffix)
            desired = bool(self.store.get("recording", {}).get("session"))
            if desired and not self.recording_active() and time.monotonic() - self.last_recording_retry >= 5:
                self.last_recording_retry = time.monotonic()
                try:
                    self.start_recording(requested_by_user=False)
                except Exception:
                    LOG.exception("Unable to restart recording")

    def cleanup_recordings(self) -> int:
        """Delete oldest files by age and free-space policy."""
        deleted = 0
        now = time.time()
        max_age = self.config.recording_max_age_days * 86400
        files = sorted(self.config.recording_dir.rglob("*.opus"), key=lambda item: item.stat().st_mtime)
        for path in list(files):
            if max_age > 0 and now - path.stat().st_mtime > max_age:
                if self.recording_in_use(path):
                    LOG.info("Retention skipped recording in use: %s", path)
                    continue
                path.unlink(missing_ok=True)
                files.remove(path)
                deleted += 1
                LOG.info("Retention removed old recording: %s", path)
        minimum = self.config.recording_min_free_gb * 1_000_000_000
        for path in files:
            usage = shutil.disk_usage(self.config.recording_dir)
            usage_percent = usage.used * 100 / usage.total
            enough_free = minimum <= 0 or usage.free >= minimum
            below_maximum = (
                self.config.recording_max_disk_usage_percent <= 0
                or usage_percent <= self.config.recording_max_disk_usage_percent
            )
            if enough_free and below_maximum:
                break
            if self.recording_in_use(path):
                LOG.info("Retention skipped recording in use: %s", path)
                continue
            path.unlink(missing_ok=True)
            deleted += 1
            LOG.warning("Retention removed recording to recover disk space: %s", path)
        return deleted

    def stop_all(self) -> None:
        with self._lock:
            self._stop_named_process("soundboard")
            self._stop_named_process("session")
            self._stop_named_process("recording-playback")
            self.recording_playback_path = ""
