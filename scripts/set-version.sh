#!/usr/bin/env bash
set -euo pipefail

HAUDIO_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HAUDIO_VERSION="${1:-}"
HAUDIO_DATE="$(date +%Y-%m-%d)"

if [[ ! "${HAUDIO_VERSION}" =~ ^[0-9]+\.[0-9]+([.][0-9]+)?$ ]]; then
  echo "Usage: $0 <numeric-version>" >&2
  exit 1
fi
cd "${HAUDIO_PROJECT_ROOT}"

printf '%s\n' "${HAUDIO_VERSION}" > VERSION
sed -i -E "s/__version__ = \"[^\"]+\"/__version__ = \"${HAUDIO_VERSION}\"/" opt/haudio/haudio/__init__.py
sed -i -E "s/<title>hAudio [^<]+<\//<title>hAudio ${HAUDIO_VERSION}<\//" opt/haudio/frontend/index.html
sed -i -E "s#(static/(app.js|style.css)\?v=)[^\"]+#\1${HAUDIO_VERSION}#g" opt/haudio/frontend/index.html
sed -i -E "1s/^# hAudio [^ ]+/# hAudio ${HAUDIO_VERSION}/" README.md
sed -i -E "s#version-[^-]+-22c55e#version-${HAUDIO_VERSION}-22c55e#" README.md
sed -i -E "s/^- Updated: .*/- Updated: ${HAUDIO_DATE}/" README.md
sed -i -E "1s/hAudio [^ ]+ source/hAudio ${HAUDIO_VERSION} source/" MANIFEST.txt
sed -i -E "1s/updated [0-9-]+/updated ${HAUDIO_DATE}/" MANIFEST.txt
sed -i -E "1s/(hAudio )[0-9]+(\.[0-9]+)+/\1${HAUDIO_VERSION}/" requirements-tested.txt requirements-dev-tested.txt
"${HAUDIO_PROJECT_ROOT}/scripts/update-checksums.sh"
