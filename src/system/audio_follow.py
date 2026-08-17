"""
A connected Bluetooth speaker should be the one making the sound.

Connecting a speaker and then opening the system's sound settings to select
it is two steps for something with one obvious answer: nobody pairs a
speaker to a wall panel and then wants the panel's own drivers.

This sets the SYSTEM default, not the panel's output setting. Music plays
through a hidden browser page, and a browser follows the system default - so
moving only what this process plays would put the assistant's voice on the
speaker and leave the music on the panel. Half a switch is worse than none:
it sounds broken rather than unconfigured.

Two jobs, and the second is the one that matters day to day:

  * A speaker CONNECTS         -> make it the default, move what is playing.
  * A speaker IS connected but
    the default is something
    else                       -> the same thing, again.

The second is a repair rather than an event. A sound server restarting, a
speaker dropping and re-attaching, or something else on the machine taking
the default all leave a panel with a speaker connected and the sound coming
out of the wrong thing, with nothing to say so. It is checked on a timer for
the same reason the volume minimum is applied on every wake: the state is
what matters, not the moment it changed.
"""

from __future__ import annotations

from typing import Optional

from src.system import bluetooth, sinks

#BlueZ's own icon hints for something that plays sound. The same tokens the
#device list ranks by - see bluetooth.KIND_ORDER - kept here as a set because
#this asks a yes/no question rather than an ordering one.
AUDIO_HINTS = ("headset", "headphone", "audio", "speaker")

#What a Bluetooth sink is called by PipeWire and PulseAudio. The address
#follows, with colons as underscores.
SINK_PREFIX = "bluez_output."
#PulseAudio's older name for the same thing. Both are matched, since the two
#servers name these differently and a panel may be on either.
SINK_PREFIX_OLD = "bluez_sink."


def is_audio(device: bluetooth.Device) -> bool:
    """Whether this device is something that plays sound."""
    hint = (getattr(device, "icon", "") or "").lower()
    return any(token in hint for token in AUDIO_HINTS)


def _address_key(address: str) -> str:
    """An address in the form a sink name spells it: AA_BB_CC_DD_EE_FF."""
    return str(address or "").strip().upper().replace(":", "_")


def sink_for_device(device: bluetooth.Device,
                    catalogue: list = None) -> str:
    """
    The sink a connected Bluetooth device is playing through, or empty.

    Matched on the ADDRESS rather than the name. A sink description is the
    device's Bluetooth name and two speakers of the same model share it,
    while the address is in the sink's own identifier and is unique by
    definition.

    Empty is a normal answer, not a failure: a speaker that has just
    connected takes a moment to appear as a sink, and a headset connected for
    calls only has no output at all.
    """
    key = _address_key(getattr(device, "address", ""))
    if not key:
        return ""

    for sink in (catalogue if catalogue is not None else sinks.sinks()):
        name = sink.get("name") or ""
        lowered = name.lower()
        if not (lowered.startswith(SINK_PREFIX)
                or lowered.startswith(SINK_PREFIX_OLD)):
            continue
        if key in name.upper():
            return name
    return ""


def connected_speaker() -> Optional[bluetooth.Device]:
    """
    The connected audio device to follow, or None.

    The first one, using the ordering the device list already applies, so
    this agrees with what the quick panel shows rather than picking a second
    device nobody can see is connected.
    """
    try:
        for device in bluetooth.connected_devices():
            if is_audio(device):
                return device
    except Exception:
        return None
    return None


def wanted_sink() -> tuple:
    """
    (sink name, device) that sound should be coming out of, or ("", None).

    ("", None) means there is nothing to follow - no speaker connected, or
    one connected that the sound server has not published a sink for yet.
    Neither is an error and neither should move anything.
    """
    device = connected_speaker()
    if device is None:
        return "", None
    return sink_for_device(device), device


def needs_fixing() -> tuple:
    """
    (should switch, sink, device) - whether the default is already right.

    Asked before doing anything, so the ordinary case is two cheap reads and
    no change. Switching unconditionally would move every playing stream on
    every check, which is audible.
    """
    sink, device = wanted_sink()
    if not sink:
        return False, "", None
    return sinks.default_sink() != sink, sink, device


