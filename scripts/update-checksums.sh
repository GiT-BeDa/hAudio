#!/usr/bin/env bash
set -euo pipefail

HAUDIO_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${HAUDIO_PROJECT_ROOT}"

HAUDIO_CHECKSUM_FILES=(
  opt/haudio/haudio_main.py
  opt/haudio/haudio/__init__.py
  opt/haudio/haudio/app.py
  opt/haudio/haudio/audio.py
  opt/haudio/haudio/config.py
  opt/haudio/haudio/media.py
  opt/haudio/haudio/state.py
  opt/haudio/frontend/index.html
  opt/haudio/frontend/app.js
  opt/haudio/frontend/style.css
  etc/haudio/haudio.json
  etc/systemd/user/haudio-control.service
)
sha256sum "${HAUDIO_CHECKSUM_FILES[@]}" > SHA256SUMS
