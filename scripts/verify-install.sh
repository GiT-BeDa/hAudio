#!/usr/bin/env bash
set -euo pipefail

HAUDIO_SERVICE_USER="${1:-${HAUDIO_USER:-haudio}}"
HAUDIO_SERVICE_UID="$(id -u "${HAUDIO_SERVICE_USER}")"

for HAUDIO_ATTEMPT in $(seq 1 30); do
  if curl --fail --silent --show-error http://127.0.0.1:8765/health/ready >/dev/null; then
    runuser -u "${HAUDIO_SERVICE_USER}" -- env XDG_RUNTIME_DIR="/run/user/${HAUDIO_SERVICE_UID}" pactl info >/dev/null
    echo "hAudio is ready."
    exit 0
  fi
  sleep 1
done

runuser -u "${HAUDIO_SERVICE_USER}" -- env XDG_RUNTIME_DIR="/run/user/${HAUDIO_SERVICE_UID}" \
  systemctl --user status haudio-control.service --no-pager -l || true
echo "hAudio did not become ready within 30 seconds." >&2
exit 1
