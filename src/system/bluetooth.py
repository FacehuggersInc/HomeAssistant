"""
Bluetooth, over BlueZ's D-Bus interface.

Pure Python: `jeepney` speaks D-Bus with nothing to compile, so this needs no
system bindings and no `bluetoothctl` subprocess per question. BlueZ already
exposes everything worth asking - whether the adapter is on, what is in range,
what has been paired before, and what a device's battery is down to - so the
work here is asking properly rather than reimplementing any of it.

Everything degrades to "unavailable" rather than raising: a panel with no
adapter, no BlueZ, or no jeepney should say so and carry on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
BATTERY_IFACE = "org.bluez.Battery1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

#Long enough for a pairing round trip, short enough not to freeze a page.
CALL_TIMEOUT = 10.0
CONNECT_TIMEOUT = 30.0


@dataclass
class Device:
    """One Bluetooth device."""
    address: str
    name: str = ""
    paired: bool = False
    trusted: bool = False
    connected: bool = False
    battery: int = -1          #-1 when the device does not report one
    icon: str = ""             #BlueZ's own hint: "audio-headset", "input-mouse"
    rssi: int = 0
    path: str = ""

    @property
    def known(self) -> bool:
        """Whether this has been paired before, so it needs no setup."""
        return bool(self.paired or self.trusted)

    @property
    def label(self) -> str:
        return self.name or self.address

    @property
    def has_battery(self) -> bool:
        return self.battery >= 0


#Whether there is anything to talk to. Worked out once and kept.
#
#Neither answer changes while the app runs - a Python package does not appear
#and an adapter is not plugged into a wall panel - and finding out costs a
#round trip to the system bus. Asking on every repaint made that a synchronous
#D-Bus call on the UI thread once a second.
_missing_cache: Optional[str] = None

#How long to wait for the bus itself. Without this a socket that accepts but
#never finishes the handshake hangs the caller for good - and if that caller is
#the UI thread, the app has stopped.
CONNECT_WAIT = 3.0


def _bus():
    """A blocking D-Bus connection, or None."""
    try:
        from jeepney.io.blocking import open_dbus_connection
    except Exception:
        return None
    try:
        connection = open_dbus_connection(bus="SYSTEM")
    except Exception:
        return None
    try:
        # Applied to the socket, since open_dbus_connection takes no timeout of
        # its own and a reply that never comes would otherwise block forever.
        connection.sock.settimeout(CONNECT_WAIT)
    except Exception:
        pass
    return connection


def _message(path: str, interface: str, member: str, signature: str = "",
             body: tuple = ()):
    from jeepney import new_method_call, DBusAddress
    address = DBusAddress(path, bus_name=BLUEZ, interface=interface)
    return new_method_call(address, member, signature, body)


def _call(connection, message, timeout: float = CALL_TIMEOUT):
    """Send and wait. Returns the reply body, or None."""
    try:
        reply = connection.send_and_get_reply(message, timeout=timeout)
    except Exception:
        return None
    if getattr(reply, "header", None) is not None:
        from jeepney import MessageType
        if reply.header.message_type is MessageType.error:
            return None
    return reply.body


def disabled() -> bool:
    """
    Whether Bluetooth has been switched off from outside the app.

    `HA_NO_BLUETOOTH=1` or `HA_SAFE_MODE=1` makes every call here answer
    immediately without touching the bus. See src/system/safemode.py - a
    suspicion that cannot be tested is not worth much on hardware you have to
    walk to.
    """
    from src.system import safemode
    return safemode.no_bluetooth()


def have_jeepney() -> bool:
    try:
        import jeepney  # noqa: F401
        return True
    except ImportError:
        return False


def available() -> bool:
    """Whether there is a Bluetooth adapter to talk to."""
    return adapter_path() is not None


def missing(refresh: bool = False) -> str:
    """
    Which requirement is unmet, as a key for the requirements table.

    Told apart rather than lumped together: "install a Python package" and
    "start the Bluetooth service" are different jobs, and a message that says
    the wrong one sends somebody the wrong way.

    **Cached, and never call this from the UI thread the first time.** Working
    it out is a round trip to the system bus, and if BlueZ is not running that
    is a D-Bus service activation attempt - which waits far longer than a
    person will believe the app is still alive for.
    """
    global _missing_cache
    if disabled():
        _missing_cache = "bluetooth"
        return _missing_cache
    if _missing_cache is not None and not refresh:
        return _missing_cache

    if not have_jeepney():
        _missing_cache = "bluetooth_dbus"
    elif adapter_path() is None:
        _missing_cache = "bluetooth"
    else:
        _missing_cache = ""
    return _missing_cache


def known() -> bool:
    """Whether missing() has an answer yet, so a caller can avoid blocking."""
    return _missing_cache is not None


@dataclass
class Snapshot:
    """Everything the panel asks for, from one round trip."""
    powered: bool = False
    devices: list = None
    connected: Optional["Device"] = None

    def __post_init__(self):
        if self.devices is None:
            self.devices = []


def snapshot() -> Snapshot:
    """
    The adapter and its devices, in **one** call to the bus.

    powered(), devices() and connected_device() each ask separately, which is
    three connections and three GetManagedObjects for one screen refresh. The
    tree already holds all of it.
    """
    objects = _managed_objects()
    path = None
    for candidate, interfaces in objects.items():
        if ADAPTER_IFACE in interfaces:
            path = candidate
            break
    if path is None:
        return Snapshot()

    on = bool(_properties(objects.get(path, {}), ADAPTER_IFACE).get("Powered"))
    found = parse_devices(objects) if on else []
    joined = [d for d in found if d.connected]
    joined.sort(key=lambda d: (not d.has_battery, d.label.lower()))
    return Snapshot(powered=on, devices=found,
                    connected=joined[0] if joined else None)


def _managed_objects() -> dict:
    """Everything BlueZ knows about, in one call."""
    if disabled() or not have_jeepney():
        return {}
    connection = _bus()
    if connection is None:
        return {}
    try:
        body = _call(connection, _message(
            "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"))
        return body[0] if body else {}
    finally:
        try:
            connection.close()
        except Exception:
            pass


def adapter_path() -> Optional[str]:
    for path, interfaces in _managed_objects().items():
        if ADAPTER_IFACE in interfaces:
            return path
    return None


def _unwrap(value):
    """jeepney hands back (signature, value) for a variant."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return value[1]
    return value


