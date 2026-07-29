"""
Real display brightness, where the machine will allow it.

The overlay dimmer is a black wash over the window. It works everywhere and
costs nothing to run, but it is a lie: the backlight is still at full, the
panel still draws the same power, and in a dark room the difference between a
dimmed screen and a dark one is obvious.

This tries the real thing first, and keeps the overlay as the fallback and as
the way to go darker than the hardware can.

No new dependencies. Everything here shells out to tools a distribution either
ships or packages, because the alternative is a D-Bus binding, a udev binding
and an I2C binding for one slider.

Four routes, in the order they are tried:

===================  ==========================================================
sysfs                Writing /sys/class/backlight/<dev>/brightness directly.
                     Works when a udev rule has granted the video group, which
                     several distributions do. Instant and dependency-free.
logind               org.freedesktop.login1.Session.SetBrightness over D-Bus.
                     The route desktop environments are meant to use: no root,
                     no suid helper, arbitrated by the session manager. Needs
                     an active seat session, so it fails on a headless build
                     or from a service with no seat.
ddcutil              DDC/CI over I2C, for external monitors - which a wall
                     panel usually is. Needs the i2c-dev module and read/write
                     on /dev/i2c-*; modern ddcutil ships a udev rule using the
                     uaccess tag which grants exactly that to the seated user.
                     Slow (tens to hundreds of ms) and not every monitor
                     answers, so it is tried last of the real ones.
brightnessctl/light  Small wrappers that already solved the permission problem
                     on their own. Used if present, since somebody installing
                     one has already decided how this should work.
===================  ==========================================================
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

#nothing here may hang the caller
CALL_TIMEOUT = 4.0
#DDC in particular is slow, and writes are not free on every monitor
MIN_INTERVAL = 0.25

SYSFS_ROOT = Path("/sys/class/backlight")


def backlight_devices() -> list:
    """
    Names under /sys/class/backlight.

    Empty means the machine has no internal panel the kernel knows about -
    a desktop with an external monitor - and every route except DDC/CI is
    therefore reaching something other than the screen.
    """
    try:
        return sorted(p.name for p in SYSFS_ROOT.iterdir() if p.is_dir())
    except OSError:
        return []


def _run(args: list, timeout: float = CALL_TIMEOUT) -> tuple:
    """(ok, stdout). Never raises, never hangs."""
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return done.returncode == 0, (done.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return False, ""


class Backend:
    """One way of setting the brightness."""

    name = "none"
    label = "None"
    #whether a change is cheap enough to send on every animation step
    fast = True

    def detect(self) -> bool:
        return False

    def set(self, percent: int) -> bool:
        return False

    def get(self) -> Optional[int]:
        return None

    def detail(self) -> str:
        return ""


class SysfsBackend(Backend):
    """
    Write the raw value straight into sysfs.

    Only usable when something has already granted write access - a udev rule
    putting the device in the `video` group is the usual arrangement. Checked
    with os.access rather than by trying, so a probe never writes anything.
    """

    name = "sysfs"
    label = "sysfs backlight"

    def __init__(self, device: str = ""):
        self.device = device
        self.path: Optional[Path] = None
        self.max_value = 0

    def _candidates(self) -> list:
        if not SYSFS_ROOT.is_dir():
            return []
        found = sorted(p for p in SYSFS_ROOT.iterdir() if p.is_dir())
        if self.device:
            named = [p for p in found if p.name == self.device]
            if named:
                return named
        return found

    def detect(self) -> bool:
        for entry in self._candidates():
            brightness = entry / "brightness"
            maximum = entry / "max_brightness"
            try:
                if not (brightness.is_file() and maximum.is_file()):
                    continue
                if not os.access(brightness, os.W_OK):
                    continue
                self.max_value = int(maximum.read_text().strip())
            except (OSError, ValueError):
                continue
            if self.max_value > 0:
                self.path = entry
                return True
        return False

    def set(self, percent: int) -> bool:
        if self.path is None:
            return False
        # Never zero. A backlight at raw 0 is a black screen with no way back
        # that anyone can see; one percent of the range is dim, not off.
        raw = max(1, int(round(self.max_value * max(0, min(100, percent)) / 100)))
        try:
            (self.path / "brightness").write_text(str(raw))
            return True
        except OSError:
            return False

    def get(self) -> Optional[int]:
        if self.path is None or not self.max_value:
            return None
        try:
            raw = int((self.path / "brightness").read_text().strip())
        except (OSError, ValueError):
            return None
        return int(round(raw * 100 / self.max_value))

    def detail(self) -> str:
        return f"{self.path.name} (0-{self.max_value})" if self.path else ""


class LogindBackend(Backend):
    """
    Ask systemd-logind to do it.

    The route intended for this: unprivileged, arbitrated, and no suid helper.
    It refuses unless the session has a seat, is in the foreground and belongs
    to the caller - so it works for a panel logged in at the screen and not
    for one started from a service with no seat.

    Driven through `busctl` rather than a D-Bus binding, to avoid adding one
    for a single call.
    """

    name = "logind"
    label = "systemd-logind"

    def __init__(self, device: str = ""):
        self.device = device
        self.max_value = 0

    def detect(self) -> bool:
        if not shutil.which("busctl") or not SYSFS_ROOT.is_dir():
            return False
        for entry in sorted(p for p in SYSFS_ROOT.iterdir() if p.is_dir()):
            if self.device and entry.name != self.device:
                continue
            try:
                self.max_value = int((entry / "max_brightness").read_text().strip())
            except (OSError, ValueError):
                continue
            if self.max_value <= 0:
                continue
            self.device = entry.name
            # Probed by setting what is already set: the call either works or
            # it does not, and this is the only way to find out without
            # changing anything the person would notice.
            current = self._read_raw()
            if current is None:
                continue
            if self._write_raw(current):
                return True
        return False

    def _read_raw(self) -> Optional[int]:
        try:
            return int((SYSFS_ROOT / self.device / "brightness").read_text().strip())
        except (OSError, ValueError):
            return None

    def _write_raw(self, raw: int) -> bool:
        ok, _ = _run(["busctl", "call", "org.freedesktop.login1",
                      "/org/freedesktop/login1/session/self",
                      "org.freedesktop.login1.Session", "SetBrightness",
                      "ssu", "backlight", self.device, str(int(raw))])
        return ok

    def set(self, percent: int) -> bool:
        if not self.max_value:
            return False
        raw = max(1, int(round(self.max_value * max(0, min(100, percent)) / 100)))
        return self._write_raw(raw)

    def get(self) -> Optional[int]:
        raw = self._read_raw()
        if raw is None or not self.max_value:
            return None
        return int(round(raw * 100 / self.max_value))

    def detail(self) -> str:
        return f"{self.device} (0-{self.max_value})" if self.device else ""


class DdcutilBackend(Backend):
    """
    DDC/CI over I2C, for an external monitor.

    The one that matters for a wall panel, which is usually a monitor on the
    end of an HDMI cable rather than a laptop screen. VCP feature x10 is
    brightness in the Monitor Control Command Set.

    Marked slow: a write is tens to hundreds of milliseconds, some monitors
    are unreliable about it, and repeated writes are not free on every panel.
    The controller rate-limits everything, but especially this.
    """

    name = "ddcutil"
    label = "DDC/CI (external monitor)"
    fast = False

    BRIGHTNESS_VCP = "10"

    def __init__(self, display: str = ""):
        self.display = str(display or "")
        self.found = False

    def _base(self) -> list:
        args = ["ddcutil"]
        if self.display:
            args += ["--display", self.display]
        # Retries help on monitors that answer intermittently; the terse
        # output is far easier to parse than the default.
        args += ["--sleep-multiplier", "0.4"]
        return args

    def detect(self) -> bool:
        if not shutil.which("ddcutil"):
            return False
        # detect is slower than a get, but it is the only call that says
        # whether anything is actually reachable.
        ok, out = _run(["ddcutil", "detect", "--brief"], timeout=8.0)
        if not ok or "Display" not in out:
            return False
        if not self.display:
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Display "):
                    self.display = line.split()[1].strip()
                    break
        self.found = self.get() is not None
        return self.found

    def set(self, percent: int) -> bool:
        percent = max(0, min(100, int(percent)))
        ok, _ = _run(self._base() + ["setvcp", self.BRIGHTNESS_VCP, str(percent)],
                     timeout=8.0)
        return ok

    def get(self) -> Optional[int]:
        ok, out = _run(self._base() + ["getvcp", "--brief", self.BRIGHTNESS_VCP],
                       timeout=8.0)
        if not ok or not out:
            return None
        # "VCP 10 C 45 100" - current then maximum
        parts = out.split()
        try:
            index = parts.index("C")
            current, maximum = int(parts[index + 1]), int(parts[index + 2])
        except (ValueError, IndexError):
            return None
        if maximum <= 0:
            return None
        return int(round(current * 100 / maximum))

    def detail(self) -> str:
        return f"display {self.display}" if self.display else ""


class CommandBackend(Backend):
    """
    brightnessctl or light, if one is installed.

    Somebody who has installed one of these has already decided how brightness
    should be set on that machine, and it is not this program's business to
    have a different opinion.

    **Pinned to the backlight class.** Left to choose, `brightnessctl` takes
    the first device it can find - and on a desktop with no internal panel
    that is an LED: a keyboard backlight, a capslock light, a power light. It
    then reports success while controlling something that is not the screen,
    which is worse than reporting failure, because the survey says yes and the
    display never changes.
    """

    def __init__(self, tool: str, device: str = ""):
        self.tool = tool
        self.name = tool
        self.label = tool
        self.device = device or ""

    def _args(self, *rest) -> list:
        if self.tool == "brightnessctl":
            args = ["brightnessctl", "--class=backlight"]
            if self.device:
                args += [f"--device={self.device}"]
            return args + list(rest)
        return ["light"] + list(rest)

    def detect(self) -> bool:
        if not shutil.which(self.tool):
            return False
        # No backlight class device means neither of these tools can be
        # reaching the display, whatever they answer.
        if not backlight_devices():
            return False
        return self.get() is not None

    def set(self, percent: int) -> bool:
        percent = max(1, min(100, int(percent)))
        if self.tool == "brightnessctl":
            ok, _ = _run(self._args("--quiet", "set", f"{percent}%"))
        else:
            ok, _ = _run(["light", "-S", str(percent)])
        return ok

    def get(self) -> Optional[int]:
        if self.tool == "brightnessctl":
            ok, out = _run(self._args("--machine-readable", "get"))
            if not ok:
                return None
            ok_max, out_max = _run(self._args("--machine-readable", "max"))
            try:
                current, maximum = int(out), int(out_max)
                return int(round(current * 100 / maximum)) if maximum else None
            except ValueError:
                return None
        ok, out = _run(["light", "-G"])
        try:
            return int(round(float(out))) if ok else None
        except ValueError:
            return None

    def detail(self) -> str:
        devices = backlight_devices()
        if self.device:
            return self.device
        return devices[0] if devices else ""


#the order they are tried in
BACKEND_ORDER = ("sysfs", "logind", "brightnessctl", "light", "ddcutil")


def build_backend(name: str, device: str = "") -> Optional[Backend]:
    if name == "sysfs":
        return SysfsBackend(device)
    if name == "logind":
        return LogindBackend(device)
    if name == "ddcutil":
        return DdcutilBackend(device)
    if name in ("brightnessctl", "light"):
        return CommandBackend(name, device)
    return None


class BacklightController:
    """
    Picks a backend and feeds it, without ever blocking the caller.

    Everything here runs on one worker thread with a latest-wins slot rather
    than a queue. A fade steps thirty times in a second and a half; sending
    thirty DDC writes would take longer than the fade and wear the monitor
    for the sake of frames nobody sees. The overlay does the smooth part; the
    hardware is told where it ended up.
    """

    def __init__(self, log=None, preferred: str = "auto", device: str = ""):
        self.log = log or (lambda *a, **k: None)
        self.preferred = (preferred or "auto").strip().lower()
        self.device = device or ""

        self.backend: Optional[Backend] = None
        self.ready = threading.Event()

        self._wanted: Optional[int] = None
        self._applied: Optional[int] = None
        self._last_write = 0.0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None

    ## -- lifecycle

    def start(self) -> None:
        """Probe in the background. Detection can take seconds with ddcutil."""
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name="__backlight",
                                        daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _probe(self) -> None:
        if self.preferred in ("off", "overlay", "none"):
            self.log("info", "[Backlight] Hardware control disabled by setting.")
            return

        order = (BACKEND_ORDER if self.preferred == "auto"
                 else (self.preferred,))
        for name in order:
            backend = build_backend(name, self.device)
            if backend is None:
                self.log("warning", f"[Backlight] Unknown backend '{name}'.")
                continue
            try:
                if backend.detect():
                    self.backend = backend
                    self.log("info", f"[Backlight] Using {backend.label}"
                                     f"{' - ' + backend.detail() if backend.detail() else ''}.")
                    return
            except Exception as e:
                self.log("debug", f"[Backlight] {name} probe failed: {e}")

        self.log("info", "[Backlight] No hardware control available - "
                         "falling back to the overlay.")

    def _run(self) -> None:
        self._probe()
        self.ready.set()

        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            if self._stop.is_set():
                return
            if self.backend is None:
                continue

            with self._lock:
                wanted = self._wanted
            if wanted is None or wanted == self._applied:
                continue

            # Rate limited, and harder for a slow backend. Everything between
            # here and the last write is dropped rather than queued - the only
            # value worth sending is the one it ended on.
            gap = MIN_INTERVAL if self.backend.fast else MIN_INTERVAL * 2
            since = time.time() - self._last_write
            if since < gap:
                time.sleep(gap - since)
                self._wake.set()      # re-check; it may have moved again
                continue

            try:
                if self.backend.set(wanted):
                    self._applied = wanted
                else:
                    self.log("debug", f"[Backlight] {self.backend.name} "
                                      f"refused {wanted}%.")
            except Exception as e:
                self.log("warning", f"[Backlight] {self.backend.name} failed: {e}")
            self._last_write = time.time()

    ## -- use

    def available(self) -> bool:
        return self.backend is not None

    def set(self, percent: int) -> None:
        """Ask for a level. Returns immediately; the worker gets there."""
        percent = max(0, min(100, int(percent)))
        with self._lock:
            if self._wanted == percent:
                return
            self._wanted = percent
        self._wake.set()

    def describe(self) -> dict:
        return {
            "available": self.available(),
            "backend":   self.backend.name if self.backend else "overlay",
            "label":     self.backend.label if self.backend else "Overlay only",
            "detail":    self.backend.detail() if self.backend else "",
            "preferred": self.preferred,
            "probed":    self.ready.is_set(),
            "applied":   self._applied,
        }

    @staticmethod
    def survey() -> dict:
        """
        What this machine could do, whatever is configured.

        For the settings page and for /ping-style diagnosis: "it is using the
        overlay" is not useful on its own, and this says why.
        """
        devices = backlight_devices()
        out = {"_panel": {"available": bool(devices),
                          "detail": ", ".join(devices) if devices else
                                    "no internal backlight - external display"}}
        for name in BACKEND_ORDER:
            backend = build_backend(name)
            try:
                ok = bool(backend and backend.detect())
            except Exception:
                ok = False
            out[name] = {"available": ok,
                         "detail": backend.detail() if ok and backend else ""}
        return out
