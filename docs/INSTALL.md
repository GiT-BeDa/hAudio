# Install or restore hAudio

These commands assume Raspberry Pi OS/Debian, a dedicated `haudio` service
user, and a PipeWire PulseAudio-compatible interface.

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
sudo apt update
sudo apt install pipewire pipewire-pulse wireplumber ffmpeg python3 python3-pip
python3 -m pip install -r requirements.txt
~~~

The service unit runs as the unprivileged `haudio` user. Create that user and
grant it access to the audio devices before enabling the service, if it does
not already exist:

~~~bash
sudo useradd --system --create-home --groups audio,video haudio
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
initialization. Replace `<raspberry-pi-address>` with the Pi's current LAN
address when opening the web interface: `http://<raspberry-pi-address>:8765`.