def _properties(interfaces: dict, name: str) -> dict:
    return {key: _unwrap(val)
            for key, val in (interfaces.get(name) or {}).items()}


## -- the adapter

def powered() -> bool:
    path = adapter_path()
    if path is None:
        return False
    objects = _managed_objects()
    return bool(_properties(objects.get(path, {}), ADAPTER_IFACE).get("Powered"))


def set_powered(on: bool) -> tuple:
    """Turn the adapter on or off. Returns (ok, message)."""
    path = adapter_path()
    if path is None:
        return False, "No Bluetooth adapter."
    connection = _bus()
    if connection is None:
        return False, "Could not reach the system bus."
    try:
        body = _call(connection, _message(
            path, PROPS_IFACE, "Set", "ssv",
            (ADAPTER_IFACE, "Powered", ("b", bool(on)))))
        if body is None:
            return False, ("Could not turn Bluetooth "
                           f"{'on' if on else 'off'}.")
        return True, f"Bluetooth {'on' if on else 'off'}."
    finally:
        try:
            connection.close()
        except Exception:
            pass


def discovering() -> bool:
    path = adapter_path()
    if path is None:
        return False
    objects = _managed_objects()
    return bool(_properties(objects.get(path, {}),
                            ADAPTER_IFACE).get("Discovering"))


def start_scan() -> bool:
    path = adapter_path()
    if path is None:
        return False
    connection = _bus()
    if connection is None:
        return False
    try:
        return _call(connection, _message(
            path, ADAPTER_IFACE, "StartDiscovery")) is not None
    finally:
        try:
            connection.close()
        except Exception:
            pass


def stop_scan() -> bool:
    path = adapter_path()
    if path is None:
        return False
    connection = _bus()
    if connection is None:
        return False
    try:
        return _call(connection, _message(
            path, ADAPTER_IFACE, "StopDiscovery")) is not None
    finally:
        try:
            connection.close()
        except Exception:
            pass


## -- devices

