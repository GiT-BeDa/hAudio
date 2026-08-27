# hAudio HTTP API

The control API is served by the same FastAPI process as the web interface.
The default base URL is `http://<raspberry-pi-address>:8765`.

All state-changing endpoints use `POST` with a JSON body unless noted
otherwise. Successful responses contain the current state returned by
`GET /api/status`; device and file endpoints return their resource-specific
state. Errors are JSON objects such as `{"detail":"invalid volume"}`.

## Status and devices

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/status` | Complete live state: computers, headset, microphone, recording, soundboard, levels, system and devices. |
| `GET` | `/api/devices` | Lists detected USB audio cards and current assignments. |
| `POST` | `/api/devices/assign` | Assign a card: `{"role":"pc1\|pc2\|headset","card_id":"..."}`. Use an empty `card_id` to unassign. |

Volume values are integer percentages from 0 to 100. Mute and microphone
route values are booleans.

## Audio controls

| Method | Endpoint | JSON body |
| --- | --- | --- |
| `POST` | `/api/pc1/volume` | `{"value":70}` |
| `POST` | `/api/pc2/volume` | `{"value":70}` |
| `POST` | `/api/headset/volume` | `{"value":65}` |
| `POST` | `/api/mic/volume` | `{"value":50}` |
| `POST` | `/api/soundboard/volume` | `{"value":100}` |
| `POST` | `/api/pc1/mute` | `{"value":true}` |
| `POST` | `/api/pc2/mute` | `{"value":true}` |
| `POST` | `/api/mic/mute` | `{"value":true}` |
| `POST` | `/api/mic/route/pc1` | `{"value":true}` |
| `POST` | `/api/mic/route/pc2` | `{"value":true}` |

## Soundboard

`GET /api/soundboard` returns `{ "files": [], "playing": "", "volume": 100 }`.
Soundboard playback is independent of the browser and is routed to the
headset and non-muted computer outputs.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/soundboard/upload` | Multipart upload with form field `file`; MP3 only, maximum 200 MB. |
| `POST` | `/api/soundboard/{name}/play` | Start or replace playback of an MP3. |
| `POST` | `/api/soundboard/stop` | Stop playback. |
| `GET` | `/api/soundboard/{name}` | Download an MP3. |
| `POST` | `/api/soundboard/{name}/rename` | Rename with `{"name":"new-name.mp3"}`. |
| `DELETE` | `/api/soundboard/{name}` | Delete an MP3. |

## Recording management

Version 0.01 records the combined headset output and microphone into one
segmented Opus session.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/recordings` | List recordings. |
| `POST` | `/api/recording/toggle` | Start or stop the combined recording. |
| `POST` | `/api/recording/session/start` | Start recording explicitly. |
| `POST` | `/api/recording/session/stop` | Stop recording explicitly. |
| `POST` | `/api/recording/start-all` | Compatibility alias for starting the session. |
| `POST` | `/api/recording/stop-all` | Compatibility alias for stopping the session. |
| `GET` | `/api/recordings/{path}` | Download an Opus file. |
| `POST` | `/api/recordings/{path}/rename` | Rename with `{"name":"new-name.opus"}`. |
| `DELETE` | `/api/recordings/{path}` | Delete a recording. |

## Live updates

Connect a WebSocket to `/ws`. The server sends a JSON status object about once
per second. It includes the same main state sections as `/api/status`, plus
live dB levels in `levels`.

Example:

```json
{
  "pc1": {"connected": true, "volume": 70, "mute": false},
  "soundboard": {"playing": "alert.mp3", "active": true, "volume": 80},
  "levels": {"pc1": -18.2, "pc2": -60.0, "microphone": -24.1, "headset": -14.0}
}
```

The API currently has no authentication or HTTPS. Expose it only to a trusted
local network until access control and TLS are added.
