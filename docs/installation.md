# Installation

## Clone the repository

```bash
git clone https://github.com/FacehuggersInc/HomeAssistant.git

cd HomeAssistant
```

## Create a virtual environment

### Windows

```bash
python -m venv .venv

.venv\Scripts\activate
```

### Linux

```bash
python3 -m venv .venv

source .venv/bin/activate
```

## Install dependencies

```bash
pip install -r requirements.txt
```

## Run the application

```bash
python app.py
```

That runs the client directly, which is fine for development.

For anything long-running -- a wall mounted display, a kiosk, an autostart
entry -- start it through the launcher instead, so crashes and updates are
handled for you:

```bash
./startup.sh          # Linux / macOS
startup.bat           # Windows
```

Both scripts do the same two things: activate the virtualenv and hand off to
`launcher.py`. All the actual supervision logic lives in `launcher.py`, so it
behaves identically on both platforms.

---


## Optional: real screen dimming

The panel dims itself at night and from quick settings. Out of the box that is
a dark overlay painted over the window, which works everywhere and changes
nothing about the actual backlight.

For **real** brightness control there is usually one package to install:

```bash
# an external monitor - the usual case for a wall panel
sudo apt install ddcutil i2c-tools     # Mint / Ubuntu / Debian
sudo pacman -S ddcutil i2c-tools       # Arch / CachyOS
sudo modprobe i2c-dev
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf
sudo usermod -aG i2c $USER             # then log out and back in

# a laptop panel, if sysfs and logind are not already handling it
sudo apt install brightnessctl
```

Restart the panel, then check what it picked up:

```bash
python3 hactl.py backlight --survey
```

[Screen brightness](backlight.md) covers every route, what each one needs, and
what to do when the survey still says no.
