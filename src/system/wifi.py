"""
Which network the panel is on, what else is in range, and how to join one.

`nmcli` is the only backend that can do the whole job. NetworkManager is what
actually holds credentials and reconnects on boot, so joining a network through
anything else would work until the next restart and then quietly stop. Where it
is missing, `iw`/`iwgetid` still answer "what am I connected to" and the rest is
reported as unavailable rather than half-done.

Throughput is read from the kernel's own counters in /sys/class/net, which needs
no tool at all.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

#Long enough for a scan, short enough that the settings page is not frozen by a
#radio that has gone away.
CALL_TIMEOUT = 12.0
CONNECT_TIMEOUT = 45.0

_SYS_NET = Path("/sys/class/net")


@dataclass
class Network:
    """One network in range."""
    ssid: str
    signal: int = 0            #0-100
    security: str = ""         #"WPA2", "WPA3", "" for open
    active: bool = False       #the one currently joined
    known: bool = False        #a saved profile exists, so no password needed
    frequency: str = ""        #"2.4 GHz" / "5 GHz"

    @property
    def open(self) -> bool:
        return not self.security or self.security.lower() in ("none", "--")

    @property
    def bars(self) -> int:
        """Signal as 0-4, for an icon."""
        if self.signal >= 75: return 4
        if self.signal >= 50: return 3
        if self.signal >= 30: return 2
        if self.signal > 0:   return 1
        return 0


@dataclass
class Connection:
    """The network the panel is on."""
    ssid: str
    interface: str = ""
    signal: int = 0
    security: str = ""
    ip_address: str = ""

    @property
    def bars(self) -> int:
        return Network(self.ssid, self.signal).bars


def _run_reason(args: list, timeout: float = CALL_TIMEOUT) -> tuple:
    """(ok, stdout, stderr). Never raises."""
    return _run(args, timeout)


def _run(args: list, timeout: float = CALL_TIMEOUT) -> tuple:
    """(ok, stdout, stderr). Never raises."""
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return done.returncode == 0, done.stdout or "", done.stderr or ""
    except subprocess.TimeoutExpired:
        return False, "", "timed out"
    except (OSError, subprocess.SubprocessError) as e:
        return False, "", str(e)


def backend() -> str:
    """"nmcli", "iw", or "" when there is no way to ask."""
    if shutil.which("nmcli"):
        return "nmcli"
    if shutil.which("iwgetid") or shutil.which("iw"):
        return "iw"
    return ""


def available() -> bool:
    return bool(backend())


def can_connect() -> bool:
    """
    Whether joining a network is possible, not just reading one.

    Only NetworkManager: it is what stores the credential and brings the link
    back after a reboot. Joining by any other route would hold until the next
    restart and then silently stop, which is worse than saying no.
    """
    return backend() == "nmcli"


## -- nmcli's terse format

def _split_terse(line: str) -> list:
    r"""
    Split one `nmcli -t` line on unescaped colons.

    nmcli escapes `:` and `\` inside values, so a plain split() mangles any
    network with a colon in its name - and those exist. This is why terse mode
    is worth parsing properly rather than reading the aligned output, which has
    no escaping at all and cannot be split reliably.
    """
    fields, current, i = [], [], 0
    while i < len(line):
        char = line[i]
        if char == "\\" and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
            continue
        if char == ":":
            fields.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    fields.append("".join(current))
    return fields


def _normalise_security(raw: str) -> str:
    """nmcli reports a flag soup; the page wants a word."""
    text = (raw or "").strip()
    if not text or text in ("--", "none"):
        return ""
    upper = text.upper()
    if "WPA3" in upper or "SAE" in upper:
        return "WPA3"
    if "WPA2" in upper or "RSN" in upper:
        return "WPA2"
    if "WPA" in upper:
        return "WPA"
    if "WEP" in upper:
        return "WEP"
    if "802.1X" in upper:
        return "Enterprise"
    return text


def _band(freq_mhz: str) -> str:
    try:
        mhz = int(float(freq_mhz))
    except (TypeError, ValueError):
        return ""
    if mhz >= 5900:
        return "6 GHz"
    if mhz >= 4900:
        return "5 GHz"
    if mhz >= 2400:
        return "2.4 GHz"
    return ""


## -- reading

#Why current() last returned nothing. Read by anything that wants to say more
#than "not connected" - which, on a machine that plainly IS connected, is not
#an answer anybody can act on.
LAST_REASON = ""


def current() -> Optional[Connection]:
    """
    The network the panel is on, or None.

    Every route is tried before giving up. nmcli is preferred because it knows
    the most, but a machine where it answers nothing - a permissions problem, a
    field this version does not have, NetworkManager not managing the device -
    may still have a perfectly good `iwgetid`. Reporting "not connected" while
    `iwgetid -r` prints an SSID is worse than being slow.
    """
    global LAST_REASON
    LAST_REASON = ""

    found = _current_nmcli() if backend() == "nmcli" else None
    if found is not None:
        return found

    found = _current_iw()
    if found is not None:
        LAST_REASON = ""
        return found

    if not LAST_REASON:
        LAST_REASON = "no route reported a wireless connection"
    return None


def _current_nmcli() -> Optional[Connection]:
    global LAST_REASON
    name = "nmcli"

    if True:
        # The active connection, from `connection show`.
        #
        # Asked here rather than from `device wifi list`, whose in-use column is
        # IN-USE and marked with a star - ACTIVE is a field of `connection
        # show`, and nmcli errors on a field it does not have, so asking for the
        # wrong one reports "not connected" on a perfectly connected machine.
        ssid, device = "", ""
        ok, out, _ = _run_reason(["nmcli", "-t", "-f", "TYPE,NAME,DEVICE",
                                  "connection", "show", "--active"])[:3]
        if ok:
            for line in out.splitlines():
                parts = _split_terse(line.strip())
                # Both spellings. Older nmcli reports the full connection
                # type, "802-11-wireless"; newer reports "wifi" - which does
                # not contain the word "wireless", so matching on that alone
                # found nothing on any current NetworkManager.
                kind = parts[0].lower()
                if len(parts) >= 3 and ("wireless" in kind or kind == "wifi"):
                    ssid, device = parts[1], parts[2]
                    break

        # Signal and security come from the scan list, matched on the star.
        signal, security = 0, ""
        ok, out, _ = _run_reason(["nmcli", "-t", "-f",
                                  "IN-USE,SSID,SIGNAL,SECURITY",
                                  "device", "wifi", "list"])[:3]
        if ok:
            for line in out.splitlines():
                parts = _split_terse(line.strip())
                if len(parts) >= 4 and parts[0].strip() in ("*", "yes"):
                    if not ssid:
                        ssid = parts[1]
                    try:
                        signal = int(parts[2] or 0)
                    except ValueError:
                        signal = 0
                    security = _normalise_security(parts[3])
                    break

        if not ssid:
            LAST_REASON = ("nmcli reported no active wireless connection "
                           "and no in-use network")
            return None
        if not device:
            device = _wireless_interface()
        return Connection(ssid=ssid, signal=signal, security=security,
                          interface=device, ip_address=_ip_for(device))


