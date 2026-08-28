#!/usr/bin/env bash
set -euo pipefail

HAUDIO_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HAUDIO_SERVICE_USER="${HAUDIO_USER:-haudio}"
HAUDIO_BACKUP_SUFFIX="$(date +%Y%m%d-%H%M%S)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

apt-get update
apt-get install -y \
  alsa-utils curl dbus-user-session ffmpeg pipewire pipewire-pulse \
  pulseaudio-utils python3 python3-venv usbutils wireplumber

if ! id "${HAUDIO_SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --groups audio --shell /usr/sbin/nologin "${HAUDIO_SERVICE_USER}"
fi
usermod --append --groups audio "${HAUDIO_SERVICE_USER}"
HAUDIO_SERVICE_UID="$(id -u "${HAUDIO_SERVICE_USER}")"
HAUDIO_SERVICE_HOME="$(getent passwd "${HAUDIO_SERVICE_USER}" | cut -d: -f6)"

loginctl enable-linger "${HAUDIO_SERVICE_USER}"
systemctl start "user@${HAUDIO_SERVICE_UID}.service"

for HAUDIO_PATH in /opt/haudio /etc/haudio; do
  if [[ -e "${HAUDIO_PATH}" ]]; then
    cp -a "${HAUDIO_PATH}" "${HAUDIO_PATH}.bak-${HAUDIO_BACKUP_SUFFIX}"
  fi
done

install -d -m 750 -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" \
  /opt/haudio /opt/haudio/haudio /opt/haudio/frontend \
  /var/lib/haudio /data/haudio/recordings /data/haudio/soundboard
install -d -m 755 /etc/haudio
install -d -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" \
  "${HAUDIO_SERVICE_HOME}/.config/pipewire/pipewire.conf.d" \
  "${HAUDIO_SERVICE_HOME}/.config/systemd/user"

for HAUDIO_USER_FILE in \
  "${HAUDIO_SERVICE_HOME}/.config/pipewire/pipewire.conf.d/haudio.conf" \
  "${HAUDIO_SERVICE_HOME}/.config/systemd/user/haudio-control.service"; do
  if [[ -e "${HAUDIO_USER_FILE}" ]]; then
    cp -a "${HAUDIO_USER_FILE}" "${HAUDIO_USER_FILE}.bak-${HAUDIO_BACKUP_SUFFIX}"
  fi
done

install -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 755 \
  "${HAUDIO_PROJECT_ROOT}/opt/haudio/haudio_main.py" /opt/haudio/haudio_main.py
install -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 644 \
  "${HAUDIO_PROJECT_ROOT}"/opt/haudio/haudio/*.py /opt/haudio/haudio/
install -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 644 \
  "${HAUDIO_PROJECT_ROOT}"/opt/haudio/frontend/* /opt/haudio/frontend/
if [[ ! -e /etc/haudio/haudio.json ]]; then
  install -m 644 "${HAUDIO_PROJECT_ROOT}/etc/haudio/haudio.json" /etc/haudio/haudio.json
else
  echo "Preserving existing /etc/haudio/haudio.json"
fi
install -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 644 \
  "${HAUDIO_PROJECT_ROOT}/etc/pipewire/pipewire.conf.d/haudio.conf" \
  "${HAUDIO_SERVICE_HOME}/.config/pipewire/pipewire.conf.d/haudio.conf"
install -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 644 \
  "${HAUDIO_PROJECT_ROOT}/etc/systemd/user/haudio-control.service" \
  "${HAUDIO_SERVICE_HOME}/.config/systemd/user/haudio-control.service"

if [[ ! -e /etc/haudio/haudio.env ]]; then
  install -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 600 /dev/null /etc/haudio/haudio.env
fi

if [[ ! -x /opt/haudio/.venv/bin/python ]]; then
  runuser -u "${HAUDIO_SERVICE_USER}" -- python3 -m venv /opt/haudio/.venv
fi
runuser -u "${HAUDIO_SERVICE_USER}" -- /opt/haudio/.venv/bin/pip install --upgrade pip
runuser -u "${HAUDIO_SERVICE_USER}" -- /opt/haudio/.venv/bin/pip install \
  -r "${HAUDIO_PROJECT_ROOT}/requirements-tested.txt"

runuser -u "${HAUDIO_SERVICE_USER}" -- env XDG_RUNTIME_DIR="/run/user/${HAUDIO_SERVICE_UID}" \
  systemctl --user daemon-reload
runuser -u "${HAUDIO_SERVICE_USER}" -- env XDG_RUNTIME_DIR="/run/user/${HAUDIO_SERVICE_UID}" \
  systemctl --user enable --now pipewire.service pipewire-pulse.service wireplumber.service haudio-control.service

"${HAUDIO_PROJECT_ROOT}/scripts/verify-install.sh" "${HAUDIO_SERVICE_USER}"
