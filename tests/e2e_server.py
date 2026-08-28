"""Small dependency-free HTTP fixture for browser layout tests."""

from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parents[1]
FRONTEND = ROOT / "opt" / "haudio" / "frontend"

STATUS = {
    "name": "hAudio",
    "version": "0.03",
    "online": True,
    "pc1": {"connected": True, "volume": 50, "mute": False},
    "pc2": {"connected": True, "volume": 50, "mute": False},
    "headset": {"connected": True, "volume": 100},
    "microphone": {
        "connected": True,
        "volume": 100,
        "mute": False,
        "route_pc1": True,
        "route_pc2": True,
    },
    "recording": {"session": False, "playback": {"active": False, "path": "", "name": ""}},
    "soundboard": {"active": False, "playing": "", "volume": 100},
    "levels": {"pc1": -20.0, "pc2": -24.0, "headset": -18.0, "microphone": -30.0},
    "system": {
        "pipewire": True,
        "graph_ready": True,
        "disk_free_gb": 20.0,
        "cpu_load": 0.5,
        "ram_used_percent": 25.0,
        "temperature_c": 50.0,
        "uptime_seconds": 3600,
        "connection_type": "LAN",
        "network_interface": "eth0",
        "primary_ip": "192.0.2.10",
        "wlan_connected": False,
    },
    "devices": {"cards": [], "selected": {}, "assignments": {}},
    "presets": {"mute_all_active": False},
    "errors": [],
}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path.startswith("/index.html"):
            self._file(FRONTEND / "index.html", "text/html; charset=utf-8")
        elif self.path.startswith("/static/"):
            name = self.path.split("?", 1)[0].removeprefix("/static/")
            content_type = "text/javascript" if name.endswith(".js") else "text/css"
            self._file(FRONTEND / name, content_type)
        elif self.path.startswith("/api/status"):
            self._json(STATUS)
        elif self.path.startswith("/api/recordings"):
            self._json({"files": [], "total": 0, "limit": 100, "offset": 0})
        elif self.path.startswith("/api/soundboard"):
            self._json({"files": [], "active": False, "playing": "", "volume": 100})
        elif self.path == "/health/live":
            self._json({"status": "alive"})
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        if self.path.startswith("/api/"):
            self._json(STATUS)
        else:
            self.send_error(404)

    def _json(self, value: object) -> None:
        payload = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _file(self, path: Path, content_type: str) -> None:
        if path.parent != FRONTEND or not path.is_file():
            self.send_error(404)
            return
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8876), Handler).serve_forever()
