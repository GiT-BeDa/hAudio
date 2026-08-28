# Contributing to hAudio

Issues and pull requests are welcome. Please keep changes focused on stable,
low-latency audio operation and explain how the change was tested.

Before submitting a pull request, install `requirements-dev-tested.txt`, run
`npm ci`, and then:

1. Run `scripts/check-release.sh` for Python compilation, Ruff, mypy, pytest
   coverage, frontend DOM tests, checksum validation, and whitespace checks.
2. Run `npx playwright install chromium` once and `npm run test:e2e` when
   changing layout, controls, or browser behavior.
3. Check that documentation and the manifest describe new or removed files.
4. Update the `?v=` cache key in `frontend/index.html` when releasing a new
   version with changed CSS or JavaScript.
5. Run `scripts/update-checksums.sh` after changing installed files.
6. Search the complete diff for private IP addresses, usernames, credentials,
   recordings, SSH keys, and deployment-specific secrets.

Please do not commit generated caches, local configuration, credentials,
recordings, screenshots containing sensitive information, or private keys.
Use placeholders such as `<raspberry-pi-address>` in documentation.

For audio changes, include the tested sample rate, connected device types,
hotplug/reboot behavior, and any known latency or CPU trade-offs. Keep the
audio engine independent from the browser and treat recording failures as
non-fatal to live audio.

Tests use temporary state and media directories. New tests must not create
files under `/var/lib`, `/data`, or a contributor's home directory. The GitHub
Actions workflow runs Python checks on 3.11 through 3.13 and the frontend DOM
and responsive Chromium tests on Node.js 22.