def _current_iw() -> Optional[Connection]:
    global LAST_REASON
    if True:
        if shutil.which("iwgetid"):
            ok, out, _ = _run(["iwgetid", "-r"])
            ssid = out.strip()
            if ok and ssid:
                iface = _wireless_interface()
                return Connection(ssid=ssid, interface=iface,
                                  ip_address=_ip_for(iface))
        if shutil.which("iw"):
            iface = _wireless_interface()
            if iface:
                ok, out, _ = _run(["iw", "dev", iface, "link"])
                match = re.search(r"SSID:\s*(.+)", out)
                if ok and match:
                    return Connection(ssid=match.group(1).strip(),
                                      interface=iface,
                                      ip_address=_ip_for(iface))
        LAST_REASON = LAST_REASON or "iwgetid and iw reported nothing"
        return None


def scan(rescan: bool = True) -> list:
    """
    Everything in range, strongest first.

    One entry per name. A network with more than one access point shows up once
    per radio, and a list with the same name five times is not a list of
    networks.
    """
    if backend() != "nmcli":
        return []

    args = ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY,FREQ",
            "device", "wifi", "list"]
    if rescan:
        args.append("--rescan")
        args.append("yes")
    ok, out, _ = _run(args)
    if not ok:
        # A rescan needs privileges the panel may not have; the cached list is
        # still worth showing.
        ok, out, _ = _run(["nmcli", "-t", "-f",
                           "ACTIVE,SSID,SIGNAL,SECURITY,FREQ",
                           "device", "wifi", "list"])
        if not ok:
            return []

    saved = set(known_networks())
    best: dict = {}
    for line in out.splitlines():
        parts = _split_terse(line.strip())
        if len(parts) < 4 or not parts[1]:
            continue
        try:
            signal = int(parts[2] or 0)
        except ValueError:
            signal = 0
        entry = Network(
            ssid=parts[1], signal=signal,
            security=_normalise_security(parts[3]),
            active=parts[0].lower() == "yes",
            known=parts[1] in saved,
            frequency=_band(parts[4]) if len(parts) > 4 else "")
        seen = best.get(entry.ssid)
        # The strongest radio for a name wins, but "currently joined" is never
        # lost to a stronger one that is not.
        if seen is None or entry.signal > seen.signal or entry.active:
            if seen is not None and seen.active:
                entry.active = True
            best[entry.ssid] = entry

    return sorted(best.values(),
                  key=lambda n: (not n.active, -n.signal, n.ssid.lower()))


