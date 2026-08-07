"""
Kyutai Pocket TTS - spoken replies with no key and no network.

A drop-in replacement for `TTSProcessing`: the same constructor, the same
`available` / `error` / `is_speaking()` / `play()` / `stream()`, so
`client.TTS` can be either without anything else in the application knowing
which it got.

The model is 100M parameters and runs on CPU. Kyutai report no speedup from a
GPU at all - batch size is one and the model is tiny - so there is nothing to
configure and nothing to install beyond the package.

    pip install pocket-tts

Two things about it shape the code below. `load_model()` takes seconds, and
`get_state_for_audio_prompt()` takes seconds per voice. Both are done once, on
a worker, and kept in memory; a voice is also cached to a safetensors file so
the second start does not repeat the work.
"""

from __future__ import annotations

import time
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Optional

from src.assistant.speakable import speakable

if TYPE_CHECKING:
    from src.main import Client


#Kyutai's own catalogue name. Any of their voice names, a local wav, or an
#hf:// path also work - see huggingface.co/kyutai/tts-voices.
DEFAULT_VOICE_NAME = "alba"

#Kyutai's published voices. English unless marked.
CATALOGUE = (
    "alba", "anna", "azelma", "bill_boerst", "caro_davy", "charles",
    "cosette", "eponine", "eve", "fantine", "george", "jane", "jean",
    "javert", "marius", "mary", "michael", "paul", "peter_yearsley",
    "stuart_bell", "vera",
    "estelle",    # fr
    "giovanni",   # it
    "juergen",    # de
    "lola",       # es
    "rafael",     # pt
)

#How long play() will wait for the model to finish loading before giving up on
#a phrase. The first utterance after launch is the only one that ever waits.
WARMUP_WAIT = 45.0

#Frames the model generates past the end of the sentence.
#
#Its own guess is 3 for four words or fewer and 1 beyond that, plus 2 - so a
#short reply gets barely any tail and stops the instant the last word does,
#which is what makes "Cleared 3 notifications." sound clipped. Kyutai's own
#french_24l config asks for 8, so a larger number is not unusual.
TAIL_FRAMES_SHORT = 8
TAIL_FRAMES_LONG = 5
#Below this many words a phrase counts as short.
SHORT_PHRASE_WORDS = 6


