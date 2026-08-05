"""
Sounds the panel can make, by name.

A plugin registers a key against a file in `src/assets/audio` and then asks for
that key. Nothing outside this file needs to know where a sound lives, what
format it is in, or whether it is there at all - which is the point: a panel
with no audio files is a panel that is quiet, not one that crashes.

Playback is on a worker. A timer's alarm repeating for thirty seconds must not
be thirty seconds of frozen screen, and every caller here is on the UI thread.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.main import Client

#Where a registered file is looked for.
#
#`.audio` at the root and out of git, so sounds licensed for personal use are
#never committed or shipped. An empty one means a quiet panel, not a broken one.
AUDIO_DIR = Path(__file__).resolve().parent.parent.parent / ".audio"

#What soundfile will open. Listed rather than "try it and see" so a key
#registered against a .txt is refused when it is registered, not when the timer
#goes off at six in the morning.
SUFFIXES = (".wav", ".flac", ".ogg", ".oga", ".aiff", ".aif", ".mp3")

#A gap between repeats, when the caller does not say. Long enough that two
#plays are heard as two rather than as one longer noise.
DEFAULT_GAP = 0.35

#How long a repeating sound may run if nobody stops it. A sound that repeats
#for ever because a timer widget was destroyed mid-alarm is worse than one that
#stops early.
MAX_DURATION = 300.0


@dataclass
class AudioAsset:
    """
    One registered sound, which may have several recordings behind it.

    Variations exist for the sounds heard most often. A tap that makes exactly
    the same noise a hundred times an hour stops being feedback and becomes a
    tic; three that differ slightly are heard as the panel responding rather
    than as a machine repeating itself.
    """
    key: str
    owner: str
    filenames: list
    volume: float = 1.0
    description: str = ""

    @property
    def filename(self) -> str:
        return self.filenames[0] if self.filenames else ""

    def present(self) -> list:
        """
        The variations that are actually there, whatever they are called.

        A name without an extension matches any format this can open, so
        `tap-1` finds `tap-1.oga` or `tap-1.wav` without the registration
        having to know which. Sounds are downloaded rather than authored, and
        whoever downloads them does not choose the container.

        When both exist the earlier suffix in SUFFIXES wins - an order, not a
        preference for anything in particular, so the answer is the same on
        every launch rather than whatever the filesystem lists first.
        """
        found = []
        for name in self.filenames:
            path = AUDIO_DIR / name
            if path.suffix.lower() in SUFFIXES:
                try:
                    if path.is_file():
                        found.append(path)
                except OSError:
                    pass
                continue

            for suffix in SUFFIXES:
                candidate = AUDIO_DIR / (name + suffix)
                try:
                    if candidate.is_file():
                        found.append(candidate)
                        break
                except OSError:
                    continue
        return found

    def exists(self) -> bool:
        return bool(self.present())

    def choose(self) -> Optional[Path]:
        """
        One of the variations, at random, never the same one twice running.

        Random rather than round-robin: a cycle of three is a pattern, and a
        pattern is the thing variations exist to avoid.
        """
        found = self.present()
        if not found:
            return None
        if len(found) == 1:
            return found[0]
        choices = [p for p in found if p != self._last] or found
        pick = random.choice(choices)
        self._last = pick
        return pick

    _last: Optional[Path] = None


@dataclass
class _Playing:
    """A sound currently making noise, and the way to stop it."""
    key: str
    stop: Event = field(default_factory=Event)
    started: float = field(default_factory=time.time)


class AudioRegistry:
    """
    Named sounds, owned by the client.

    Registration and playback are separate on purpose. A plugin declares what
    it might play when it loads - which is when it knows - and asks for it
    later from wherever the event happens, without carrying a path around.
    """

    def __init__(self, client: "Client"):
        self.client = client
        self.assets: dict[str, AudioAsset] = {}
        self._playing: dict[str, _Playing] = {}
        self._lock = Lock()
        #Keys already complained about, so a missing file is reported once
        #rather than on every tick of a repeating alarm.
        self._warned: set = set()
        self._backend_warned = False

    ## -- registration

    def register(self, owner: str, key: str, filename,
                 volume: float = 1.0, description: str = "") -> bool:
        """
        Declare a sound. Returns False if the key or the name is unusable.

        A file that is not there yet still registers. Sounds are content, and
        content arrives later than the code that plays it - refusing to
        register one would mean a plugin could not declare what it wants until
        somebody had drawn it.
        """
        key = str(key or "").strip().lower()
        # One name or several. A caller with variations should not have to
        # invent keys for them.
        if isinstance(filename, (list, tuple)):
            names = [str(n).strip() for n in filename if str(n).strip()]
        else:
            names = [str(filename or "").strip()]
            names = [n for n in names if n]
        if not key or not names:
            return False
        for name in names:
            suffix = Path(name).suffix.lower()
            # No suffix at all is fine and preferred: it matches whatever
            # format is actually there. A suffix that IS given has to be one
            # this can open, or the name is a mistake rather than a wildcard.
            if suffix and suffix not in SUFFIXES:
                self.client.log("warning",
                                f"[Audio] '{key}' points at {name}, which is "
                                f"not a sound file this can open.")
                return False
        if key in self.assets and self.assets[key].owner != owner:
            self.client.log("warning",
                            f"[Audio] '{key}' is already registered by "
                            f"{self.assets[key].owner}; {owner} is ignored.")
            return False

        self.assets[key] = AudioAsset(
            key=key, owner=owner, filenames=names,
            volume=max(0.0, min(1.0, float(volume))),
            description=str(description or ""))
        return True

    def unregister(self, owner: str) -> int:
        """Drop everything one owner registered, and stop it if it is playing."""
        gone = [k for k, a in self.assets.items() if a.owner == owner]
        for key in gone:
            self.stop(key)
            self.assets.pop(key, None)
        return len(gone)

    def get(self, key: str) -> Optional[AudioAsset]:
        return self.assets.get(str(key or "").strip().lower())

    def keys(self) -> list:
        return sorted(self.assets)

    def missing(self) -> list:
        """Registered keys whose file is not there. For the Info page."""
        return sorted(k for k, a in self.assets.items() if not a.exists())

    ## -- the backend

    def usable(self) -> bool:
        """Whether anything can be played at all."""
        return self._backend() is not None

    #What "follow the system" is called in the settings.
    DEFAULT_DEVICE = "Default"

    def devices(self, direction: str = "output") -> list:
        """
        The audio devices the system can see, for a settings dropdown.

        Named rather than numbered. PortAudio indices are not stable - they
        renumber when anything is plugged in, which is exactly what happens
        with a USB microphone array, so a saved index quietly points at
        something else after a reboot.

        `Default` is always first and always means "whatever the system is
        using now". That is the right answer for most panels and the wrong
        one for a panel with an array that takes the output as well as the
        input, which is why the rest of the list exists.
        """
        found = [self.DEFAULT_DEVICE]
        backend = self._backend()
        if backend is None:
            return found
        sounddevice, _ = backend

        # Outputs, from the sound server rather than from ALSA.
        #
        # PortAudio answers this question at the ALSA layer and the system's
        # own sound settings answer it at the server's, so the two lists share
        # no names - which is a dropdown offering things nobody recognises.
        # Inputs stay on PortAudio: the assistant opens the microphone
        # directly and by index, which is a different job.
        if not str(direction).lower().startswith("in"):
            try:
                from src.system import sinks as server_sinks
                if server_sinks.server_device_index(sounddevice) is not None:
                    for sink in server_sinks.sinks():
                        label = sink.get("description") or sink.get("name")
                        if label and label not in found:
                            found.append(label)
                    if len(found) > 1:
                        return found
            except Exception as e:
                self.client.log("debug",
                                f"[Audio] Could not list sinks, falling back "
                                f"to the ALSA list: {e}")

        # The same filter the assistant already used for its own logging.
        # PortAudio lists ALSA's plugins as devices - resamplers, mixers,
        # channel maps - and every one of them opens without complaining and
        # then behaves in ways nobody chose. A dropdown full of them looks
        # like a list of microphones, and picking the wrong entry is how a
        # panel ends up hearing nothing with no error to show for it.
        try:
            from src.assistant.audio import _HELPER_DEVICES as skip
        except Exception:
            skip = set()

        wants_input = str(direction).lower().startswith("in")
        try:
            for device in sounddevice.query_devices():
                channels = device.get(
                    "max_input_channels" if wants_input
                    else "max_output_channels", 0)
                if not channels:
                    continue
                name = str(device.get("name") or "").strip()
                if not name:
                    continue
                # "samplerate:CARD=..." as well as bare "samplerate".
                bare = name.split(":")[0].strip().lower()
                if bare in skip:
                    continue
                # ALSA's own "default" is what `Default` above already means.
                # Two entries for one thing, one of them capitalised, is a
                # dropdown that looks like it has a trick in it.
                if bare == "default":
                    continue
                if name not in found:
                    found.append(name)
        except Exception as e:
            self.client.log("warning",
                            f"[Audio] Could not list devices: {e}")
        return found

    def sink_for(self, name: str) -> str:
        """
        The server sink behind a chosen output name, or empty.

        Empty covers everything that is not one: `Default`, an ALSA device
        from the fallback list, and a saved name for hardware that is not
        here today.
        """
        wanted = str(name or "").strip()
        if not wanted or wanted == self.DEFAULT_DEVICE:
            return ""
        try:
            from src.system import sinks as server_sinks
            return server_sinks.name_for(wanted)
        except Exception:
            return ""

    def chosen_sink(self) -> str:
        """The sink the settings point at, ready for `sinks.routed()`."""
        try:
            return self.sink_for(
                str(self.client.setting("audio.devices.output_device.value", "")))
        except Exception:
            return ""

    def is_helper(self, name: str) -> bool:
        """
        Whether a name is an ALSA plugin rather than a device.

        Asked about saved settings: an earlier version of the dropdown
        offered these, and one that was chosen has to be undone rather than
        preserved. Unknown names are NOT helpers - they may be hardware that
        is currently unplugged, which is worth keeping.
        """
        try:
            from src.assistant.audio import _HELPER_DEVICES as known
        except Exception:
            return False
        bare = str(name or "").split(":")[0].strip().lower()
        return bare in known or bare == "default"

    def device_index(self, name: str, direction: str = "output"):
        """
        A named device as a PortAudio index, or None for the system default.

        Looked up each time rather than saved. The index is what the audio
        libraries want and the name is what survives a reboot, so the
        translation happens here and the setting never holds a number.
        """
        wanted = str(name or "").strip()
        if not wanted or wanted == self.DEFAULT_DEVICE:
            return None
        backend = self._backend()
        if backend is None:
            return None
        sounddevice, _ = backend

        wants_input = str(direction).lower().startswith("in")

        # A sink is not a PortAudio device. It is reached through the one
        # PortAudio has for the sound server, with the sink chosen per stream
        # - see sink_for() and src/system/sinks.py.
        if not wants_input and self.sink_for(wanted):
            from src.system import sinks as server_sinks
            index = server_sinks.server_device_index(sounddevice)
            if index is not None:
                return index
        try:
            for index, device in enumerate(sounddevice.query_devices()):
                if not device.get("max_input_channels" if wants_input
                                  else "max_output_channels", 0):
                    continue
                if str(device.get("name") or "").strip() == wanted:
                    return index
        except Exception as e:
            self.client.log("warning",
                            f"[Audio] Could not find '{wanted}': {e}")
        # Gone. The system default is a working panel; a stale index is not.
        self.client.log("warning",
                        f"[Audio] '{wanted}' is not connected - using the "
                        f"system default.")
        return None

    def _backend(self):
        """
        (sounddevice, soundfile), or None.

        Imported here rather than at module load: this registry is built during
        startup, and a machine with no PortAudio should get a quiet panel
        rather than a failed launch.
        """
        try:
            import sounddevice
            import soundfile
            return sounddevice, soundfile
        except Exception as e:
            if not self._backend_warned:
                self._backend_warned = True
                self.client.log(
                    "info", f"[Audio] No sound output available, so registered "
                            f"sounds will not play: {e}")
            return None

    ## -- playing

    def play(self, key: str, volume: float = None, repeat: int = 1,
             for_seconds: float = None, gap: float = None) -> bool:
        """
        Make a noise. Returns False when it could not.

        `repeat` plays it that many times; `for_seconds` keeps repeating until
        the time is up, whichever the caller finds easier to say. A key already
        playing is restarted rather than layered - two copies of the same alarm
        a fraction apart is not twice as useful.
        """
        # Muted is muted.
        #
        # Checked here rather than at each caller: a timer, a reminder and a
        # tap all reach this, and one that forgot would be the one sound that
        # goes off at three in the morning.
        try:
            if self.client.sounds_muted():
                return False
        except Exception:
            pass

        asset = self.get(key)
        if asset is None:
            self._warn_once(key, f"[Audio] Nothing is registered as '{key}'.")
            return False
        chosen = asset.choose()
        if chosen is None:
            self._warn_once(
                key, f"[Audio] '{key}' has no file in {AUDIO_DIR}; nothing "
                     f"will play until one is put there.")
            return False
        if self._backend() is None:
            return False

        self.stop(key)

        level = asset.volume if volume is None else max(0.0, min(1.0, float(volume)))
        pause = DEFAULT_GAP if gap is None else max(0.0, float(gap))
        limit = (min(float(for_seconds), MAX_DURATION)
                 if for_seconds else None)
        times = max(1, int(repeat))

        handle = _Playing(key=key)
        with self._lock:
            self._playing[key] = handle

        Thread(target=self._run, name=f"__audio({key})",
               args=(asset, chosen, handle, level, times, limit, pause),
               daemon=True).start()
        return True

    def _run(self, asset: AudioAsset, path: Path, handle: _Playing,
             volume: float, times: int, limit: Optional[float],
             gap: float) -> None:
        backend = self._backend()
        if backend is None:
            return
        sounddevice, soundfile = backend

        try:
            data, rate = soundfile.read(str(path), dtype="float32")
        except Exception as e:
            self._warn_once(asset.key,
                            f"[Audio] Could not read {path}: {e}")
            self._finished(asset.key, handle)
            return

        if volume != 1.0:
            data = data * volume

        played = 0
        started = time.time()
        try:
            while not handle.stop.is_set():
                if limit is None and played >= times:
                    break
                if limit is not None and (time.time() - started) >= limit:
                    break

                # A stream of its own, not sounddevice.play().
                #
                # play() writes to one module-level stream. Two sounds at once
                # - a tap while the assistant is speaking, which is the normal
                # case - therefore reach into the same object from two threads.
                # PortAudio does not survive that, and the way it says so is
                # SIGABRT on an assertion in its own mutex code.
                #
                # Written in blocks rather than in one call so a stop() lands
                # part way through: a timer silenced by pressing Dismiss should
                # go quiet then, not at the end of the current repeat.
                self._write(sounddevice, data, rate, handle)
                played += 1

                if handle.stop.is_set():
                    break
                if not self._sleep(handle, gap):
                    break
        except Exception as e:
            self._warn_once(asset.key, f"[Audio] '{asset.key}' failed: {e}")
        finally:
            # Nothing to stop globally: _write() owns and closes its stream,
            # and calling sounddevice.stop() here would reach into the shared
            # one that this deliberately does not use.
            self._finished(asset.key, handle)

    #How much audio to hand PortAudio at a time, in frames. Small enough that
    #a stop is heard as immediate, large enough not to be a syscall per note.
    BLOCK = 2048

    def _write(self, sounddevice, data, rate: int, handle: _Playing) -> None:
        """One playthrough, in blocks, stoppable between them."""
        channels = 1 if data.ndim == 1 else data.shape[1]
        stream = None
        try:
            # The chosen output, or the system's. A microphone array with a
            # speaker jack takes the default output when it is plugged in,
            # which is how the panel ends up playing through a device nobody
            # chose - see audio.devices.output_device.
            chosen = self.device_index(
                str(self.client.setting("audio.devices.output_device.value", "")),
                "output")
            # Opened inside the routing block: PULSE_SINK is read when the
            # stream is created, not when it is written to, so setting it
            # afterwards would send this one to the old place and only the
            # next one where it was asked to go.
            from src.system import sinks as server_sinks
            with server_sinks.routed(self.chosen_sink()):
                stream = sounddevice.OutputStream(samplerate=rate,
                                                  device=chosen,
                                                  channels=channels,
                                                  dtype="float32")
                stream.start()
            for start in range(0, len(data), self.BLOCK):
                if handle.stop.is_set():
                    break
                stream.write(data[start:start + self.BLOCK])
        finally:
            if stream is not None:
                try:
                    # abort(), not stop(): stop() waits for the buffer to
                    # drain, which is the opposite of what a stop means.
                    if handle.stop.is_set():
                        stream.abort()
                    stream.close()
                except Exception:
                    pass

    @staticmethod
    def _sleep(handle: _Playing, seconds: float) -> bool:
        """Wait, unless asked to stop. False means stop."""
        if seconds <= 0:
            return not handle.stop.is_set()
        return not handle.stop.wait(timeout=seconds)

    def _finished(self, key: str, handle: _Playing) -> None:
        with self._lock:
            if self._playing.get(key) is handle:
                self._playing.pop(key, None)

    ## -- stopping

    def stop(self, key: str) -> bool:
        handle = None
        with self._lock:
            handle = self._playing.get(str(key or "").strip().lower())
        if handle is None:
            return False
        handle.stop.set()
        return True

    def stop_all(self, wait: float = 0.0) -> int:
        """
        Silence everything. `wait` gives the threads a moment to notice.

        Waited on at shutdown, because a playback thread still inside
        PortAudio when the library is torn down aborts the process - the
        assertion in PaUnixMutex_Terminate is a mutex being destroyed while
        somebody still holds it.
        """
        with self._lock:
            handles = list(self._playing.values())
        for handle in handles:
            handle.stop.set()

        if wait > 0 and handles:
            deadline = time.time() + wait
            while time.time() < deadline:
                if not self.is_playing():
                    break
                time.sleep(0.02)
        return len(handles)

    def is_playing(self, key: str = None) -> bool:
        with self._lock:
            if key is None:
                return bool(self._playing)
            return str(key or "").strip().lower() in self._playing

    ## -- helpers

    def _warn_once(self, key: str, message: str) -> None:
        """
        One line per key, not one per attempt.

        A missing file on a repeating alarm would otherwise write a line every
        time round the loop, which buries the rest of the log in the one
        situation somebody is reading it.
        """
        if key in self._warned:
            return
        self._warned.add(key)
        self.client.log("warning", message)
