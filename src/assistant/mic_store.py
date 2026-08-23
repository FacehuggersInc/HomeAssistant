"""
What has been decided about a particular microphone.

Which capsule of an array feeds the transcriber, and later the filter curve
applied to it. **Not settings**, deliberately, and worth saying why because
`input_device` and `mic_processing` are two lines away in the settings tree
and these look like they belong beside them.

They cannot be chosen from a settings page. Which channel of an array carries
the processed output is answerable only by watching the meters while somebody
talks - on some devices it is channel 0 and on others channel 0 is a bare
capsule with the array's own beamforming bypassed, and nothing about the
device says which. A number box with no meters next to it is a field nobody
can fill in correctly, and offering one is offering the appearance of a
choice. So the panel keeps the value and something with meters sets it.

Keyed by device NAME rather than index, for the reason indices are not
identities: unplug a USB device and every index past it shifts, so a panel
that was told "channel 2" would quietly start taking channel 2 of something
else. A name survives that, and a machine with two microphones keeps a
separate answer for each - a tuned array and a laptop's built-in want nothing
to do with each other's.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from src.constants import APP_NAME, get_data_dir

#What a device is filed under when nothing named it. The system default is a
#real choice rather than a missing one, so it gets a key rather than being
#left out.
DEFAULT_KEY = "__default__"

#How the channels of a multichannel device may be reduced to the one the
#transcriber gets.
#
#`mean` is offered and is rarely the right answer on an untuned array:
#uncorrelated noise adds while speech partially cancels, because the capsules
#are centimetres apart and a wavefront does not reach them at the same
#moment. It is one line and occasionally right on a matched pair, which is
#why it is here and why nothing defaults to it.
MIXES = ("first", "mean")

#Never open more than this many, whatever the device claims.
#
#A loopback or virtual device can report thirty-two inputs, and opening all of
#them costs a copy of every window per channel for capsules that do not exist
#in the room. Eight covers every microphone array a panel is likely to have.
MAX_CHANNELS = 8

DEFAULT_ENTRY = {
    "stt_channel": 0,
    "mix": "first",
    #Whether the filter also runs on the live feed, before the VAD and the
    #wake spotter see it.
    #
    #OFF, and it has to be. openWakeWord is trained on unprocessed audio, so a
    #curve that helps the transcriber is not automatically neutral for the
    #word that starts everything - and a panel that stops answering to its
    #name is a far worse failure than one that transcribes slightly worse.
    #Anyone turning this on should re-run the wake report and compare rather
    #than trust that it sounds better.
    "dsp_stream": False,
}


def normalise(raw: dict = None) -> dict:
    """
    Any dict, folded into one that can be acted on.

    Read from a file somebody may have edited and from whatever a plugin hands
    over, neither of which is trusted to be the right shape. A channel index
    is clamped to something sane HERE and to what the device actually offers
    later, when there is a device to compare it against.
    """
    raw = raw if isinstance(raw, dict) else {}

    try:
        channel = int(raw.get("stt_channel", DEFAULT_ENTRY["stt_channel"]))
    except (TypeError, ValueError):
        channel = DEFAULT_ENTRY["stt_channel"]

    mix = str(raw.get("mix", DEFAULT_ENTRY["mix"]) or "").strip().lower()

    entry = {
        "stt_channel": min(max(0, channel), MAX_CHANNELS - 1),
        "mix": mix if mix in MIXES else DEFAULT_ENTRY["mix"],
        "dsp_stream": bool(raw.get("dsp_stream",
                                   DEFAULT_ENTRY["dsp_stream"])),
    }

    # Carried rather than understood. The curve is `mic_dsp.py`'s business and
    # this file is only where it lives; validating it here would be a second
    # opinion on a shape that already has an owner, and the two would drift.
    #
    # Note the split: WHETHER the filter runs on the live feed is above,
    # because that is a decision about the panel's wake word rather than about
    # the curve, and the two are separately switchable on purpose.
    profile = raw.get("dsp")
    if isinstance(profile, dict):
        entry["dsp"] = profile
    return entry


class MicStore:
    """
    The file, and one lock around it.

    Written from whichever thread a plugin happened to call from and read on
    the way to spawning the child, so the two need keeping apart. The lock is
    this object's; two `MicStore`s over one path would not see each other,
    which is why the facade keeps one.
    """

    def __init__(self, log=None, path: Path = None):
        self.log = log
        self.path = Path(path) if path else (
            get_data_dir(APP_NAME) / "audio" / "microphone.json")
        self._lock = threading.RLock()
        self.entries: dict = {}
        self.load()

    ## -- disk

    def _say(self, level: str, message: str) -> None:
        if callable(self.log):
            try:
                self.log(level, message)
                return
            except Exception:
                pass

    def load(self) -> None:
        with self._lock:
            self.entries = {}
            try:
                if not self.path.is_file():
                    return
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                # Not fatal, and not silent. A file that will not parse means
                # the panel is about to listen on channel 0 with no filter,
                # which is a working panel and not the one somebody set up.
                self._say("warning",
                          f"[Microphone] Could not read {self.path}: {exc}")
                return
            if not isinstance(raw, dict):
                return
            for key, entry in raw.items():
                if isinstance(entry, dict):
                    self.entries[str(key)] = normalise(entry)

    def save(self) -> bool:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Written beside and moved over, so a panel losing power
                # mid-write leaves the previous answer rather than half of
                # the new one.
                temporary = self.path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(self.entries, indent=2, sort_keys=True),
                    encoding="utf-8")
                temporary.replace(self.path)
                return True
            except OSError as exc:
                self._say("warning",
                          f"[Microphone] Could not write {self.path}: {exc}")
                return False

    ## -- reading and writing

    @staticmethod
    def key(device_name: str = "") -> str:
        """
        The name a device is filed under.

        "Default" is not a device, it is the absence of a choice, so
        everything that means it lands on one key rather than on whatever the
        system happened to be using when it was written down.
        """
        name = str(device_name or "").strip()
        if name.lower() in ("", "default", "system default"):
            return DEFAULT_KEY
        return name

    def get(self, device_name: str = "") -> dict:
        with self._lock:
            return normalise(self.entries.get(self.key(device_name)))

    def set(self, device_name: str = "", **fields) -> dict:
        """
        Change some of what is known about a device, and keep the rest.

        Fields rather than a whole entry, because the channel and the filter
        are set by different things at different times and a caller that had
        to send both would send a stale copy of whichever it did not care
        about.
        """
        with self._lock:
            key = self.key(device_name)
            entry = normalise(self.entries.get(key))
            for name, value in fields.items():
                if value is not None:
                    entry[name] = value
            entry = normalise(entry)
            self.entries[key] = entry
            self.save()
            return entry

    def forget(self, device_name: str = "") -> bool:
        with self._lock:
            key = self.key(device_name)
            if key not in self.entries:
                return False
            del self.entries[key]
            self.save()
            return True

    def devices(self) -> list:
        """Every device something has been decided about."""
        with self._lock:
            return sorted(self.entries)
