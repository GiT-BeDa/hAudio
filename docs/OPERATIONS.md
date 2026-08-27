# Operation and diagnostics

## Service

~~~bash
systemctl restart haudio-control.service
systemctl status haudio-control.service --no-pager -l
journalctl -u haudio-control.service -f
~~~

## PipeWire devices

~~~bash
runuser -u haudio -- pactl list short sources
runuser -u haudio -- pactl list short sinks
cat /proc/asound/cards
for x in /sys/class/sound/card*; do echo "$x: $(readlink -f "$x/device")"; done
~~~

## Clipping

USB capture gain should be set to a safe level, normally 0 dB:

~~~bash
amixer -c1 sget Mic
amixer -c2 sget Mic
~~~

## USB reconnection

hAudio continuously checks PipeWire devices. If devices or hAudio loopbacks
disappear, the graph is rebuilt. When devices return, USB cards are assigned
again using their physical paths. The web interface shows missing devices as
disconnected.

## Recordings and web interface

Recordings are started under “Recordings”. Each file is a segmented Opus file
containing headset output and microphone. A recording failure must not stop
audio routing; details are written to the journal.

The web interface is available at `http://<raspberry-pi-address>:8765`. Its layout is:

1. PC1 and PC2
2. Headset and microphone
3. Recordings and soundboard
4. System status in the footer

MP3 files are stored in `/data/haudio/soundboard/`. Playback is sent to the
headset and non-muted computer outputs. Starting a new file stops the previous
one.