def parse_devices(objects: dict) -> list:
    """
    BlueZ's object tree, as a list of devices.

    Split out from the D-Bus call so it can be tested against a captured tree
    without an adapter present.

    Sorted connected first, then things paired before, then by signal. A list
    that opens on the headphones already in use is a list that answers the
    common question without being read.
    """
    devices = []
    for path, interfaces in (objects or {}).items():
        if DEVICE_IFACE not in interfaces:
            continue
        props = _properties(interfaces, DEVICE_IFACE)
        battery = _properties(interfaces, BATTERY_IFACE).get("Percentage", -1)
        try:
            battery = int(battery)
        except (TypeError, ValueError):
            battery = -1
        try:
            rssi = int(props.get("RSSI", 0) or 0)
        except (TypeError, ValueError):
            rssi = 0
        devices.append(Device(
            address=str(props.get("Address", "")),
            name=str(props.get("Alias") or props.get("Name") or ""),
            paired=bool(props.get("Paired")),
            trusted=bool(props.get("Trusted")),
            connected=bool(props.get("Connected")),
            battery=battery,
            icon=str(props.get("Icon", "")),
            rssi=rssi,
            path=str(path),
        ))

    return sorted(devices, key=lambda d: (not d.connected, not d.known,
                                          -d.rssi, d.label.lower()))


def devices() -> list:
    return parse_devices(_managed_objects())


def connected_device() -> Optional[Device]:
    """The one in use, for the quick panel. Prefers one reporting a battery."""
    joined = [d for d in devices() if d.connected]
    if not joined:
        return None
    joined.sort(key=lambda d: (not d.has_battery, d.label.lower()))
    return joined[0]


def _device_action(path: str, member: str, timeout: float) -> tuple:
    connection = _bus()
    if connection is None:
        return False, "Could not reach the system bus."
    try:
        body = _call(connection, _message(path, DEVICE_IFACE, member),
                     timeout=timeout)
        return (body is not None), ""
    finally:
        try:
            connection.close()
        except Exception:
            pass


def connect(device: Device) -> tuple:
    """
    Connect, pairing first if this device is new.

    Pairing is attempted before connecting rather than instead of it: a device
    already paired connects straight away, and one that is not has to be paired
    before a connection means anything.
    """
    if not device or not device.path:
        return False, "No device."
    if not device.known:
        ok, _ = _device_action(device.path, "Pair", CONNECT_TIMEOUT)
        if not ok:
            return False, (f"Could not pair with {device.label}. It may need "
                           f"to be in pairing mode.")
        # Trusted so it reconnects on its own next time, which is what somebody
        # pairing a pair of headphones to a wall panel means by it.
        _set_device_property(device.path, "Trusted", True)

    ok, _ = _device_action(device.path, "Connect", CONNECT_TIMEOUT)
    if ok:
        return True, f"Connected to {device.label}."
    return False, f"Could not connect to {device.label}."


def disconnect(device: Device) -> tuple:
    if not device or not device.path:
        return False, "No device."
    ok, _ = _device_action(device.path, "Disconnect", CALL_TIMEOUT)
    return ok, (f"Disconnected {device.label}." if ok
                else f"Could not disconnect {device.label}.")


def forget(device: Device) -> tuple:
    """Remove the pairing, so it stops reconnecting on its own."""
    path = adapter_path()
    if path is None or not device or not device.path:
        return False, "No device."
    connection = _bus()
    if connection is None:
        return False, "Could not reach the system bus."
    try:
        body = _call(connection, _message(
            path, ADAPTER_IFACE, "RemoveDevice", "o", (device.path,)))
        return ((body is not None),
                f"Forgot {device.label}." if body is not None
                else f"Could not forget {device.label}.")
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _set_device_property(path: str, name: str, value) -> bool:
    connection = _bus()
    if connection is None:
        return False
    try:
        return _call(connection, _message(
            path, PROPS_IFACE, "Set", "ssv",
            (DEVICE_IFACE, name, ("b", bool(value))))) is not None
    finally:
        try:
            connection.close()
        except Exception:
            pass


def describe() -> str:
    if not have_jeepney():
        return "jeepney not installed"
    if adapter_path() is None:
        return "no adapter"
    return "bluez" + (" (on)" if powered() else " (off)")
