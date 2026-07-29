# Screen brightness

The panel dims itself — at night, on a schedule, from quick settings. This is
how that actually reaches the screen.

There are two mechanisms, and they cooperate.

**Real backlight control** changes the display. The backlight drops, the panel
draws less power, and a dark room stays dark.

**The overlay** is a black wash painted over the window. It works on every
machine and fails on none, but it is a lie: the backlight is still at full and
in a dark room the difference between a dimmed screen and a dark one is
obvious.

`auto` tries the real thing and falls back to the wash.

---

## The four routes

Tried in this order, first one that answers wins.

| Route | Reaches | Needs |
|---|---|---|
| `sysfs` | Laptop panels, some others | Write access to `/sys/class/backlight/<dev>/brightness`, usually via a udev rule granting the `video` group. |
| `logind` | The same devices | `busctl`, and an **active seat session**. No root, no suid helper. |
| `brightnessctl`, `light` | Whatever they handle | The tool installed. |
| `ddcutil` | **External monitors**, over DDC/CI | The `i2c-dev` module, and read/write on `/dev/i2c-*`. |

**`brightnessctl` and `light` are gated on there actually being a backlight
device.** Left to choose, `brightnessctl` takes the first device it finds — and
on a desktop with no internal panel that is an LED: a keyboard backlight, a
capslock light. It then reports success while controlling something that is not
the screen, which is worse than reporting failure, because a survey says yes
and the display never changes. Both are pinned to the backlight class and
skipped entirely when `/sys/class/backlight` is empty.

`ddcutil` is last because it is the slowest and the least reliable — a write
is tens to hundreds of milliseconds and not every monitor answers — but it is
the one that matters for a wall panel, which is usually a monitor on the end
of an HDMI cable rather than a laptop screen.

`logind` is the route desktop environments are meant to use:
`org.freedesktop.login1.Session.SetBrightness`, arbitrated by the session
manager. It refuses unless the session has a seat, is in the foreground and
belongs to the caller — so it works for a panel logged in at its own screen
and not for one started from a service with no seat.

---

## Finding out what yours does

```
python3 hactl.py backlight --survey
```

```
  brightness : 12%
  driving    : DDC/CI (external monitor)  (display 1)
  setting    : auto

  internal panel : no internal backlight - external display

  available on this machine:
    yes  ddcutil  display 1
     no  brightnessctl
     no  light
     no  logind
     no  sysfs
```

The **internal panel** line is the one to read first. `no internal backlight`
means the display is external, so DDC/CI is the only route that can reach it -
and anything else answering yes would be controlling an LED.

Without `--survey` it reports only what was chosen, which is quicker. The same
thing is at `GET /backlight`.

---

## Getting it working

Run all of this **on the machine with the display**, not over `hactl`.

### Laptop panels

Usually nothing to do — either sysfs is already writable, or `logind` handles
it. If neither works, install `brightnessctl`, which has already solved the
permission problem:

```bash
sudo pacman -S brightnessctl     # Arch
sudo apt install brightnessctl   # Mint / Ubuntu / Debian
```

### External monitors (DDC/CI)

This is the usual case for a wall panel. **Confirmed working on Linux Mint and
on Arch** with the steps below.

**Linux Mint, Ubuntu, Debian**

```bash
sudo apt install ddcutil i2c-tools
sudo modprobe i2c-dev
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf   # survives a reboot
sudo usermod -aG i2c $USER
```

**Arch, CachyOS, EndeavourOS, Manjaro**

```bash
sudo pacman -S ddcutil i2c-tools
sudo modprobe i2c-dev
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf
```

Recent `ddcutil` ships `/usr/lib/udev/rules.d/60-ddcutil-i2c.rules`, which uses
the `uaccess` tag to grant the seated user read/write on the right
`/dev/i2c-*` devices, so the group step is often unnecessary. If it does not
work, or the machine uses eudev, fall back to the group as above.

**Then log out and back in.** Group membership does not apply to a session that
already exists. Restart the panel afterwards, and re-run the survey.

Nvidia's proprietary driver additionally needs
`/usr/share/ddcutil/data/90-nvidia-i2c.conf` copied into
`/etc/X11/xorg.conf.d/`.

### When the survey still says no

Work down this list. Each step tells you which of the four possible causes it
is, and they are in decreasing order of likelihood.

```bash
which ddcutil          # 1. installed at all?
lsmod | grep i2c_dev   # 2. module loaded?
ls /dev/i2c-*          # 3. buses exposed?
sudo ddcutil detect    # 4. does the monitor answer, as root?
ddcutil detect         # 5. and as you?
```

| What you see | What it means |
|---|---|
| `sudo` works, plain does not | **Permissions.** The `usermod` above fixes it; check with `groups` after logging back in. |
| No `/dev/i2c-*` | **Module.** `sudo modprobe i2c-dev` and look again. Some GPU drivers do not expose the buses until it is loaded. |
| Buses exist, `detect` finds nothing | Either the monitor does not implement DDC/CI, or **it is switched off in the monitor's own menu**. Look under Settings / Others / System for "DDC/CI" — it ships disabled on some Dell and BenQ models. |
| `detect` works but this program does not | Restart the panel. Backends are probed once at startup. |

### Checking it by hand

```bash
ddcutil detect            # does anything answer at all
ddcutil getvcp 10         # feature x10 is brightness
ddcutil setvcp 10 40      # and setting it
```

If `setvcp` changes the screen and the panel still does not, that is a bug
here rather than a setup problem — say so.

### If it genuinely cannot work

Set `application.backlight.mode` to `overlay` on that panel. It stops probing
`ddcutil` at every start, which takes a few seconds and will never succeed.

The overlay is a reasonable fallback for a room that is not pitch dark. What it
cannot do is reduce the actual light output, so on a bedroom wall at 3am it
will still glow.

---

## Settings

| Key | Default | Meaning |
|---|---|---|
| `application.backlight.mode` | `auto` | `auto`, `overlay` to never touch the hardware, or a route by name. |
| `application.backlight.device` | *(blank)* | A `/sys/class/backlight` name or a ddcutil display number. Blank picks the first that works. |
| `application.backlight.floor` | `0` | Below this the overlay takes over. |

### The floor

Plenty of monitors at brightness zero are still far too bright for a bedroom at
3am. Above the floor the hardware does all the work and the overlay is off;
below it the hardware holds at the floor and the wash makes up the rest.

Set it to the lowest hardware level that still looks lit — 15 is a reasonable
starting point — and the panel can then go darker than the monitor alone
allows. `0` means the hardware covers the whole range.

---

## For anyone changing this

**Nothing here may block the UI thread.** Every route shells out, and a
`ddcutil` write can take half a second. `BacklightController` runs one worker
thread and `set()` returns immediately.

**It coalesces rather than queues.** A fade steps thirty times in a second and
a half. Sending thirty DDC writes would take longer than the fade, and writes
are not free on every panel. The controller keeps a latest-wins slot with a
rate limit — the overlay does the smooth part, the hardware is told where it
ended up. In testing, a thirty-step fade produces **one** hardware write, and
it is the value the fade ended on.

**Zero is never written.** A backlight at raw 0 is a black screen with no
visible way back; one percent of the range is dim, not off.

Every subprocess call has a timeout and swallows its errors. A missing binary,
a hung `ddcutil`, a monitor that stops answering — all of them fall back to
the overlay rather than taking the panel down.
