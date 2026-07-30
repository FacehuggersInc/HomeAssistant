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
import re
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


#Why a call did not succeed, when it did not.
#
#A timeout and a missing tool both used to come back as (False, "") and were
#therefore indistinguishable. They are not the same problem: one means the
#route does not exist on this machine, the other means it does and was too slow
#to answer this time - and only one of those is worth trying again.
TIMED_OUT = "timeout"
FAILED = "failed"
MISSING = "missing"


def _run(args: list, timeout: float = CALL_TIMEOUT) -> tuple:
    """(ok, stdout). Never raises, never hangs."""
    ok, out, _ = _run_reason(args, timeout)
    return ok, out


def _run_reason(args: list, timeout: float = CALL_TIMEOUT) -> tuple:
    """(ok, stdout, reason). `reason` is empty when it worked."""
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, check=False)
        if done.returncode == 0:
            return True, (done.stdout or "").strip(), ""
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return False, (done.stdout or "").strip(), (
            f"{FAILED}: {detail[-1][:90]}" if detail else FAILED)
    except subprocess.TimeoutExpired:
        return False, "", f"{TIMED_OUT} after {timeout:g}s"
    except FileNotFoundError:
        return False, "", MISSING
    except (OSError, subprocess.SubprocessError) as e:
        return False, "", f"{FAILED}: {e}"


class Backend:
    #Why detect() last returned False. Empty when it has not been asked, or
    #when it succeeded. Read by the controller so a rejection can be logged
    #rather than silently skipped.
    reason = ""

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

    #Shortens DDC's inter-message waits to this fraction. Faster, and a common
    #reason a monitor stops answering: the timings in the spec are what slower
    #panels actually need. Used only once a display has proven it responds.
    FAST_MULTIPLIER = "0.4"

    def _base(self, patient: bool = False) -> list:
        args = ["ddcutil"]
        if self.display:
            args += ["--display", self.display]
        if not patient:
            args += ["--sleep-multiplier", self.FAST_MULTIPLIER]
        return args

    @staticmethod
    def _parse_displays(out: str) -> list:
        """
        Every display number ddcutil reported, in order.

        By regex rather than by splitting a line on whitespace, because the
        exact shape of `detect` output varies between ddcutil versions and a
        parse that reads the number as "the second word" silently produces
        nothing when it does not. Nothing then becomes an empty --display flag,
        which on a machine with three monitors is a question ddcutil cannot
        answer.
        """
        numbers = re.findall(r"^\s*Display\s+(\d+)\b", out, re.MULTILINE)
        if numbers:
            return numbers
        # Older output, or a format that changed again: fall back to the bus
        # numbers, which ddcutil also accepts via --bus.
        return []

    #Why detect() last said no. Read by the controller for its log.
    reason = ""

    def detect(self, timeout: float = 8.0) -> bool:
        self.reason = ""
        if not shutil.which("ddcutil"):
            self.reason = "ddcutil is not installed"
            return False
        # detect is slower than a get, but it is the only call that says
        # whether anything is actually reachable. It walks every I2C bus, so
        # under load - which startup is - it can take longer than it does from
        # a prompt on an idle machine.
        if self.display:
            candidates = [self.display]
        else:
            ok, out, why = _run_reason(["ddcutil", "detect", "--brief"],
                                       timeout=timeout)
            if not ok:
                self.reason = why or "ddcutil detect found nothing"
                return False
            candidates = self._parse_displays(out)
            if not candidates:
                self.reason = ("ddcutil answered but no display number could "
                               "be read from it")
                return False

        # Every one of them, not just the first.
        #
        # A desk with three monitors on it is the normal case for this backend,
        # and there is no reason the first one ddcutil lists is the one that
        # answers VCP 10 - plenty of monitors do not implement it at all.
        # Giving up after one meant a working display two rows down was never
        # asked.
        tried = []
        for number in candidates:
            self.display = number
            # Patiently: the shortened timings are for a display already known
            # to work, and using them to decide whether one works is how a
            # slower panel gets written off.
            if self.get(patient=True) is not None:
                self.found = True
                return True
            tried.append(number)

        self.display = ""
        self.found = False
        self.reason = ("no display reported a brightness value (tried "
                       + ", ".join(tried) + ")")
        return False

    def set(self, percent: int) -> bool:
        percent = max(0, min(100, int(percent)))
        ok, _ = _run(self._base() + ["setvcp", self.BRIGHTNESS_VCP, str(percent)],
                     timeout=8.0)
        return ok

    def get(self, patient: bool = False) -> Optional[int]:
        ok, out = _run(self._base(patient) +
                       ["getvcp", "--brief", self.BRIGHTNESS_VCP],
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

    def _probe(self, timeout: float = None) -> None:
        if self.preferred in ("off", "overlay", "none"):
            self.log("info", "[Backlight] Hardware control disabled by setting.")
            return

        order = (BACKEND_ORDER if self.preferred == "auto"
                 else (self.preferred,))
        rejected = []
        for name in order:
            backend = build_backend(name, self.device)
            if backend is None:
                self.log("warning", f"[Backlight] Unknown backend '{name}'.")
                continue
            try:
                # A longer leash on the retry, where the machine is idle and
                # the extra wait costs nobody anything.
                detected = (backend.detect(timeout=timeout)
                            if timeout and name == "ddcutil"
                            else backend.detect())
                if detected:
                    self.backend = backend
                    self.log("info", f"[Backlight] Using {backend.label}"
                                     f"{' - ' + backend.detail() if backend.detail() else ''}.")
                    return
                rejected.append((name, getattr(backend, "reason", "")
                                 or "not available"))
            except Exception as e:
                rejected.append((name, f"probe raised: {e}"))

        # Said in full, not swallowed.
        #
        # "No hardware control available" on its own is not something anybody
        # can act on - especially when the same machine reports a working
        # ddcutil from a prompt a minute later. The reason each route was
        # passed over is the whole difference between "this box cannot" and
        # "this box was busy".
        for name, why in rejected:
            self.log("info", f"[Backlight]   {name}: {why}")
        self.log("info", "[Backlight] No hardware control available - "
                         "falling back to the overlay.")
        self._retry_pending = bool(
            [1 for _, why in rejected if TIMED_OUT in why])

    #How long after a timed-out probe to try once more, and how much longer to
    #let it take. Startup is the busiest the machine gets: plugins loading, a
    #browser engine starting, a speech model being read off disk. A DDC/CI
    #round trip over I2C is slow at the best of times and this is not one of
    #them, so a single attempt at second one is the worst possible moment to
    #decide a monitor cannot be controlled.
    RETRY_AFTER = 25.0
    RETRY_TIMEOUT = 20.0

    def _maybe_retry(self) -> None:
        """One more probe, once the machine has settled."""
        if not getattr(self, "_retry_pending", False):
            return
        if time.time() - self._started_at < self.RETRY_AFTER:
            return
        self._retry_pending = False
        self.log("info", "[Backlight] Trying again now that startup is over "
                         "- the first probe timed out.")
        self._probe(timeout=self.RETRY_TIMEOUT)
        if self.backend is None:
            self.log("info", "[Backlight] Still nothing; staying on the overlay.")

    def _run(self) -> None:
        self._started_at = time.time()
        self._retry_pending = False
        self._probe()
        self.ready.set()

        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            if self._stop.is_set():
                return
            if self.backend is None:
                self._maybe_retry()
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
