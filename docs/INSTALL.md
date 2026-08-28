# Install or restore hAudio

This procedure targets Raspberry Pi OS or Debian with Python 3.11 through 3.13
and PipeWire. It creates a dedicated `haudio` account whose user manager owns
PipeWire, WirePlumber, and the hAudio service. This keeps every audio process in
the same runtime session.

## 1. Download hAudio

~~~bash
sudo apt update
sudo apt install git
git clone https://github.com/GiT-BeDa/hAudio.git
cd hAudio
~~~

All remaining commands that reference repository files must be run from this
`hAudio` directory. A downloaded source archive can be used instead; extract it
and change into its root directory first.

## Automated installation

For a standard new installation, use:

~~~bash
sudo scripts/install.sh
~~~

The script installs required packages, creates the dedicated account, backs up
existing application/configuration paths, installs the tested Python
dependencies, enables the user services, and waits up to 30 seconds for audio
readiness. Runtime state and recordings are preserved.

Use `sudo scripts/update.sh` for later generic updates. Set `HAUDIO_USER` only
when intentionally targeting a different existing service account. The manual
steps below document every operation and remain the recovery procedure.

## 2. Install system packages

~~~bash
sudo apt install alsa-utils curl dbus-user-session ffmpeg pipewire \
  pipewire-pulse pulseaudio-utils python3 python3-venv usbutils wireplumber
~~~

Create the service account if it does not exist, then enable its persistent
user manager:

~~~bash
if ! id -u haudio >/dev/null 2>&1; then
  sudo useradd --system --create-home --groups audio haudio
fi
sudo usermod --append --groups audio haudio
sudo loginctl enable-linger haudio
HAUDIO_UID="$(id -u haudio)"
sudo systemctl start "user@${HAUDIO_UID}.service"
~~~

Do not run PipeWire as root. The user manager creates the required runtime at
`/run/user/<haudio-uid>` even when nobody is logged in.

## 3. Back up an existing installation

Existing paths are copied to timestamped backups. Missing paths are skipped.

~~~bash
HAUDIO_BACKUP_SUFFIX="$(date +%Y%m%d-%H%M%S)"
sudo test ! -e /opt/haudio || sudo cp -a /opt/haudio "/opt/haudio.bak-${HAUDIO_BACKUP_SUFFIX}"
sudo test ! -e /etc/haudio || sudo cp -a /etc/haudio "/etc/haudio.bak-${HAUDIO_BACKUP_SUFFIX}"
HAUDIO_SERVICE_FILE=/home/haudio/.config/systemd/user/haudio-control.service
sudo test ! -e "$HAUDIO_SERVICE_FILE" || sudo cp -a "$HAUDIO_SERVICE_FILE" \
  "${HAUDIO_SERVICE_FILE}.bak-${HAUDIO_BACKUP_SUFFIX}"
~~~

Recordings and `/var/lib/haudio/state.json` are not replaced by the installation.

## 4. Install application and writable directories

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
sudo test -e /etc/haudio/haudio.json || \
  sudo install -m 644 etc/haudio/haudio.json /etc/haudio/haudio.json
~~~

Create an isolated Python environment and install the tested direct dependency
constraints. Pip resolves compatible transitive dependencies for the Pi's
Python version and platform:

~~~bash
sudo -u haudio python3 -m venv /opt/haudio/.venv
sudo -u haudio /opt/haudio/.venv/bin/pip install -r requirements-tested.txt
~~~

## 5. Install PipeWire and user-service configuration

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

## 6. Verify

~~~bash
HAUDIO_UID="$(id -u haudio)"
sudo -u haudio env XDG_RUNTIME_DIR="/run/user/${HAUDIO_UID}" \
  systemctl --user status haudio-control.service --no-pager
sudo -u haudio env XDG_RUNTIME_DIR="/run/user/${HAUDIO_UID}" \
  pactl info
curl --fail http://127.0.0.1:8765/api/status
curl --fail http://127.0.0.1:8765/health/ready
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
