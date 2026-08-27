# Install or restore hAudio

This procedure targets Raspberry Pi OS or Debian with PipeWire. It creates a
dedicated `haudio` account whose user manager owns PipeWire, WirePlumber, and
the hAudio service. This keeps every audio process in the same runtime session.

## 1. Install system packages

~~~bash
sudo apt update
sudo apt install pipewire pipewire-pulse wireplumber ffmpeg python3 python3-venv
~~~

Create the service account if it does not exist, then enable its persistent
user manager:

~~~bash
sudo useradd --system --create-home --groups audio haudio
sudo loginctl enable-linger haudio
HAUDIO_UID="$(id -u haudio)"
sudo systemctl start "user@${HAUDIO_UID}.service"
~~~

Do not run PipeWire as root. The user manager creates the required runtime at
`/run/user/<haudio-uid>` even when nobody is logged in.

## 2. Back up an existing installation

Skip files that do not exist yet.

~~~bash
sudo cp -a /opt/haudio /opt/haudio.bak
sudo cp -a /etc/haudio /etc/haudio.bak
sudo cp -a /home/haudio/.config/systemd/user/haudio-control.service \
  /home/haudio/.config/systemd/user/haudio-control.service.bak
~~~

Recordings and `/var/lib/haudio/state.json` are not replaced by the installation.

## 3. Install application and writable directories

Run these commands from the repository root:

~~~bash
sudo install -d -o haudio -g haudio /opt/haudio /opt/haudio/frontend
sudo install -d -o haudio -g haudio /opt/haudio/haudio
sudo install -d -o haudio -g haudio /var/lib/haudio
sudo install -d -o haudio -g haudio /data/haudio/recordings /data/haudio/soundboard
sudo install -d -m 755 /etc/haudio

sudo install -o haudio -g haudio -m 755 opt/haudio/haudio_main.py /opt/haudio/haudio_main.py
sudo install -o haudio -g haudio -m 644 opt/haudio/haudio/*.py /opt/haudio/haudio/
sudo install -o haudio -g haudio -m 644 opt/haudio/frontend/index.html /opt/haudio/frontend/index.html
sudo install -o haudio -g haudio -m 644 opt/haudio/frontend/app.js /opt/haudio/frontend/app.js
sudo install -o haudio -g haudio -m 644 opt/haudio/frontend/style.css /opt/haudio/frontend/style.css
sudo install -m 644 etc/haudio/haudio.json /etc/haudio/haudio.json
~~~

Create an isolated Python environment and install the tested dependency set:

~~~bash
sudo -u haudio python3 -m venv /opt/haudio/.venv
sudo -u haudio /opt/haudio/.venv/bin/pip install -r requirements-lock.txt
~~~

## 4. Install PipeWire and user-service configuration

~~~bash
sudo install -D -o haudio -g haudio -m 644 etc/pipewire/pipewire.conf.d/haudio.conf \
  /home/haudio/.config/pipewire/pipewire.conf.d/haudio.conf
sudo install -D -o haudio -g haudio -m 644 etc/systemd/user/haudio-control.service \
  /home/haudio/.config/systemd/user/haudio-control.service
~~~

Enable PipeWire and hAudio in the same user manager:

~~~bash
HAUDIO_UID="$(id -u haudio)"
sudo -u haudio env XDG_RUNTIME_DIR="/run/user/${HAUDIO_UID}" \
  systemctl --user daemon-reload
sudo -u haudio env XDG_RUNTIME_DIR="/run/user/${HAUDIO_UID}" \
  systemctl --user enable --now pipewire.service pipewire-pulse.service wireplumber.service
sudo -u haudio env XDG_RUNTIME_DIR="/run/user/${HAUDIO_UID}" \
  systemctl --user enable --now haudio-control.service
~~~

## 5. Verify

~~~bash
HAUDIO_UID="$(id -u haudio)"
sudo -u haudio env XDG_RUNTIME_DIR="/run/user/${HAUDIO_UID}" \
  systemctl --user status haudio-control.service --no-pager
sudo -u haudio env XDG_RUNTIME_DIR="/run/user/${HAUDIO_UID}" \
  pactl info
curl --fail http://127.0.0.1:8765/api/status
~~~

Open `http://<raspberry-pi-address>:8765` and assign one detected USB audio
interface to each required role. A single interface cannot be assigned to
multiple roles because that could create feedback loops.

## Existing custom installations

An existing installation may deliberately run the backend as a normal login
user. Keep that user and its working PipeWire session during an upgrade. Copy
the new application files and restart the existing service, but do not replace
its service identity with the generic `haudio` account without migrating the
PipeWire session as well.

See [CONFIGURATION.md](CONFIGURATION.md) for recording, storage, and path
settings and [OPERATIONS.md](OPERATIONS.md) for diagnostics.