def known_networks() -> list:
    """Names with a saved profile, so no password is needed to rejoin."""
    if backend() != "nmcli":
        return []
    ok, out, _ = _run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    if not ok:
        return []
    names = []
    for line in out.splitlines():
        parts = _split_terse(line.strip())
        if len(parts) >= 2 and "wireless" in parts[1].lower():
            names.append(parts[0])
    return names


def _wireless_interface() -> str:
    """The first interface that looks like a radio."""
    try:
        for path in sorted(_SYS_NET.iterdir()):
            if (path / "wireless").exists() or (path / "phy80211").exists():
                return path.name
    except OSError:
        pass
    return ""


def _ip_for(interface: str) -> str:
    if not interface:
        return ""
    if shutil.which("ip"):
        ok, out, _ = _run(["ip", "-4", "-o", "addr", "show", "dev", interface])
        if ok:
            match = re.search(r"inet\s+([0-9.]+)", out)
            if match:
                return match.group(1)
    return ""


## -- joining

def connect(ssid: str, password: str = "") -> tuple:
    """
    Join a network. Returns (ok, message).

    The message is nmcli's own reason on failure, trimmed. A wrong password and
    a network out of range fail differently and the person needs to know which.
    """
    if not can_connect():
        return False, "NetworkManager is not available on this machine."
    if not ssid:
        return False, "No network name."

    args = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    ok, out, err = _run(args, timeout=CONNECT_TIMEOUT)
    if ok:
        return True, f"Connected to {ssid}."

    reason = (err or out or "").strip().splitlines()
    text = reason[-1] if reason else "Could not connect."
    text = re.sub(r"^Error:\s*", "", text).strip()
    # nmcli says this for a bad password, and it is not obvious out of context.
    if "secrets were required" in text.lower() or "802.1x" in text.lower():
        text = "Wrong password, or the network needs more than a password."
    return False, text or "Could not connect."


def disconnect() -> tuple:
    """Drop the current wireless link without forgetting it."""
    if not can_connect():
        return False, "NetworkManager is not available on this machine."
    iface = _wireless_interface()
    if not iface:
        return False, "No wireless interface."
    ok, out, err = _run(["nmcli", "device", "disconnect", iface])
    return ok, ("Disconnected." if ok
                else (err or out or "Could not disconnect.").strip())


def forget(ssid: str) -> tuple:
    """Delete a saved profile, so it stops rejoining on its own."""
    if not can_connect():
        return False, "NetworkManager is not available on this machine."
    ok, out, err = _run(["nmcli", "connection", "delete", ssid])
    return ok, (f"Forgot {ssid}." if ok
                else (err or out or "Could not forget it.").strip())


## -- throughput

@dataclass
class Counters:
    rx: int = 0
    tx: int = 0
    at: float = field(default_factory=time.monotonic)


def counters(interface: str = "") -> Optional[Counters]:
    """
    The kernel's byte counters for an interface.

    Straight from /sys, so this needs no tool and costs a file read. The
    counters are cumulative since boot; a rate needs two samples.
    """
    iface = interface or _wireless_interface()
    if not iface:
        return None
    base = _SYS_NET / iface / "statistics"
    try:
        return Counters(rx=int((base / "rx_bytes").read_text().strip()),
                        tx=int((base / "tx_bytes").read_text().strip()))
    except (OSError, ValueError):
        return None


def rates(previous: Counters, latest: Counters) -> tuple:
    """
    (down_bytes_per_second, up_bytes_per_second) between two samples.

    A negative difference means the counter wrapped or the interface was reset,
    and is reported as zero rather than as a spike - a 32-bit counter wrapping
    would otherwise read as four gigabytes in one second.
    """
    if previous is None or latest is None:
        return 0.0, 0.0
    seconds = latest.at - previous.at
    if seconds <= 0:
        return 0.0, 0.0
    down = max(0, latest.rx - previous.rx) / seconds
    up = max(0, latest.tx - previous.tx) / seconds
    return down, up


def human_rate(bytes_per_second: float) -> str:
    """A rate as a short string. Bits, because that is how links are sold."""
    bits = max(0.0, float(bytes_per_second)) * 8
    for unit, step in (("Gb/s", 1e9), ("Mb/s", 1e6), ("kb/s", 1e3)):
        if bits >= step:
            value = bits / step
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return "0 kb/s"


def describe() -> str:
    name = backend()
    if not name:
        return "unavailable"
    return name if can_connect() else f"{name} (read-only)"
