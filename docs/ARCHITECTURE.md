# hAudio architecture

hAudio keeps live audio in PipeWire. The browser carries control and status
data only; closing or reloading it does not remove audio routes.

~~~text
USB audio devices
       │
       ▼
PipeWire / PipeWire-Pulse  ◄──── FFmpeg recording and soundboard processes
       ▲
       │ serialized, idempotent graph operations
       │
hAudio runtime ───── cached status and live levels ───── FastAPI/WebSocket
                                                           ▲
                                                           │ control data
                                                        Browser
~~~

## Backend modules

- `config.py` loads validated JSON configuration and environment overrides.
- `state.py` owns thread-safe state and atomic persistence.
- `audio.py` discovers actual PipeWire nodes and reconciles only stale routes.
- `media.py` supervises soundboard, combined recording, direct headset-only
  recording playback, and retention.
- `app.py` owns lifecycle tasks, status caching, API routes, and WebSockets.
- `haudio_main.py` is the stable Uvicorn entry point.

All blocking `pactl`, FFmpeg, filesystem, and system-status work is moved away
from the asyncio event loop. One cached status refresh serves any number of
WebSocket clients. Lightweight live level values are inserted into each
WebSocket message without repeating hardware enumeration.
Device updates are event-driven through `pactl subscribe`, with a slower health
check as a safety net. Individual control requests touch only their target;
full control reconciliation is reserved for startup, presets, and graph repair.
Live `pw-cat` meters run only while the browser has a WebSocket connection.

## Failure boundaries

- A browser or WebSocket failure affects control visibility only.
- A backend restart preserves healthy server-side PipeWire loopbacks.
- A failed recording or soundboard process is reported without removing live
  computer/headset routes.
- Recording playback targets the assigned headset sink directly and is isolated
  from both computer microphone routes.
- A requested recording is retried after a temporary device loss.
- Invalid or duplicate device assignments are rejected before graph changes.
- PipeWire command failures remain visible until that control is confirmed.
- A missing assigned role degrades readiness even when remaining routes work.

The generic installation runs hAudio, PipeWire, and WirePlumber in one
persistent systemd user manager. This avoids crossing incompatible PulseAudio
runtime directories.
