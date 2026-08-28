#!/usr/bin/env bash
set -euo pipefail

HAUDIO_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HAUDIO_SERVICE_USER="${HAUDIO_USER:-haudio}"
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this updater as root." >&2
  exit 1
fi
if ! id "${HAUDIO_SERVICE_USER}" >/dev/null 2>&1; then
  echo "Service account ${HAUDIO_SERVICE_USER} does not exist; run scripts/install.sh first." >&2
  exit 1
fi
HAUDIO_SERVICE_UID="$(id -u "${HAUDIO_SERVICE_USER}")"
HAUDIO_SERVICE_HOME="$(getent passwd "${HAUDIO_SERVICE_USER}" | cut -d: -f6)"
HAUDIO_BACKUP_ROOT="/opt/haudio.backup-$(date +%Y%m%d-%H%M%S)"

if [[ ! -x /opt/haudio/.venv/bin/python ]]; then
  echo "No generic hAudio installation found; run scripts/install.sh first." >&2
  exit 1
fi

cp -a /opt/haudio "${HAUDIO_BACKUP_ROOT}"
HAUDIO_UNIT_PATH="${HAUDIO_SERVICE_HOME}/.config/systemd/user/haudio-control.service"
if [[ -e "${HAUDIO_UNIT_PATH}" ]]; then
  cp -a "${HAUDIO_UNIT_PATH}" "${HAUDIO_UNIT_PATH}.bak-$(date +%Y%m%d-%H%M%S)"
fi
HAUDIO_PIPEWIRE_PATH="${HAUDIO_SERVICE_HOME}/.config/pipewire/pipewire.conf.d/haudio.conf"
if ! cmp --silent "${HAUDIO_PROJECT_ROOT}/etc/pipewire/pipewire.conf.d/haudio.conf" "${HAUDIO_PIPEWIRE_PATH}"; then
  if [[ -e "${HAUDIO_PIPEWIRE_PATH}" ]]; then
    cp -a "${HAUDIO_PIPEWIRE_PATH}" "${HAUDIO_PIPEWIRE_PATH}.bak-$(date +%Y%m%d-%H%M%S)"
  fi
  install -D -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 644 \
    "${HAUDIO_PROJECT_ROOT}/etc/pipewire/pipewire.conf.d/haudio.conf" "${HAUDIO_PIPEWIRE_PATH}"
  echo "PipeWire configuration updated; it will take effect after the next PipeWire restart or reboot."
fi
install -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 755 \
  "${HAUDIO_PROJECT_ROOT}/opt/haudio/haudio_main.py" /opt/haudio/haudio_main.py
install -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 644 \
  "${HAUDIO_PROJECT_ROOT}"/opt/haudio/haudio/*.py /opt/haudio/haudio/
install -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 644 \
  "${HAUDIO_PROJECT_ROOT}"/opt/haudio/frontend/* /opt/haudio/frontend/
install -o "${HAUDIO_SERVICE_USER}" -g "${HAUDIO_SERVICE_USER}" -m 644 \
  "${HAUDIO_PROJECT_ROOT}/etc/systemd/user/haudio-control.service" \
  "${HAUDIO_SERVICE_HOME}/.config/systemd/user/haudio-control.service"
runuser -u "${HAUDIO_SERVICE_USER}" -- /opt/haudio/.venv/bin/pip install \
  -r "${HAUDIO_PROJECT_ROOT}/requirements-tested.txt"

runuser -u "${HAUDIO_SERVICE_USER}" -- env XDG_RUNTIME_DIR="/run/user/${HAUDIO_SERVICE_UID}" \
  systemctl --user daemon-reload
runuser -u "${HAUDIO_SERVICE_USER}" -- env XDG_RUNTIME_DIR="/run/user/${HAUDIO_SERVICE_UID}" \
  systemctl --user restart haudio-control.service

if ! "${HAUDIO_PROJECT_ROOT}/scripts/verify-install.sh" "${HAUDIO_SERVICE_USER}"; then
  echo "Update verification failed. Backup: ${HAUDIO_BACKUP_ROOT}" >&2
  exit 1
fi
echo "Update verified. Backup: ${HAUDIO_BACKUP_ROOT}"
