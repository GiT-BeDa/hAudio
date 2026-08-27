# hAudio 0.01 – Audio Routing for Two Computers

[![Tests](https://github.com/GiT-BeDa/hAudio/actions/workflows/tests.yml/badge.svg)](https://github.com/GiT-BeDa/hAudio/actions/workflows/tests.yml)
[![License: GPL v3 or later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.01-22c55e.svg)](VERSION)

- Updated: 2026-08-28
- Author: Peter Grunert
- Website: <https://www.bk99.de>
- License: GNU General Public License v3.0 or later (GPL-3.0-or-later)

hAudio is a permanently running Raspberry Pi audio-routing system. It mixes
audio from PC1 and PC2 to a wireless headset and routes the headset microphone
independently to PC1, PC2, both, or neither computer.

## Web interface

The responsive web interface provides independent computer volume and mute
controls, microphone routing, live level meters, recording management,
hardware assignment, editable persistent presets, and a soundboard. `MUTE ALL`
restores the previous mute/routing state when pressed a second time.

![hAudio web interface](docs/images/WebInterface.png)

The screenshot shows the current interface from an example deployment. Device
names and assignments vary with the connected hardware. Its private LAN address
has been replaced with the documentation-only example address `192.0.2.10`.

The endpoint reference is available in [docs/API.md](docs/API.md).
The process and failure-boundary design is described in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick installation

For a complete installation, including system packages, writable directory
ownership, an isolated Python environment, the PipeWire user session, and
verification, follow [docs/INSTALL.md](docs/INSTALL.md). Do not run PipeWire as
root or place the backend in a different runtime session from PipeWire.

Open the web interface at `http://<raspberry-pi-address>:8765`. See
`docs/INSTALL.md` for backups, verification, and recovery details.

## Signal paths

~~~text
PC1 -> USB audio interface A --------------┐
                                           ├-> PipeWire mix -> USB headset adapter -> headset
PC2 -> USB audio interface B --------------┘

Headset microphone -> USB headset adapter -> PipeWire -> PC1 and/or PC2
~~~

## Optional hardware examples

hAudio is designed to work with compatible USB audio adapters and 3.5 mm
audio cables; the exact brands and models are not required. The following
links are examples of hardware used for this type of setup:

- [USB audio adapters](https://amzn.to/4xrTc1R)
- [3.5 mm AUX audio cables](https://amzn.to/4hZ3JfU)

These are affiliate links. If you buy through one of them, the maintainer may
receive a commission at no additional cost to you. Product links are optional;
equivalent compatible hardware can be used. As an Amazon Associate I earn
from qualifying purchases.

Identical USB audio interfaces are distinguished by their physical USB paths
or by explicit assignments in the web interface. ALSA card numbers and USB
device numbers are not permanent identifiers. Any compatible headset adapter
can be selected for the headset role.

## Included files

- opt/haudio/haudio_main.py – stable Uvicorn entry point
- opt/haudio/haudio/ – configuration, atomic state, PipeWire graph, media
  processes, status monitoring, and FastAPI routes
- opt/haudio/frontend/ – static HTML, JavaScript, and CSS for the web interface
- etc/systemd/user/haudio-control.service – automatic backend start/restart in
  the same user session as PipeWire
- etc/haudio/haudio.json – generic recording and audio configuration
- etc/pipewire/pipewire.conf.d/haudio.conf – 48 kHz audio parameters
- docs/ – installation, operations, API, and reference-setup documentation
- requirements.txt and requirements-dev.txt – flexible runtime and test dependencies
- requirements-lock.txt and requirements-dev-lock.txt – tested dependency baselines
- tests/ – automated unit tests for safety-critical helper logic

Runtime state, recordings, credentials, and private SSH keys are not included.

## Development and tests

Install runtime and development dependencies with `pip install -r
requirements-dev.txt`, then run:

~~~bash
python3 -m py_compile opt/haudio/haudio_main.py
python3 -m compileall -q opt/haudio/haudio
node --check opt/haudio/frontend/app.js
pytest -q
~~~

The tests cover atomic persistence, actual PipeWire node discovery, partial
graphs, microphone and soundboard routing, API route behavior, duplicate
assignment protection, and the complete static frontend.

## Related projects

If you like hAudio, check also [deskhop](https://github.com/hrvach/deskhop),
an open-source project for sharing keyboard, mouse, and display control.

## Recording and soundboard

The web interface provides one combined recording containing headset output and
headset microphone, stored as segmented Opus files under
/data/haudio/recordings/YYYY-MM-DD/. Recordings can be started, stopped,
downloaded, renamed, and deleted.

MP3 files can be uploaded, validated, played, downloaded, renamed, and deleted.
Playback uses a dedicated PipeWire mix bus and is sent to the headset and the
currently active microphone routes. Both features work independently of the
browser.

## Device assignment

Currently detected USB audio cards can be assigned to PC1, PC2, and
headset/microphone roles in the web interface. Assignments are stored
persistently and the audio graph is rebuilt from the selected PipeWire cards.

## Operation

- Web interface: `http://<raspberry-pi-address>:8765`
- User service: `haudio-control.service`
- Service user: `haudio`
- PipeWire runtime: `/run/user/<service-uid>`
- Configuration: `/etc/haudio/haudio.json`
- Logs: `journalctl --user -u haudio-control.service -f` in the service user's session

Audio processing runs independently of the browser. Healthy PipeWire routes
are preserved during backend restarts and only stale or missing links are
replaced. Recording processes are secondary and automatically recover after a
temporary device loss when recording was requested.

## Security and contribution

Read [SECURITY.md](SECURITY.md) before exposing the service beyond a trusted
LAN. Contributions and release hygiene are described in
[CONTRIBUTING.md](CONTRIBUTING.md).
