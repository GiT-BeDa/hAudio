# Install or restore HAUDIO

These commands assume Raspberry Pi OS/Debian, the haudio user, and a
PipeWire PulseAudio-compatible interface.

## Hardware examples

The project does not require a specific manufacturer. Compatible USB audio
adapters and 3.5 mm cables may be used. Optional example links:

- [USB audio adapters](https://amzn.to/4xrTc1R)
- [3.5 mm AUX audio cables](https://amzn.to/4hZ3JfU)

These are affiliate links. The maintainer may receive a commission from a
qualifying purchase at no additional cost to you. Equivalent hardware works
as well. As an Amazon Associate I earn from qualifying purchases.

Install the Python dependencies before starting the service:

~~~bash
python3 -m pip install -r requirements.txt
~~~

## Copy files

~~~bash
install -d /opt/haudio
install -o haudio -g haudio -m 755 opt/haudio/haudio_main.py /opt/haudio/haudio_main.py
install -D -m 644 etc/systemd/system/haudio-control.service /etc/systemd/system/haudio-control.service
install -D -o haudio -g haudio -m 644 etc/pipewire/pipewire.conf.d/haudio.conf \
  /home/haudio/.config/pipewire/pipewire.conf.d/haudio.conf
~~~

Back up existing files first:

~~~bash
cp -a /opt/haudio/haudio_main.py /opt/haudio/haudio_main.py.bak
cp -a /etc/systemd/system/haudio-control.service /etc/systemd/system/haudio-control.service.bak
~~~

## Enable and verify

~~~bash
python3 -m py_compile /opt/haudio/haudio_main.py
systemctl daemon-reload
systemctl enable --now haudio-control.service
systemctl status haudio-control.service --no-pager
curl http://127.0.0.1:8765/api/status
runuser -u haudio -- pactl list cards
~~~

The application may need several seconds after startup for device
initialization.
