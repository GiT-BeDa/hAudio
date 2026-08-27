# Operation and diagnostics

The commands below assume the generic `haudio` service account. Existing
custom installations should substitute their actual PipeWire user.

~~~bash
HAUDIO_UID="$(id -u haudio)"
HAUDIO_RUN="/run/user/${HAUDIO_UID}"
~~~

## Service and logs

~~~bash
sudo -u haudio env XDG_RUNTIME_DIR="${HAUDIO_RUN}" \
  systemctl --user status haudio-control.service --no-pager -l
sudo -u haudio env XDG_RUNTIME_DIR="${HAUDIO_RUN}" \
  systemctl --user restart haudio-control.service
journalctl _SYSTEMD_USER_UNIT=haudio-control.service -f
~~~

The backend logs device reconciliation, routing changes, presets, volume and
mute changes, recording state, retention deletions, and failed subprocesses.

## PipeWire devices and graph

~~~bash
sudo -u haudio env XDG_RUNTIME_DIR="${HAUDIO_RUN}" pactl info
sudo -u haudio env XDG_RUNTIME_DIR="${HAUDIO_RUN}" pactl list short cards
sudo -u haudio env XDG_RUNTIME_DIR="${HAUDIO_RUN}" pactl list short sources
sudo -u haudio env XDG_RUNTIME_DIR="${HAUDIO_RUN}" pactl list short sinks
sudo -u haudio env XDG_RUNTIME_DIR="${HAUDIO_RUN}" pactl list short modules
cat /proc/asound/cards
for card in /sys/class/sound/card*; do echo "$card: $(readlink -f "$card/device")"; done
~~~

The web status footer distinguishes between PipeWire being reachable and the
hAudio graph being complete. An unassigned role is allowed; routes for other
assigned devices continue to be monitored and repaired.

The network footer shows the interface selected by the lowest-metric default
route as the primary connection. A simultaneously associated Wi-Fi interface
is shown as `SECONDARY`, not as the primary transport.

## USB reconnection

hAudio identifies USB audio cards by `device.bus_path`. Sources and sinks are
resolved from the current PipeWire objects, so profile-specific suffixes are
not assumed. On a reconnect, only stale or missing hAudio loopbacks are
replaced. Healthy routes remain active.

If a device was moved to another physical USB port, select its new path in the
appropriate web-interface card. Assigning one interface to multiple roles is
blocked to prevent accidental feedback.

## Recordings

Combined recordings contain headset output and microphone at the configured
Opus bitrate. The play button sends an existing recording directly to the
assigned headset; it does not use either computer output. While a file is
playing it cannot be renamed or deleted. Active recording segments cannot be
played, renamed, or deleted. If FFmpeg exits after temporary device loss,
hAudio retries while recording remains requested.

Retention runs hourly. Files older than `recording_max_age_days` are removed,
then the oldest files are removed as needed to maintain
`recording_min_free_gb` and `recording_max_disk_usage_percent`. Every deletion
is written to the journal.

## Audio faults and load

For USB transfer errors, XRUNs, or regular dropouts, inspect both kernel and
PipeWire logs:

~~~bash
journalctl -k --since "10 minutes ago" | grep -E 'usb|NYET|under.?voltage|xrun' -i
journalctl --user --since "10 minutes ago" | grep -E 'pipewire|wireplumber|broken pipe|xrun' -i
~~~

Increasing `loopback_latency_ms` or the PipeWire quantum can improve marginal
USB hardware at the cost of latency. Change one setting at a time and test all
routes before keeping it.

## Web interface

Open `http://<raspberry-pi-address>:8765`. The page reconnects its WebSocket
automatically and falls back to periodic HTTP status requests while live
updates are unavailable. API errors appear in a visible banner.
