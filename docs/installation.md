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


## Optional: real screen dimming

The panel dims itself at night and from quick settings. Out of the box that is
a dark overlay painted over the window, which works everywhere and changes
nothing about the actual backlight.

> **Real brightness control is Linux only.** Every route below - sysfs, logind,
> ddcutil, brightnessctl - is Linux. On Windows and macOS the overlay is all
> there is, and everything else in the panel works normally.

On Linux there is usually one package to install:

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

## A USB touchscreen that stops responding

A touchscreen that only fails after the machine has been idle for a long time,
and comes back when you unplug and replug it, is **USB autosuspend**: the
kernel powers the device down after inactivity and it does not wake up. Sleep
and the screensaver are unrelated.

This is **Linux only**, and it was found on one specific setup: a portable
touchscreen taking its picture over HDMI-to-HDMI-mini and both its data and its
power over a single USB cable. A screen powered from the wall, or one plugged
into a machine that never idles, may never show it.

### Find the device

A touchscreen reports absolute coordinates, which a mouse does not - so ask the
kernel for that rather than matching a product name:

```bash
# every input device, with the capabilities it reports
sudo libinput list-devices | grep -A2 -i touch

# or straight from the kernel
grep -B4 'ABS=.*[1-9]' /proc/bus/input/devices | grep -i 'Name='
```

Then find its USB entry. `lsusb -t` shows the tree, which matters - the **hub**
suspends too, and a suspended hub takes everything on it down no matter what
the device's own setting says:

```bash
lsusb                 # vendor:product ids
lsusb -t              # the tree, so you can see which hub it is behind
```

### Check whether autosuspend is on

```bash
# every USB device's power control, with the product name beside it
for d in /sys/bus/usb/devices/*/; do
  [ -f "$d/product" ] || continue
  printf '%-40s %s\n' "$(cat "$d/product")" "$(cat "$d/power/control" 2>/dev/null)"
done
```

`auto` means the kernel may power it down after inactivity. `on` means it may
not.

### Turn it off for this boot

Writes to sysfs only, so a reboot undoes it. That is what makes it safe to try
first - if the screen then survives an idle it would normally not, the cause is
confirmed:

```bash
# replace with the path from the listing above, and do the HUB as well
echo on | sudo tee /sys/bus/usb/devices/1-2/power/control
echo on | sudo tee /sys/bus/usb/devices/1-2/power/autosuspend_delay_ms
```

### Make it permanent

A udev rule, matched on the vendor and product ids from `lsusb`:

```bash
sudo tee /etc/udev/rules.d/50-touchscreen-nosuspend.rules <<'EOF'
# Replace 0eef/0005 with your own ids from lsusb.
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0eef", ATTR{idProduct}=="0005", TEST=="power/control", ATTR{power/control}="on"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
```

Confirm it took by re-running the listing above; the device should now read
`on`.
