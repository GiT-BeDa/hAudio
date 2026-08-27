# hAudio current system state

Recorded on 2026-08-27 on the reference Raspberry Pi.

## Hardware

~~~text
USB audio interface A -> PC1
USB audio interface B -> PC2
USB headset adapter   -> headset/microphone
~~~

The concrete device models and physical ports are intentionally not part of
the generic project documentation. They are selected at runtime in the web
interface.

## Services and endpoints

~~~text
haudio-control.service: enabled, active
Web interface/API:      0.0.0.0:8765
PipeWire quantum:       2048
Audio sample rate:      48000 Hz
~~~

## Persistence

- Backend: /opt/haudio/haudio_main.py
- Runtime state: /var/lib/haudio/state.json
- Recordings: /data/haudio/recordings/
- PipeWire: /home/haudio/.config/pipewire/pipewire.conf.d/haudio.conf
- Service: /etc/systemd/system/haudio-control.service
- Undervoltage warning display: avoid_warnings=1 in /boot/firmware/config.txt

## USB changes

The backend device monitor checks PipeWire devices regularly. Missing sources,
sinks, or hAudio loopbacks cause the graph to be rebuilt. USB capture gain is
reset to a safe level during recovery.

## Limitation

PC1/PC2 assignment follows the physical USB paths or the assignments selected
in the web interface. Hardware-specific paths are deployment-specific and are
not part of this generic documentation.
