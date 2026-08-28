# hAudio HTTP API

The control API is served by the same FastAPI process as the web interface.
The default base URL is `http://<raspberry-pi-address>:8765`. Replace the
placeholder with the Pi's current LAN address; do not include the angle
brackets in the actual URL.

FastAPI also provides interactive OpenAPI documentation at `/docs` and the raw
schema at `/openapi.json`.

All state-changing endpoints use `POST` with a JSON body unless noted
otherwise. Audio control endpoints generally return the current system state;
device, preset-save, and media endpoints return their resource-specific state.
Errors are JSON objects such as `{"detail":"invalid volume"}`.
When optional Basic authentication is enabled, all UI, API, download, and
WebSocket requests require the configured credentials. Health endpoints remain
available without credentials for service supervision.

Quick examples:

~~~bash
curl --fail http://<raspberry-pi-address>:8765/api/status
curl --fail -X POST http://<raspberry-pi-address>:8765/api/pc1/volume \
  -H 'Content-Type: application/json' \
  -d '{"value":50}'
~~~

## Status and devices

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/status` | Complete live state: computers, headset, microphone, recording, soundboard, levels, system and devices. |
| `GET` | `/api/devices` | Lists detected USB audio cards and current assignments. |
| `POST` | `/api/devices/assign` | Assign a card: `{"role":"pc1\|pc2\|headset","card_id":"..."}`. Use an empty `card_id` to unassign. Duplicate role assignments return `409`. |

Volume values are integer percentages from 0 to 100. Mute and microphone
route values are booleans.
The `controls` object in `/api/status` reports which desired controls were most
recently confirmed by PipeWire. A failed control also appears in `errors`.

## Audio controls

| Method | Endpoint | JSON body |
| --- | --- | --- |
| `POST` | `/api/pc1/volume` | `{"value":50}` |
| `POST` | `/api/pc2/volume` | `{"value":50}` |
| `POST` | `/api/headset/volume` | `{"value":100}` |
| `POST` | `/api/mic/volume` | `{"value":100}` |
| `POST` | `/api/soundboard/volume` | `{"value":100}` |
| `POST` | `/api/pc1/mute` | `{"value":true}` |
| `POST` | `/api/pc2/mute` | `{"value":true}` |
| `POST` | `/api/mic/mute` | `{"value":true}` |
| `POST` | `/api/mic/route/pc1` | `{"value":true}` |
| `POST` | `/api/mic/route/pc2` | `{"value":true}` |

## Presets

Apply a preset with `POST /api/preset/{name}`. Available names are `normal`,
`pc1-only`, `pc2-only`, `meeting`, and `mute-all`. `mute-all` is a toggle: the
first request mutes all routes and the next request restores the exact previous
mute and routing state.

`GET /api/presets` returns the persistently saved editable presets. Save the
current volumes, mutes, and microphone routes with
`POST /api/presets/{name}/save`, where `{name}` is `normal`, `pc1-only`,
`pc2-only`, or `meeting`. Presets do not start or stop recording.

Activating a microphone route with `POST /api/mic/route/{computer}` also clears
global microphone mute so the newly activated route is immediately usable.
The web interface keeps saved routes visually inactive while global microphone
mute is enabled.

## Soundboard

`GET /api/soundboard` returns `{ "files": [], "playing": "", "active": false,
"volume": 100 }`.
Soundboard playback is independent of the browser and is routed to the
headset and currently active microphone routes.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/soundboard/upload` | Multipart upload with form field `file`; filename and MP3 audio stream are validated; maximum size is configurable. |
| `POST` | `/api/soundboard/{name}/play` | Start or replace playback of an MP3. |
| `POST` | `/api/soundboard/stop` | Stop playback. |
| `GET` | `/api/soundboard/{name}` | Download an MP3. |
| `POST` | `/api/soundboard/{name}/rename` | Rename with `{"name":"new-name.mp3"}`. |
| `DELETE` | `/api/soundboard/{name}` | Delete an MP3. |

## Recording management

Version 0.03 records the combined headset output and microphone into one
segmented Opus session.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/recordings?limit=100&offset=0` | List a bounded recording page as `{files,total,limit,offset}`; maximum limit is 500. |
| `POST` | `/api/recording/toggle` | Start or stop the combined recording. |
| `POST` | `/api/recording/session/start` | Start recording explicitly. |
| `POST` | `/api/recording/session/stop` | Stop recording explicitly. |
| `POST` | `/api/recording/start-all` | Compatibility alias for starting the session. |
| `POST` | `/api/recording/stop-all` | Compatibility alias for stopping the session. |
| `POST` | `/api/recordings/{path}/play` | Play an Opus recording directly on the assigned headset. |
| `POST` | `/api/recordings/playback/stop` | Stop direct recording playback. |
| `GET` | `/api/recordings/{path}` | Download an Opus file. |
| `POST` | `/api/recordings/{path}/rename` | Rename with `{"name":"new-name.opus"}`. |
| `DELETE` | `/api/recordings/{path}` | Delete a recording. |

Recording playback uses the assigned headset sink directly. It is not routed
through the soundboard bus and therefore cannot reach PC1 or PC2. Starting a
different recording replaces the current playback.

An active recording segment cannot be played, renamed, or deleted. A recording
that is currently playing cannot be renamed or deleted. These requests return
`409`; stop the relevant operation first.

Global Mic Mute is also a recording privacy control. A muted recording segment
contains headset output but no microphone input. Changing Mic Mute while a
session is active rotates to a new segment.

## Health checks

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health/live` | Process liveness and version; returns `200` while the backend responds. |
| `GET` | `/health/ready` | Audio readiness; returns `503` while the assigned graph is degraded. |

## Live updates

Connect a WebSocket to `/ws`. By default, the server sends four lightweight
status updates per second (`websocket_interval_seconds` is `0.25`). It includes
the same main state sections as `/api/status`, plus live dB levels in `levels`.
The browser automatically reconnects after a lost connection. Browser
WebSocket origins must match the hAudio host.

Example:

```json
{
  "pc1": {"connected": true, "volume": 50, "mute": false},
  "recording": {"session": false, "playback": {"active": true, "path": "2026-08-28/session.opus", "name": "session.opus"}},
  "soundboard": {"playing": "alert.mp3", "active": true, "volume": 80},
  "levels": {"pc1": -18.2, "pc2": -60.0, "microphone": -24.1, "headset": -14.0},
  "system": {"pipewire": true, "graph_ready": true, "disk_free_gb": 42.1},
  "errors": []
}
```

The API has no built-in HTTPS. Optional Basic authentication is described in
[CONFIGURATION.md](CONFIGURATION.md), but it is not a substitute for transport
encryption. Expose the service only to a trusted local network or through a
VPN/TLS tunnel.

For `{name}` and `{path}`, URL-encode special characters before placing them
in the request URL. For example, a space becomes `%20`.
