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
independently to PC1, PC2, both, or neither computer. The browser-independent
backend also provides combined Opus recording, persistent presets, live status,
and an MP3 soundboard.

## Web interface

The responsive web interface provides independent computer volume and mute
controls, microphone routing, live level meters, recording management,
hardware assignment, editable persistent presets, and a soundboard. `MUTE ALL`
restores the previous mute/routing state when pressed a second time.

![hAudio web interface](docs/images/WebInterface.png)

The screenshot shows the current interface from an example deployment. Device
names and assignments vary with the connected hardware. Its private LAN address
has been replaced with the documentation-only example address `192.0.2.10`.

Interactive API documentation is available on a running system at
`http://<raspberry-pi-address>:8765/docs`; the complete endpoint reference is
available in [docs/API.md](docs/API.md).
The process and failure-boundary design is described in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick installation

On Raspberry Pi OS or Debian, first download the source:

~~~bash
sudo apt update
sudo apt install git
git clone https://github.com/GiT-BeDa/hAudio.git
cd hAudio
~~~

Then follow [docs/INSTALL.md](docs/INSTALL.md) to install the system packages,
create the service account, configure PipeWire, install hAudio, and verify the
service. Do not run PipeWire as root or place the backend in a different runtime
session from PipeWire.

Open the web interface at `http://<raspberry-pi-address>:8765`. See
`docs/INSTALL.md` for backups, verification, and recovery details.

## Signal paths

~~~text
PC1 -> USB audio interface A --------------┐
                                           ├-> PipeWire mix -> USB headset adapter -> headset
PC2 -> USB audio interface B --------------┘

Headset microphone -> USB headset adapter -> PipeWire -> PC1 and/or PC2
~~~

## Recording and soundboard

The web interface provides one combined recording containing headset output and
headset microphone, stored as segmented Opus files under
`/data/haudio/recordings/YYYY-MM-DD/`. Recordings can be started, stopped,
downloaded, renamed, and deleted.

MP3 files can be uploaded, validated, played, downloaded, renamed, and deleted.
Playback uses a dedicated PipeWire mix bus and is sent to the headset and the
currently active microphone routes. Both features work independently of the
browser.

## Device assignment

Currently detected USB audio cards can be assigned to PC1, PC2, and
headset/microphone roles in the web interface. Assignments are stored
persistently and the audio graph is rebuilt from the selected PipeWire cards.

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

## Operation

- Web interface: `http://<raspberry-pi-address>:8765`
- Interactive API: `http://<raspberry-pi-address>:8765/docs`
- User service: `haudio-control.service`
- Service user: `haudio`
- PipeWire runtime: `/run/user/<service-uid>`
- Configuration: `/etc/haudio/haudio.json`
- Logs: `journalctl _SYSTEMD_USER_UNIT=haudio-control.service -f`

Audio processing runs independently of the browser. Healthy PipeWire routes
are preserved during backend restarts and only stale or missing links are
replaced. Recording processes are secondary and automatically recover after a
temporary device loss when recording was requested.

## Development and tests

To reproduce the direct dependency versions used by CI, install
`requirements-dev-tested.txt`. Use `requirements-dev.txt` when intentionally
testing newer compatible releases. Then run:

~~~bash
python3 -m pip install -r requirements-dev-tested.txt
python3 -m py_compile opt/haudio/haudio_main.py
python3 -m compileall -q opt/haudio/haudio
node --check opt/haudio/frontend/app.js
node --test tests/frontend.test.js
pytest -q
~~~

The tests cover atomic persistence, actual PipeWire node discovery, partial
graphs, microphone and soundboard routing, API route behavior, duplicate
assignment protection, and live DOM updates in the static frontend.

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
- requirements-tested.txt and requirements-dev-tested.txt – tested direct constraints
- tests/ – automated backend and frontend regression tests

Runtime state, recordings, credentials, and private SSH keys are not included.

## Related projects

If you like hAudio, check also [deskhop](https://github.com/hrvach/deskhop),
an open-source project for sharing keyboard, mouse, and display control.

## Security and contribution

Read [SECURITY.md](SECURITY.md) before exposing the service beyond a trusted
LAN. Contributions and release hygiene are described in
[CONTRIBUTING.md](CONTRIBUTING.md).
