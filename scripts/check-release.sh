#!/usr/bin/env bash
set -euo pipefail

HAUDIO_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${HAUDIO_PROJECT_ROOT}"

python3 -m compileall -q opt/haudio
python3 -m ruff check opt tests
python3 -m mypy opt/haudio/haudio
pytest --cov=opt/haudio/haudio --cov-report=term-missing --cov-fail-under=55 -q
node --check opt/haudio/frontend/app.js
node --test tests/frontend.test.js
sha256sum -c SHA256SUMS
git diff --check
