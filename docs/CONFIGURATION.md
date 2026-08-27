# hAudio configuration

The default configuration file is `/etc/haudio/haudio.json`. The repository
contains a safe example at `etc/haudio/haudio.json`.

| Setting | Default | Purpose |
| --- | ---: | --- |
| `sample_rate` | `48000` | Audio and recording sample rate. |
| `loopback_latency_ms` | `10` | Requested PipeWire-Pulse loopback latency. Increase if hardware is unstable. |
| `recording_bitrate` | `"128k"` | Opus bitrate for combined recordings. |
| `recording_segment_seconds` | `3600` | Maximum duration of one recording segment. |
| `recording_max_age_days` | `30` | Age-based deletion; use `0` to disable. |
| `recording_max_disk_usage_percent` | `90.0` | Delete oldest recordings above this filesystem usage; use `0` to disable. |
| `recording_min_free_gb` | `5.0` | Delete oldest recordings when free space falls below this value. |
| `soundboard_max_bytes` | `209715200` | Maximum uploaded MP3 size. |

Advanced environment-only settings include `HAUDIO_STATUS_INTERVAL_SECONDS`
for expensive system/audio health refreshes and
`HAUDIO_WEBSOCKET_INTERVAL_SECONDS` for lightweight live meter updates.
Set `HAUDIO_LOG_LEVEL` to `DEBUG`, `INFO`, `WARNING`, or `ERROR` to change
journal verbosity; the default is `INFO`.

Paths can also be configured, primarily for development and tests:

~~~json
{
  "state_dir": "/var/lib/haudio",
  "recording_dir": "/data/haudio/recordings",
  "soundboard_dir": "/data/haudio/soundboard"
}
~~~

Set `HAUDIO_CONFIG` to load a different JSON file. Individual values can be
overridden with environment variables such as `HAUDIO_RECORDING_BITRATE`,
`HAUDIO_RECORDING_MIN_FREE_GB`, and `HAUDIO_LOOPBACK_LATENCY_MS`.

Unknown configuration keys stop startup with an explicit error instead of
being silently ignored. Restart `haudio-control.service` after changing the
configuration.

## Editable presets

Set the desired volumes, mute states, and microphone routes in the web
interface. Select `NORMAL`, `PC1 ONLY`, `PC2 ONLY`, or `MEETING` next to “Save
current as” and press `SAVE CURRENT`. The complete mix is stored under
`presets` in `/var/lib/haudio/state.json` and survives service restarts and
reboots.

`MUTE ALL` is intentionally not saved as a normal preset. It temporarily saves
the current mute/routing state and changes its label to `RESTORE AUDIO`. Press
it again to restore that saved state.
