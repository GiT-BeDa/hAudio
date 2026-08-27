# Contributing to hAudio

Issues and pull requests are welcome. Please keep changes focused on stable,
low-latency audio operation and explain how the change was tested.

Before submitting a pull request:

1. Run `python3 -m py_compile opt/haudio/haudio_main.py`.
2. Run `pytest -q`.
3. Run `node --check opt/haudio/frontend/app.js` and
   `node --test tests/frontend.test.js` when changing the frontend.
4. Check that documentation and the manifest describe new or removed files.
5. Update the `?v=` cache key in `frontend/index.html` when releasing a new
   version with changed CSS or JavaScript.
6. Run `sha256sum -c SHA256SUMS` and update checksums for changed installed files.
7. Search the complete diff for private IP addresses, usernames, credentials,
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
Actions workflow runs the same compilation, JavaScript DOM, and pytest checks
on Python 3.11 through 3.13.