class PocketTTSProcessing:
    """Pocket TTS behind the same interface as the ElevenLabs backend."""

    DEFAULT_VOICE_NAME = DEFAULT_VOICE_NAME

    def __init__(self, client: "Client"):
        self.client = client
        self.speaking = False
        #Set by stop() and read between chunks of playback.
        self._interrupt = False
        # Who is entitled to stop the voice. Bumped by claim(), which the
        # client calls for every new thing said - so the most recent speaker
        # owns it and anything older has been displaced.
        self._owner = 0
        self._claim_lock = Lock()
        # A reply accepted but not yet playing - see is_speaking().
        self._pending = False
        self.available = False
        self.error = ""

        self.model = None
        self.sample_rate = 24000
        self.voices: dict = {}
        self.voice_ids: dict = {}
        self.names: list = []
        self.default_voice = None

        self._states: dict = {}
        self._lock = Lock()
        #generate_audio() is documented as NOT thread-safe: one model instance
        #cannot serve two calls at once. play() spawns a thread per phrase, so
        #two replies close together would otherwise land inside the model
        #together.
        self._generating = Lock()
        self._ready = Event()

        missing = self._import_error()
        if missing:
            self.error = missing
            return

        # Available means "this can work", not "it is warm yet". The model is
        # loaded on a worker below: doing it here would add seconds to a
        # startup that already has a browser engine and a speech model to get
        # through, and the caller checks `available` immediately.
        self.available = True

        self.voice_name = self._configured_voice()
        # Kyutai's published catalogue, so anything listing voices has a list.
        # Not a restriction: a wav path or an hf:// URL clones instead, and
        # `voice_name` is whatever the setting holds.
        self.names = list(CATALOGUE)
        if self.voice_name not in self.names:
            self.names.insert(0, self.voice_name)
        self.voices = {name: name for name in self.names}
        self.voice_ids = dict(self.voices)
        self.default_voice = self.voice_name

        Thread(target=self._warm_up, name="__pocket_tts_warmup",
               daemon=True).start()

    ## -- setup

    @staticmethod
    def _import_error() -> str:
        try:
            import pocket_tts  # noqa: F401
        except ImportError:
            return ("pocket-tts is not installed. Run "
                    "`pip install pocket-tts` - it needs no key, no network "
                    "and no GPU.")
        except Exception as e:
            return f"pocket-tts could not be imported: {e}"
        return ""

    def _configured_voice(self) -> str:
        """
        The voice from settings, or Kyutai's default.

        A clone file wins over the picker. The picker is an enum so it can only
        hold a catalogue name, and cloning needs a path - keeping them apart
        means the list stays usable without the feature being lost to it.
        """
        try:
            clone = str(self.client.setting(
                "audio.speech.tts_voice_file.value", "") or "").strip()
            if clone:
                return clone
            value = str(self.client.setting(
                "audio.speech.tts_voice.value", "") or "").strip()
        except Exception:
            value = ""
        return value or self.DEFAULT_VOICE_NAME

    def _configured_language(self) -> str:
        """
        The language weights to load, or "" for the model's own default.

        The setting says "default" rather than being blank: it is an enum, and
        an empty option renders as a blank row that cannot be told from a
        rendering fault. It is turned back into "" here so the value never
        reaches load_model() as a language nobody ships.
        """
        try:
            value = str(self.client.setting(
                "audio.speech.tts_language.value", "") or "").strip().lower()
        except Exception:
            return ""
        return "" if value in ("", "default", "auto") else value

    def _cache_dir(self) -> Optional[Path]:
        try:
            path = Path(self.client.DATAPATH) / "pocket_tts_voices"
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            return None

    def _warm_up(self) -> None:
        """Load the model and the default voice, once, off the UI thread."""
        started = time.monotonic()
        try:
            from pocket_tts import TTSModel
            # `language` picks the pretrained weights: english, french, german,
            # portuguese, italian, spanish. Passing None takes the default
            # rather than guessing from the panel's locale, which is not the
            # same question.
            language = self._configured_language()
            self.model = (TTSModel.load_model(language=language) if language
                          else TTSModel.load_model())

            # The model pads short text with spaces to get its token count up -
            # its own comment says it "does not perform well when there are
            # very few tokens", which is exactly a panel's one-line replies.
            # The English config turns this on; others may not.
            try:
                self.model.pad_with_spaces_for_short_inputs = True
            except Exception:
                pass
            self.sample_rate = int(getattr(self.model, "sample_rate", 24000))
            self._state_for(self.voice_name)
        except Exception as e:
            self.available = False
            self.error = f"Pocket TTS failed to load: {e}"
            self.client.log("warning", f"[TTS] {self.error}")
            self._ready.set()
            return

        self._ready.set()
        self.client.log(
            "info", f"[TTS] Pocket TTS ready in "
                    f"{time.monotonic() - started:.1f}s, voice "
                    f"'{self.voice_name}', {self.sample_rate} Hz.")

    def _state_for(self, voice: str):
        """
        The voice state, cached in memory and on disk.

        Building one from an audio prompt is slow. Exported to safetensors it
        is only a kvcache read, so every start after the first is quick.
        """
        with self._lock:
            if voice in self._states:
                return self._states[voice]

        from pocket_tts import export_model_state

        cache = self._cache_dir()
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in voice)
        cached = (cache / f"{safe}.safetensors") if cache else None

        state = None
        if cached is not None and cached.exists():
            try:
                state = self.model.get_state_for_audio_prompt(str(cached))
            except Exception as e:
                # A cache written by an older version of the package is not
                # worth failing over.
                self.client.log("debug",
                                f"[TTS] Ignoring cached voice '{voice}': {e}")
                state = None

        if state is None:
            state = self.model.get_state_for_audio_prompt(voice)
            if cached is not None:
                try:
                    export_model_state(state, str(cached))
                except Exception as e:
                    self.client.log("debug",
                                    f"[TTS] Could not cache voice: {e}")

        with self._lock:
            self._states[voice] = state
        return state

    def _wait_ready(self) -> bool:
        if not self.available:
            return False
        if not self._ready.wait(timeout=WARMUP_WAIT):
            self.client.log("warning", "[TTS] Pocket TTS is still loading; "
                                       "dropping this phrase.")
            return False
        return bool(self.available and self.model is not None)

    ## -- audio

    def get_audio(self, text: str, auto_play: bool = False,
                  voice: str = None, **_ignored):
        """
        PCM for `text`, as a 1D torch tensor.

        `**_ignored` swallows the ElevenLabs-shaped keywords - `voice_id`,
        `model_id`, `format` - so a caller written against that backend does
        not raise here. There is one model and one output format.
        """
        if not self._wait_ready():
            return None

        # Busy from HERE, not from when the speakers start.
        #
        # `speaking` was only true once `_play_audio` began, and generating a
        # sentence takes seconds - so a stop() issued in that window found
        # nothing to stop, returned False, and the reply played in full into
        # a room where the panel had already gone. Closing the answer, saying
        # "stop", tapping the card: all of them lost the race.
        self._pending = True
        # Cleared per REQUEST rather than per playback, so an interrupt
        # raised during generation is still standing when the audio is ready.
        self._interrupt = False
        # Rewritten for speech before the model sees it.
        #
        # An answer is written for the screen: "5 x 3 = 15" is unambiguous
        # there and unreadable aloud, because "=" is not a word and "x" is a
        # letter. Done here rather than at each caller so every backend and
        # every skill gets it without asking.
        spoken = speakable(text)
        if not spoken:
            return None

        try:
            state = self._state_for(voice or self.voice_name)
            # Serialised. Two phrases arriving together is normal - a skill
            # speaking while the assistant acknowledges - and the model cannot
            # take both.
            words = len(spoken.split())
            tail = (TAIL_FRAMES_SHORT if words < SHORT_PHRASE_WORDS
                    else TAIL_FRAMES_LONG)
            with self._generating:
                audio = self.model.generate_audio(state, spoken,
                                                  frames_after_eos=tail)
        except Exception as e:
            self.client.log("warning", f"[TTS] Could not synthesise: {e}")
            self._pending = False
            return None

        self._report_head(spoken, audio)

        # Asked for while it was being made, so it is not wanted now.
        if self._interrupt:
            self.client.log("debug",
                            "[TTS] Stopped before it started - dropped.")
            self._pending = False
            return None

        if auto_play:
            self._play_audio(audio)
        self._pending = False
        return audio

    def _report_head(self, spoken: str, audio) -> None:
        """
        How the generated audio STARTS, before anything plays it.

        The one measurement that separates the two explanations for a reply
        that begins partway through its first word. If the model handed back
        audio whose first sample is already loud, the beginning was never
        generated and no amount of padding or device warm-up will bring it
        back. If it handed back silence and the speaker still starts late,
        the audio was made and something after this dropped it.

        Cheap enough to leave on: one pass over a tenth of a second.
        """
        try:
            import numpy as np
            data = self._as_playable(audio)
            if data is None or not len(data):
                self.client.log("warning", f"[TTS] No audio for {spoken!r}.")
                return
            flat = data if data.ndim == 1 else data[:, 0]
            window = int(self.sample_rate * 0.02)          # 20ms
            loud = np.abs(flat) > 0.01
            lead_ms = (int(np.argmax(loud)) / self.sample_rate * 1000
                       if loud.any() else -1)
            first = float(np.sqrt(np.mean(flat[:window] ** 2))) if len(flat) else 0.0
            self.client.log(
                "info",
                f"[TTS] {len(flat) / self.sample_rate:.2f}s for "
                f"{len(spoken.split())} words; silence before the first sound "
                f"{lead_ms:.0f}ms; first 20ms RMS {first:.4f}")
        except Exception as e:
            self.client.log("debug", f"[TTS] Could not measure the head: {e}")

    def _play_audio(self, audio) -> None:
        """Send PCM to the speakers and block until it has finished."""
        if audio is None:
            return
        from src.assistant import audio as audio_backend
        sd = audio_backend._sd()
        if sd is None:
            self.client.log("warning",
                            "[TTS] No audio output available to speak through.")
            return

        # Already asked to stop before this got the chance to start.
        if self._interrupt:
            self.client.log("debug", "[TTS] Dropped before playback.")
            self._pending = False
            return

        self.speaking = True
        self._pending = True
        try:
            data = self._pad(self._as_playable(audio))
            # A stream of its own, for the same reason the audio registry uses
            # one: sd.play() writes to a single module-level stream, and a tap
            # sound landing while this is speaking reaches into it from another
            # thread. PortAudio answers that with SIGABRT.
            import numpy as np

            channels = 1 if data.ndim == 1 else data.shape[1]
            rate = self._playback_rate()
            # The chosen output, like every other sound the panel makes -
            # see audio.devices.output_device. Without this the assistant speaks
            # through whatever was plugged in last while notification sounds
            # go where they were told, which is worse than either.
            chosen = None
            try:
                chosen = self.client.AUDIO.device_index(
                    str(self.client.setting("audio.devices.output_device.value", "")),
                    "output")
            except Exception:
                chosen = None
            # Inside the routing block, for the reason the audio registry
            # gives: PULSE_SINK is read when the stream is created.
            sink = ""
            try:
                sink = self.client.AUDIO.chosen_sink()
            except Exception:
                sink = ""
            from src.system import sinks as server_sinks
            with server_sinks.routed(sink):
                stream = sd.OutputStream(samplerate=rate,
                                         device=chosen,
                                         channels=channels, dtype="float32")
            try:
                stream.start()
                self.client.log(
                    "debug",
                    f"[TTS] Playing {len(data) / max(1, rate):.2f}s at {rate}Hz "
                    f"on device {chosen}, {channels}ch.")

                # Silence written and WAITED FOR, before the reply.
                #
                # An idle sink is suspended, and resuming it takes long enough
                # that everything written meanwhile is dropped. Padding the
                # buffer does not survive that - the measurements show close
                # to a second of silence already in front of the speech and
                # the first syllable still going missing - because the frames
                # are pulled and discarded while the device wakes.
                #
                # So the device is given something to throw away first, and
                # then a moment to finish waking. `stop()`/`start()` is not
                # used here: the point is to keep the stream running from this
                # instant until the last sample, so nothing can suspend in
                # between.
                warm = int(rate * self.WARMUP_S)
                if warm > 0:
                    shape = (warm,) if data.ndim == 1 else (warm, data.shape[1])
                    stream.write(np.zeros(shape, dtype=data.dtype))
                    time.sleep(self.WARMUP_S)
                # Written in pieces so it can be stopped part way.
                #
                # One write() of the whole reply blocks until every sample has
                # played, which is why the wake word could not interrupt: by
                # the time anything could act on it the sentence had finished
                # anyway. A tenth of a second is short enough to feel immediate
                # and long enough not to underrun.
                step = max(1, int(rate * self.INTERRUPT_STEP))
                # NOT cleared here. This ran after generation, so a stop
                # raised while the sentence was being made was wiped a
                # moment before the loop that would have honoured it -
                # which is the whole window a slow model spends.
                for start in range(0, len(data), step):
                    if self._interrupt:
                        self.client.log("debug", "[TTS] Stopped part way.")
                        break
                    stream.write(data[start:start + step])

                # Let the device finish what it has been handed.
                #
                # sounddevice's own words: close() discards pending buffers
                # "as if abort() had been called", while stop() "waits until
                # all pending audio buffers have been played". write() only
                # queues, so closing straight after the last one threw it
                # away - every reply lost its final syllables and a short one
                # lost most of itself.
                #
                # A single blocking write of the whole buffer used to hide
                # this, which is why it appeared when playback was split up to
                # be interruptible.
                #
                # Skipped when interrupted: somebody who cut the reply short
                # does not want the rest of the buffer played out first.
                if not self._interrupt:
                    try:
                        stream.stop()
                    except Exception:
                        pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass
        except Exception as e:
            self.client.log("warning", f"[TTS] Playback failed: {e}")
        finally:
            # Held briefly after the audio ends: the microphone hears the tail
            # of the panel's own speech.
            time.sleep(0.4)
            self.speaking = False
            self._pending = False
            self._told_stt()

    def _told_stt(self) -> None:
        """
        Tell the STT when the panel stopped talking.

        is_speaking() alone does not protect the assistant: the microphone
        captures during speech, but Whisper only transcribes once it hears
        silence, so the text arrives after speaking has finished. Without this
        the panel answers itself - most visibly in the AI answer panel, which
        holds a session open and reads a long reply into a live microphone.
        """
        try:
            self.client.STT.note_speech_ended()
        except Exception:
            pass

    #How long a suspended sink takes to come back. PipeWire and PulseAudio
    #both idle a sink out, and the first audio after that is written into a
    #device that is not playing yet.
    RESUME_LEAD_MS = 320

    def _padding_ms(self) -> int:
        try:
            return max(0, min(1000, int(self.client.setting(
                "audio.speech.tts_padding_ms.value", 140) or 0)))
        except Exception:
            return 140

    def _pad(self, data):
        """
        Silence either side of the phrase.

        The model stops when the sentence stops, so a one-line reply begins and
        ends hard against whatever the speaker was doing. A short lead-in also
        covers the moment a Bluetooth speaker takes to wake, which otherwise
        eats the first syllable.
        """
        import numpy as np

        milliseconds = self._padding_ms()
        if data is None:
            return data

        # The lead-in is longer than the tail, and not for symmetry.
        #
        # A sink that has gone idle is SUSPENDED, and resuming it takes a few
        # hundred milliseconds during which anything written is dropped. What
        # comes out is a reply that starts partway through its first word -
        # the same audio every time, so it reads as the model truncating
        # rather than the device waking up. The tail only has to cover the
        # speaker's own decay, which is much shorter.
        lead = max(milliseconds, self.RESUME_LEAD_MS)
        if lead <= 0 and milliseconds <= 0:
            return data

        def silence(ms):
            frames = int(self.sample_rate * ms / 1000)
            if frames <= 0:
                return None
            shape = (frames,) if data.ndim == 1 else (frames, data.shape[1])
            return np.zeros(shape, dtype=data.dtype)

        parts = [silence(lead), data, silence(milliseconds)]
        return np.concatenate([p for p in parts if p is not None])

    def _playback_rate(self) -> int:
        """
        The sample rate to play at, which is how speed is changed.

        Pocket TTS has no rate control - there is no parameter for it - so the
        only lever is playing the samples at a different rate. **That moves the
        pitch with the speed**, which is why the default is 1.0 and the range is
        narrow: at 0.95 the drop is hard to notice and the delivery is audibly
        less hurried, while at 0.8 it sounds like a slowed recording, because it
        is one.
        """
        try:
            rate = float(self.client.setting("audio.speech.tts_rate.value", 1.0)
                         or 1.0)
        except Exception:
            rate = 1.0
        rate = max(0.8, min(1.2, rate))
        return int(self.sample_rate * rate)

    @staticmethod
    def _as_playable(audio):
        """
        PCM in the shape sounddevice wants, whichever shape arrived.

        The library disagrees with itself here: `generate_audio`'s docstring
        says `[channels, samples]`, its README says a 1D tensor, and its body
        concatenates stream chunks on dim 0 - which only makes sense for 1D
        chunks. Rather than pick one and be wrong on the next release, both are
        handled: sounddevice wants 1D or `[samples, channels]`, so a
        channels-first array is transposed and anything else passes through.
        """
        data = audio.numpy() if hasattr(audio, "numpy") else audio
        shape = getattr(data, "shape", ())
        if len(shape) == 2 and shape[0] <= 2 < shape[1]:
            # Channels first - two rows and thousands of columns is not
            # thousands of channels.
            data = data.T
        return data

    def _play_tts(self, text: str) -> None:
        self._play_audio(self.get_audio(text))

    def stream_audio(self, text: str, **_ignored) -> None:
        """
        Synthesise and play.

        Named for the ElevenLabs backend's method so `stream()` behaves the
        same. Pocket TTS can stream chunks, but a panel says one short phrase
        at a time - the whole thing is generated faster than it takes to speak,
        and generating it whole avoids a gap mid-sentence if a chunk is late.
        """
        self._play_audio(self.get_audio(text))

    ## -- helpers

    def is_speaking(self) -> bool:
        """
        Whether a reply is on its way out, generating or playing.

        Generation counts. Everything asking this wants to know "is the panel
        in the middle of saying something" - and it is, from the moment the
        text is accepted. Answering False for the seconds a sentence takes to
        synthesise made an answer panel time out over a reply that had not
        started yet.
        """
        return bool(self.speaking or getattr(self, "_pending", False))

    def stop(self, owner: int = None) -> bool:
        """
        Stop speaking now. Returns whether there was anything to stop.

        The reply is abandoned rather than paused: somebody who interrupts is
        not asking for the rest of it later.

        `owner` is a token from `claim()`. Passing one means "stop this only
        if it is still mine" - a caller that has since been displaced is
        refused, because the voice it would be cutting off belongs to
        whatever replaced it. Passing nothing stops unconditionally, which is
        what the wake word and an explicit cancel do: a person interrupting
        outranks whatever is talking.

        The flag is raised whether or not audio is playing. A sentence still
        being generated cannot be interrupted mid-word, but it can be thrown
        away when it arrives, and that is what this asks for.
        """
        if owner is not None and owner != self._owner:
            self.client.log(
                "debug", f"[TTS] Not stopping - token {owner} was replaced "
                         f"by {self._owner}.")
            return False
        if not self.is_speaking():
            return False
        self._interrupt = True
        return True

    def get_voices(self) -> tuple:
        """
        The one voice in use, in the shape the ElevenLabs backend returns.

        Pocket TTS has no account to enumerate. Kyutai publish a catalogue and
        any wav can be cloned, so which voices "exist" is not a question with
        an answer here - the configured one is what there is.
        """
        return dict(self.voices), dict(self.voice_ids)

    #How much audio is written at a time, in seconds. Short enough that a
    #stop lands immediately, long enough not to starve the output.
    INTERRUPT_STEP = 0.1

    #How long the device is given to wake up, with silence, before the reply
    #starts. Written AND waited for - writing alone only queues it, and the
    #queue is what a resuming sink throws away.
    WARMUP_S = 0.45

    ## -- interface

    def claim(self) -> int:
        """
        Take ownership of the voice. Returns the token that now owns it.

        Speaking is a shared, single resource, and the thing that most
        recently started talking is the thing that owns it. Anything holding
        an OLDER token has been displaced and must not silence what replaced
        it.

        The problem this solves: an answer panel stops speech when it closes,
        which is right while it is the thing talking and wrong a minute later.
        A weather answer left on screen, a question asked, the AI fallback
        opening its own panel and beginning to speak - and then the weather
        panel times out, calls stop(), and cuts off a reply that was never
        its own.
        """
        with self._claim_lock:
            self._owner += 1
            return self._owner

    @property
    def owner(self) -> int:
        """The token currently entitled to stop the voice."""
        return self._owner

    def play(self, text: str = None, audio: list = None,
             thread: bool = True) -> None:
        if not self.available:
            return
        # Muted covers speech as well.
        #
        # "No sounds" that still talks is not what anybody means by it, and
        # the assistant is the loudest thing this panel does.
        try:
            if self.client.sounds_muted():
                return
        except Exception:
            pass
        if text:
            if thread:
                Thread(target=self._play_tts,
                       name=f"__tts_thread({str(text)[:10]})",
                       args=[text]).start()
            else:
                self._play_tts(text)
        elif audio is not None:
            if thread:
                Thread(target=self._play_audio, args=[audio]).start()
            else:
                self._play_audio(audio)

    def stream(self, text: str, thread: bool = True) -> None:
        if not self.available:
            return
        if thread:
            Thread(target=self.stream_audio, args=[text]).start()
        else:
            self.stream_audio(text)
