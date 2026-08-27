# hAudio reference setup

This document describes a generic reference deployment. It is not a live
inventory of a particular installation and intentionally contains no private
network addresses, usernames, or hardware-specific USB paths.

## Hardware roles

~~~text
USB audio interface A -> PC1
USB audio interface B -> PC2
USB headset adapter   -> headset/microphone
~~~

The concrete devices and physical ports are selected at runtime in the web
interface. Identical USB interfaces should be assigned by their stable
physical USB path, not by ALSA card number or changing USB device number.

## Services and endpoints

~~~text
haudio-control.service: enabled, active
Web interface/API:      0.0.0.0:8765
PipeWire quantum:       deployment-specific
Audio sample rate:      48000 Hz (default)
~~~

## Persistence

- Backend: `/opt/haudio/haudio_main.py`
- Frontend: `/opt/haudio/frontend/`
- Runtime state: `/var/lib/haudio/state.json`
- Recordings: `/data/haudio/recordings/`
- PipeWire configuration: `/home/haudio/.config/pipewire/pipewire.conf.d/`
- Service: `/etc/systemd/system/haudio-control.service`

## USB changes

The backend monitors PipeWire devices. Missing sources, sinks, or hAudio
loopbacks cause the graph to be rebuilt. USB capture gain is reset to a safe
level during recovery.

## Limitation

PC1 and PC2 assignment follows the physical USB paths or the assignments
selected in the web interface. Hardware-specific paths are deployment
specific and must not be copied into public documentation.