#The default sink from before a speaker took it over, so it can be given
#back. Module level because this is one machine's state, and the follow is
#called from a timer that holds nothing of its own.
#
#Empty when no speaker is currently being followed.
_previous_default: str = ""

#The sink a switch has already failed on, so the failure is reported once.
#
#Without this the complaint is on the same timer as the check: a speaker the
#server will not switch to produces a toast every fifteen seconds, forever.
#Cleared when the situation changes, so a second attempt that fails is
#reported again.
_warned_about: str = ""


def _is_bluetooth_sink(name: str) -> bool:
    lowered = str(name or "").lower()
    return (lowered.startswith(SINK_PREFIX)
            or lowered.startswith(SINK_PREFIX_OLD))


def apply(log=None, announce=None) -> Optional[str]:
    """
    Make a connected speaker the default and move what is playing to it.

    Returns the sink it switched to, or None when nothing needed doing. Never
    raises: this runs on a timer, and a sound server that has gone away is
    the ordinary reason it would.

    `announce(title, body)` is called only when something actually changed.
    Passed in rather than reached for, so this module stays a system one -
    it knows about sinks and BlueZ, and nothing about toasts.
    """
    global _previous_default, _warned_about
    try:
        if not sinks.available():
            return None

        change, sink, device = needs_fixing()

        if not sink:
            # Nothing to follow. If this put a speaker in charge earlier, the
            # speaker has since gone and the default is pointing at a sink
            # that no longer exists - which is silence rather than a fallback
            # on some servers.
            _warned_about = ""
            return _restore(log, announce)

        if not change:
            _warned_about = ""
            return None

        # Only worth keeping if it is not itself a speaker this put there.
        # Following one speaker straight to another must not record the first
        # as the thing to fall back to.
        current = sinks.default_sink()
        if current and not _is_bluetooth_sink(current):
            _previous_default = current

        if not sinks.set_default_sink(sink):
            first = _warned_about != sink
            _warned_about = sink
            if log and first:
                log("warning",
                    f"[Audio] Could not make '{device.label}' the default "
                    f"output. Sound is still going somewhere else.")
            if announce and first:
                announce("Bluetooth audio",
                         f"{device.label} is connected, but the sound could "
                         f"not be moved to it.")
            return None

        _warned_about = ""
        # Everything already playing, or the switch is silent until the next
        # track starts - which reads as it not having worked.
        moved = sinks.move_streams_to(sink)
        if log:
            also = f", and moved {moved} stream(s) to it" if moved else ""
            log("info", f"[Audio] '{device.label}' is connected - it is now "
                        f"the default output{also}.")
        if announce:
            # What it means, not what it did. "Sound is going to the speaker"
            # is the fact somebody wants; "the default sink was changed and
            # two streams were moved" is the implementation.
            announce("Audio moved", f"Sound is now playing through "
                                    f"{device.label}.")
        return sink
    except Exception as e:
        if log:
            log("debug", f"[Audio] Bluetooth follow failed: {e}")
        return None


def _restore(log=None, announce=None) -> Optional[str]:
    """
    Hand the default back to whatever had it before the speaker arrived.

    Only when this moved it. A default somebody set by hand while no speaker
    was connected is theirs, and putting it back to a remembered value would
    undo a deliberate choice.
    """
    global _previous_default
    if not _previous_default:
        return None

    going_back, _previous_default = _previous_default, ""

    # Gone in the meantime - an unplugged dock, a server restart. Nothing
    # sensible to put it back to, and picking one at random is worse than
    # leaving the server's own choice alone.
    if not any(s.get("name") == going_back for s in sinks.sinks()):
        if log:
            log("debug", f"[Audio] '{going_back}' is no longer here, so the "
                         f"output was left as the system had it.")
        return None

    if sinks.set_default_sink(going_back):
        moved = sinks.move_streams_to(going_back)
        where = sinks.description_for(going_back)
        if log:
            also = f", and moved {moved} stream(s) back" if moved else ""
            log("info", f"[Audio] The Bluetooth speaker is gone - output is "
                        f"back on '{where}'{also}.")
        if announce:
            announce("Audio moved",
                     f"The Bluetooth speaker disconnected. Sound is back on "
                     f"{where}.")
        return going_back
    return None
