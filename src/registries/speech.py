"""
What the panel knows about listening and speaking.

Three facades, held by the service registry and reached as `SERVICES.STT`,
`SERVICES.TTS` and `SERVICES.JUDGE`. Each owns the state and delegates the
work to whatever is currently doing it.

**The state is here rather than inside the implementation because it has to
outlive it.** Whatever is transcribing can be stopped, restarted or replaced;
what the assistant is *doing* is the panel's own idea and should survive all
three. On the speaking side that is not a nicety: the ring of recent replies is
what the echo guard compares against for twenty seconds after a reply, so a
backend swap that took it along would leave the panel deaf to its own voice for
exactly the window it is most likely to hear it.
"""

import time
from contextlib import contextmanager
from threading import RLock

from src.assistant import judge_protocol


class SpeechFacade:
    """
    Listening: what it is doing, and whatever is doing it.

    `source` is the recogniser - `STTProcessing` on a stock panel. It is None
    until the assistant starts and None again once it stops, which is what
    `running` reads and what the Client's own `STT` answers with.
    """

    #What the pill can say. DORMANT is the assistant not running at all, and
    #is its own state rather than an unknown falling through to LIVE - a panel
    #where a model download was declined would otherwise invite somebody to
    #say a wake word nothing is listening for.
    STATES = ("DORMANT", "LIVE", "LISTENING", "THINKING", "ACTING")

    #The capability this is a facade for. Whoever provides it builds the
    #recogniser; a plugin claiming this name replaces it.
    PROVIDER = "assistant.stt"

    def __init__(self, client):
        self.client = client
        self.source = None

        self._status = "DORMANT"
        self._level = 0.0

        # Counted rather than a flag. Two slow things overlap all the time - a
        # reply is still being generated while the previous one is spoken - and
        # a boolean means whichever finishes first puts the pill away while the
        # other is still working.
        self._thinking_depth = 0
        self._thinking_was = "DORMANT"
        self._thinking_lock = RLock()

        # The settings the running assistant was started against, compared on
        # save to decide whether it needs restarting.
        self._config: tuple = ()

    ## -- what is doing the listening

    def attach(self, source) -> None:
        self.source = source

    def detach(self):
        source, self.source = self.source, None
        return source

    @property
    def running(self) -> bool:
        return self.source is not None

    def build(self, **kwargs):
        """
        Make a recogniser from whoever provides `assistant.stt`, and take it.

        Answers with it, or None when nothing provides one - which the caller
        reports alongside every other reason the assistant could not start,
        rather than as a different kind of failure.
        """
        source = self.client.SERVICES.build(self.PROVIDER, self.client, **kwargs)
        if source is None:
            return None
        self.attach(source)
        return source

    def provider(self):
        """Whoever supplies the recogniser now."""
        return self.client.SERVICES.provider(self.PROVIDER)

    ## -- what it is doing

    @property
    def status(self) -> str:
        return self._status

    @status.setter
    def status(self, value: str) -> None:
        self._status = str(value or "DORMANT")

    @property
    def level(self) -> float:
        """Input level while capturing, 0 to 1."""
        return self._level

    @level.setter
    def level(self, value) -> None:
        try:
            self._level = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self._level = 0.0

    def status_snapshot(self) -> dict:
        """
        Everything the recogniser knows, or a stopped answer when there is
        none. A caller that has to check for None first is a caller that
        forgets to.
        """
        if self.source is None:
            return {"state": "stopped", "since": 0.0, "for": 0.0,
                    "running": False, "pid": None, "engine": "", "model": "",
                    "error": "", "listeners": 0}
        return self.source.status()

    @contextmanager
    def thinking(self, why: str = ""):
        """
        Hold the pill at "Thinking…" while something slow runs.

        The status before the first hold is what is restored after the last
        one, so this never invents a state the assistant was not in.
        """
        with self._thinking_lock:
            self._thinking_depth += 1
            first = self._thinking_depth == 1
            if first:
                self._thinking_was = self._status
                self.status = "THINKING"
        if why:
            self.client.log("debug", f"[Assistant] Thinking: {why}")
        try:
            yield
        finally:
            with self._thinking_lock:
                self._thinking_depth = max(0, self._thinking_depth - 1)
                if self._thinking_depth == 0:
                    # Only if nothing else has moved it on since. A wake word
                    # arriving mid-reply means LISTENING is the truth now.
                    if self._status == "THINKING":
                        self.status = self._thinking_was or "LIVE"

    ## -- settings

    @property
    def wake_word(self) -> str:
        """
        The default wake word for skills. Read rather than hardcoded, so
        changing it in Settings changes every skill with it.
        """
        try:
            return str(
                self.client.SETTINGS.assistant.wake.wake_word.value
            ).strip().lower() or "alexa"
        except Exception:
            return "alexa"

    def config(self) -> tuple:
        """
        The settings the running assistant depends on.

        Compared against `remembered()` on every save to decide whether it
        needs restarting. Anything the child reads once, at spawn, belongs in
        here - a setting that only takes effect on a relaunch is a setting
        somebody adjusts once and gives up on.

        **Listening only.** The voice has its own, on `SERVICES.TTS`. One
        tuple for both meant picking a different voice tore down the
        microphone, the model and the wake word with it - several seconds of
        the panel being deaf, to change something that was never listening.
        """
        setting = self.client.setting
        return (
            self.client.assistant_enabled(),
            str(setting("audio.devices.input_device.value", "") or "").strip(),
            str(setting("assistant.speech.model.value", "parakeet-v3")
                or "parakeet-v3"),
            self.wake_word,
            int(setting("assistant.wake.session_silence.value", 800)),
            # The microphone profile. Half of what it changes lives in the
            # child and is fixed when it is spawned - the noise reduction, the
            # VAD aggressiveness, the silence floor - so changing this without
            # a restart leaves the panel half switched: the guards on this side
            # move and the audio pipeline does not.
            str(setting("audio.devices.mic_processing.value", "software")),
            # A different set of weights, so a different download and a
            # different model in the child. Appended rather than inserted:
            # the saved handler reads index 2 for the model.
            str(setting("assistant.speech.parakeet_precision.value", "int8")),
            # How long the child waits for a phrase after a wake. Read once,
            # when it is spawned.
            str(setting("assistant.wake.wake_listen_timeout.value", 12)),
            # The spotter's threshold, fixed when the child is spawned - so
            # moving it in Settings does nothing at all until this restarts. It
            # is a setting somebody adjusts by feel, a step at a time, and one
            # that needs a relaunch between steps does not get adjusted.
            str(setting("assistant.wake.wake_sensitivity.value", 0.5)),
            str(setting("assistant.wake.wake_sensitivity_speaking.value", 0.0)),
        )

    def remembered(self) -> tuple:
        return self._config

    def remember(self, config: tuple = None) -> tuple:
        self._config = tuple(config) if config is not None else self.config()
        return self._config

    ## -- driving it

    def cancel(self, reason: str = "") -> bool:
        """Stop listening and go back to the wake word. No-op when idle."""
        if self.source is None:
            return False
        self.source.cancel(reason)
        return True

    def silence(self, why: str = "") -> bool:
        """
        Stop whatever is being said. Answers whether anything was talking.

        On the RECOGNISER rather than on `TTS`, because stopping a reply is
        only half of it. The other half is the bookkeeping that keeps the
        settle window and the self-hearing grace from swallowing whatever is
        said next, and that state lives here. `TTS.stop_speaking()` is the
        half without it, and is for a caller putting down a reply it owns.
        """
        if self.source is None:
            return False
        try:
            return bool(self.source.silence(why))
        except Exception as exc:
            self.client.log("warning",
                            f"[Assistant] Could not stop the voice: {exc}")
            return False

    def hold_capture(self, held: bool) -> None:
        """
        Stop capturing while the panel talks, and start again after.

        Best effort by design: the recogniser may be mid-restart, and a reply
        that goes out while it is means the text guards catch the echo instead.
        """
        if self.source is None:
            return
        try:
            self.source.hold_capture(bool(held))
        except Exception:
            pass

    def stop(self) -> None:
        source = self.detach()
        if source is None:
            return
        try:
            source.stop()
        except Exception as exc:
            self.client.log("warning", f"[Assistant] Error stopping STT: {exc}")

    ## -- the interface
    #
    # Written out rather than forwarded with __getattr__, because this list IS
    # the contract. Anything claiming `assistant.stt` has to answer all of it,
    # and a facade that forwards whatever it is asked for would let a
    # replacement look complete right up until something reached for the one
    # method it left out.

    def start(self) -> None:
        """Open the microphone and start listening."""
        if self.source is not None:
            self.source.start()

    def submit(self, phrase: str) -> bool:
        """
        Route a phrase that was typed or sent rather than heard.

        No wake word is expected: a request arriving over the API has already
        said who it is talking to by arriving at all.
        """
        if self.source is None:
            return False
        return bool(self.source.submit(phrase))

    def new_session(self):
        """
        A conversation, for a skill expecting a follow-up.

        None when nothing is listening, so a caller can tell "no answer is
        coming" from "the answer was no" without asking a second question.
        """
        if self.source is None:
            return None
        return self.source.new_session()

    def open_session(self) -> None:
        if self.source is not None:
            self.source.open_session()

    def close_session(self) -> None:
        if self.source is not None:
            self.source.close_session()

    def is_session(self) -> bool:
        return bool(self.source is not None and self.source.is_session())

    @property
    def processing(self) -> bool:
        return bool(self.source is not None and self.source.processing)

    @processing.setter
    def processing(self, value: bool) -> None:
        if self.source is not None:
            self.source.processing = bool(value)

    def start_monitor(self) -> bool:
        """Transcribe everything, with no wake word. For the microphone test."""
        if self.source is None:
            return False
        return bool(self.source.start_monitor())

    def stop_monitor(self) -> None:
        if self.source is not None:
            self.source.stop_monitor()

    def add_listener(self, callback) -> None:
        """Watch every transcript without consuming it."""
        if self.source is not None:
            self.source.add_listener(callback)

    def remove_listener(self, callback) -> None:
        if self.source is not None:
            self.source.remove_listener(callback)

    def note_speech_ended(self) -> None:
        """The panel has finished talking, so it can listen again."""
        if self.source is not None:
            self.source.note_speech_ended()

    def note_interrupted(self) -> None:
        if self.source is not None:
            self.source.note_interrupted()

    def check_wake_timeout(self) -> None:
        if self.source is not None:
            self.source.check_wake_timeout()

    def send_command(self, command: str, retries: int = 10) -> bool:
        if self.source is None:
            return False
        return bool(self.source.send_command(command, retries=retries))


class VoiceFacade:
    """
    Speaking: what was said, who owns the voice, and whatever is saying it.

    `backend` is the TTS - Pocket TTS on a stock panel - and is None when
    replies are turned off or nothing would load.
    """

    #The capability this is a facade for. Whoever provides it answers with the
    #backends to try, in order.
    PROVIDER = "assistant.tts"

    #How many recent replies to keep for the echo check.
    #
    #Several rather than one: once a loop is running the panel is answering
    #itself, so a fragment of the previous reply arrives after the next one has
    #been spoken, and a single slot has been overwritten by then.
    SPOKEN_MEMORY = 4

    def __init__(self, client):
        self.client = client
        self.backend = None
        # How the running backend was named when it was chosen. For pages
        # that have to say which voice is speaking; there is no other way to
        # tell "Pocket TTS" from "Speech at pi:8770" from outside.
        self.label = ""

        self._spoken: list = []
        self._spoken_lock = RLock()
        self._owner = 0

        # The settings the attached backend was built against, compared on
        # save to decide whether the voice - and only the voice - needs
        # rebuilding.
        self._config: tuple = ()

    ## -- what is doing the speaking

    def attach(self, backend, label: str = "") -> None:
        self.backend = backend
        self.label = str(label or "")

    def detach(self):
        backend, self.backend = self.backend, None
        self.label = ""
        return backend

    @property
    def available(self) -> bool:
        return bool(self.backend is not None
                    and getattr(self.backend, "available", False))

    def is_speaking(self) -> bool:
        if self.backend is None:
            return False
        try:
            return bool(self.backend.is_speaking())
        except Exception:
            return False

    def is_audible(self) -> bool:
        """
        Whether sound is actually leaving the speaker.

        `is_speaking()` counts a reply still being synthesised, which is what
        anything asking "is the panel busy talking" wants. This is for the one
        caller that needs "has a person heard any of this yet".

        A backend that does not draw the distinction answers with
        `is_speaking()`, which is the old behaviour and no worse than it.
        """
        if self.backend is None:
            return False
        try:
            asked = getattr(self.backend, "is_audible", None)
            if callable(asked):
                return bool(asked())
            return bool(self.backend.is_speaking())
        except Exception:
            return False

    def start(self) -> None:
        """
        Pick a backend, or report why there is none.

        Every reason is logged rather than only the last: "TTS unavailable" on
        its own does not say whether a package is missing or a key is.
        """
        self.detach()
        if not self.client.setting("audio.speech.tts_enabled.value", True):
            self.client.log("info",
                            "[Assistant] Spoken replies are disabled in settings.")
            return

        tried = []
        # A backend that says "not yet" rather than "never". Kept when nothing
        # is ready, so it can be asked again later.
        waiting = None
        waiting_label = ""

        for label, backend in self.backends():
            try:
                candidate = backend(self.client)
            except Exception as exc:
                tried.append(f"{label}: {exc}")
                continue
            if candidate.available:
                self.attach(candidate, label)
                self.client.log("info", f"[Assistant] Speaking through {label}.")
                return
            tried.append(f"{label}: {candidate.error}")
            if waiting is None and getattr(candidate, "RECOVERS", False):
                waiting, waiting_label = candidate, label

        for reason in tried:
            self.client.log("info", f"[Assistant]   {reason}")

        if waiting is not None:
            # ATTACHED ANYWAY.
            #
            # Something that talks to a server is not ready the instant it is
            # built: a speech process spawned a moment ago has not finished
            # loading its model, and one on another machine may be started
            # after the panel. Discarding it here threw away the only object
            # that knew how to ask again - so the panel stayed silent for the
            # session and said the backend was missing, which was never true.
            #
            # `available` re-checks, and everything reads it before speaking.
            self.attach(waiting, waiting_label)
            self.client.log(
                "info",
                f"[Assistant] {waiting_label} is not ready yet - it will be "
                f"asked again, and replies are silent until it answers.")
            return

        if tried:
            self.client.log("warning", "[Assistant] No voice backend available.")
            self.client.simple_notify(
                "assistant", "Assistant",
                "Voice replies are off - see the log for which backend is "
                "missing what.")

    def backends(self) -> list:
        """
        The backends to try, in order, from whoever provides `assistant.tts`.

        A list rather than one, so a provider that offers several keeps the
        per-backend failure reporting - which is what tells a missing package
        apart from a missing key.
        """
        built = self.client.SERVICES.build(self.PROVIDER, self.client)
        return list(built or [])

    def provider(self):
        """Whoever supplies the backends now."""
        return self.client.SERVICES.provider(self.PROVIDER)

    def play(self, text: str, thread: bool = True, voice: str = "") -> None:
        """Straight to the backend, with none of say()'s guards."""
        if self.backend is not None:
            self.backend.play(text, thread=thread, voice=voice)

    ## -- which voices there are

    def voice_options(self) -> tuple:
        """
        The names a caller may choose from, or `()` when there is no choice.

        Asked of the RUNNING backend, never of a setting. `tts_voice` holds
        Pocket names and means nothing to Deepgram, which reads
        `deepgram_voice`; and a socket backend's voices live on another
        machine and are not in this panel's settings at all. Anything reading
        one setting offers the wrong list for two of the three.

        A backend with no menu to offer says so with `VOICE_CHOICE` and gets
        `()` here - see `SocketTTSProcessing.VOICE_CHOICE`.
        """
        backend = self.backend
        if backend is None or not getattr(backend, "VOICE_CHOICE", False):
            return ()
        try:
            return tuple(str(name) for name in (backend.get_voices() or ()))
        except Exception:
            return ()

    def current_voice(self) -> str:
        """What is speaking now, as the running backend names it."""
        backend = self.backend
        if backend is None:
            return ""
        try:
            asked = getattr(backend, "current_voice", None)
            return str(asked()) if callable(asked) else ""
        except Exception:
            return ""

    def unavailable_reason(self) -> str:
        """
        Why nothing can be said, or "" when something can.

        Three causes look identical from outside - speech turned off, a
        backend that never loaded, and one still coming up - and they send
        somebody looking in three different places.
        """
        if self.available:
            return ""
        if self.backend is None:
            try:
                if not self.client.setting("audio.speech.tts_enabled.value", True):
                    return "Spoken replies are turned off in settings."
                chosen = str(self.client.setting(
                    "audio.speech.tts_backend.value", "auto") or "").strip().lower()
                if chosen == "off":
                    return "The voice backend is set to off."
            except Exception:
                pass
            return "No voice backend loaded."
        return str(getattr(self.backend, "error", "")
                   or "The voice is not ready yet.")

    def billing(self) -> dict:
        """
        What speaking costs, for a backend that charges. `{}` when none does.

        A page offering a voice picker to a phone is offering to spend money
        on a backend billed per character, and it should be able to say so.
        """
        backend = self.backend
        if backend is None or not getattr(backend, "BILLED", False):
            return {}
        try:
            asked = getattr(backend, "usage", None)
            figures = dict(asked()) if callable(asked) else {}
        except Exception:
            figures = {}
        figures["unit"] = "character"
        return figures

    def summary(self) -> dict:
        """Everything a page needs about the voice, in one call."""
        return {
            "available": self.available,
            "reason": self.unavailable_reason(),
            "backend": self.label,
            "voices": list(self.voice_options()),
            "current": self.current_voice(),
            "billing": self.billing(),
        }

    ## -- settings

    def config(self) -> tuple:
        """
        The settings the running voice depends on.

        Its own, separate from the microphone's. The backend is built once
        when the assistant starts, so anything read at that moment belongs
        here or changing it does nothing until something else happens to
        restart the panel.

        Kept apart from `SpeechFacade.config()` because the two have nothing
        to do with each other. One tuple for both meant picking a different
        voice stopped the microphone, the speech process and the wake word
        along with it - several seconds of a deaf panel to change something
        that was never listening.
        """
        setting = self.client.setting
        return (
            bool(setting("audio.speech.tts_enabled.value", True)),
            str(setting("audio.speech.tts_backend.value", "auto")),
            # Where the voice runs, and which machine.
            str(setting("audio.speech.tts_where.value", "local")),
            str(setting("audio.speech.tts_host.value", "")),
            str(setting("audio.speech.tts_port.value", 8770)),
            str(setting("audio.speech.tts_voice.value", "")),
            str(setting("audio.speech.tts_voice_file.value", "")),
            str(setting("audio.speech.tts_language.value", "")),
        )

    def remembered(self) -> tuple:
        return self._config

    def remember(self, config: tuple = None) -> tuple:
        self._config = tuple(config) if config is not None else self.config()
        return self._config

    ## -- what was said

    def note_spoken(self, text: str) -> None:
        """Remember something the panel is about to say."""
        text = str(text or "").strip()
        if not text:
            return
        with self._spoken_lock:
            self._spoken.insert(0, (text, time.time()))
            del self._spoken[self.SPOKEN_MEMORY:]

    def recent_spoken(self) -> list:
        """What the panel has said lately, newest first, as (text, when)."""
        with self._spoken_lock:
            return list(self._spoken)

    ## -- who owns the voice

    def owner(self) -> int:
        """
        The token for the most recent thing said, or 0.

        Held by whoever caused it and handed back to `stop(owner=...)`, so a
        stop applies only while that speech is still the current one. An answer
        panel outlives its own voice: it sits on screen, something else is
        asked, the new answer speaks - and an unconditional stop when the old
        panel times out would cut off a reply that was never its own.
        """
        return int(self._owner or 0)

    def claim(self) -> int:
        if self.backend is None:
            self._owner = 0
            return 0
        try:
            self._owner = int(self.backend.claim())
        except Exception:
            self._owner = 0
        return self._owner

    def stop_speaking(self, owner: int = None) -> bool:
        """
        Stop the voice. Answers whether anything was talking.

        With an `owner`, only if that token still holds the voice - a holder
        that has since been displaced is refused, because the voice it would
        cut off belongs to whatever replaced it. Without one it stops whatever
        is talking, which is what a person asking for silence means.
        """
        if self.backend is None:
            return False
        try:
            if owner is None:
                return bool(self.backend.stop())
            return bool(self.backend.stop(owner=owner))
        except Exception:
            return False

    ## -- saying something

    def say(self, text: str, thread: bool = True, voice: str = "") -> bool:
        """
        Speak. Answers whether a person actually heard it.

        `voice` names one for this sentence only. It is checked against
        `voice_options()` and dropped if it is not one of them: the name
        reaches a backend that may treat it as a path to an audio prompt,
        and a name arriving over the network is not somebody's own typing.

        The answer is what a caller decides on: False means show the message
        instead. Said out loud in the log either way, because silence here has
        three causes that look identical from outside - no text, no backend, or
        a backend that never came up - and a skill that answers and is not
        heard is the hardest failure to place.
        """
        client = self.client
        if not text:
            return False

        voice = str(voice or "").strip()
        if voice and voice not in self.voice_options():
            client.log("warning",
                       f"[Assistant] Ignoring unknown voice {voice[:40]!r}.")
            voice = ""

        if not self.available:
            # `available` on a recoverable backend has just re-asked, so this
            # is current rather than remembered from startup. The reason
            # matters: "missing" and "still coming up" send somebody looking
            # in completely different places.
            if self.backend is None:
                why = "missing"
            else:
                why = str(getattr(self.backend, "error", "")
                          or "not available")
            client.log("warning",
                       f"[Assistant] Nothing said - {why}: {text[:60]!r}")
            return False
        # Muted counts as not said. play() returns early when sounds are off,
        # so calling it and reporting True says "spoken" for a message nobody
        # heard - and anything relying on this to decide whether to SHOW the
        # message instead skips that too.
        if client.sounds_muted():
            client.log("info", f"[Assistant] Not said - sounds are muted: "
                               f"{text[:60]!r}")
            return False

        # Remembered before it is spoken, not after. The microphone is
        # recording while the panel talks, so a fragment of the reply can be
        # finalised and transcribed before play() returns. See
        # STTProcessing.echoed().
        self.note_spoken(text)

        # Capture held for the duration, and released by note_speech_ended()
        # when the backend reports it has finished. Audio never captured cannot
        # come back as a question.
        client.SERVICES.STT.hold_capture(True)

        try:
            client.log("debug", f"[Assistant] Speaking: {text[:80]!r}")
            # Claimed before it starts. Whatever spoke last owns the voice, and
            # anything holding an older token has been displaced.
            self.claim()
            # The pill stays up while the audio is made. A local voice takes a
            # second or two on a long reply, and a silent panel in that gap
            # looks like nothing happened.
            with client.SERVICES.STT.thinking("speaking"):
                self.backend.play(text, thread=thread, voice=voice)
            return True
        except Exception as exc:
            # Released here too. The backend reports the end of speech, and a
            # backend that raised is not going to.
            client.SERVICES.STT.hold_capture(False)
            client.log("warning", f"[Assistant] TTS failed: {exc}")
            return False


class JudgeFacade:
    """
    Whether an utterance was meant for the panel, decided by a model.

    `SERVICES.JUDGE`. The third of these, and the smallest: there is no state
    to outlive the implementation here - one question, one key, no memory
    between them - so this is a facade for the sake of the provider stack
    rather than for the sake of what it holds.

    **`addressed.py` is what happens when this is not there.** The rules are
    free, instant and always available, and they are the answer whenever this
    facade cannot give one. Nothing here is allowed to make the panel worse
    than it is with the judge turned off.
    """

    #The capability this is a facade for. A plugin claiming this name
    #replaces the judge the same way it would replace the voice.
    PROVIDER = "assistant.judge"

    def __init__(self, client):
        self.client = client
        self.backend = None
        # How the running backend was named when it was chosen, for a page
        # or a log line that has to say which one answered.
        self.label = ""
        # What it was built against, compared on save. Empty rather than the
        # current settings: before the first start there is nothing built,
        # and seeding it from settings would make the first save look like
        # nothing had changed.
        self._config = ()

    ## -- what is doing the judging

    def attach(self, backend, label: str = "") -> None:
        self.backend = backend
        self.label = str(label or "")

    def detach(self):
        backend, self.backend = self.backend, None
        self.label = ""
        return backend

    @property
    def available(self) -> bool:
        return bool(self.backend is not None
                    and getattr(self.backend, "available", False))

    def unavailable_reason(self) -> str:
        """Why there is no judge, or "" when there is one."""
        if self.available:
            return ""
        if self.backend is None:
            try:
                chosen = str(self.client.setting(
                    "assistant.wake.judge_backend.value", "off")
                    or "").strip().lower()
                if chosen == "off":
                    return "The judge is turned off in settings."
            except Exception:
                pass
            return "No judge loaded."
        return str(getattr(self.backend, "error", "")
                   or "The judge is not ready yet.")

    ## -- asking it

    def judge(self, text: str, transcript: str = "", wake: str = "",
              in_session: bool = False) -> str:
        """
        `ANSWER`, `IGNORE`, or "" when it could not say.

        Empty is not a third opinion. It means ask something else - and the
        something else is always the rules, at both call sites. A judge that
        is down, slow, or answering nonsense has to leave the panel exactly
        as it would be with no judge at all, because that is the state
        everything was tested in.

        Never raises. This sits on the path a person is waiting on, and an
        exception here would take down the reply rather than the judgement.
        """
        if not self.available:
            return ""

        payload = judge_protocol.request(
            text=text,
            transcript=transcript or text,
            wake=wake,
            in_session=in_session,
            **self._turn_before())

        started = time.time()
        try:
            key = str(self.backend.judge(payload) or "").strip().upper()
        except Exception as exc:
            self.client.log("warning", f"[Judge] {self.label or 'the judge'} "
                                       f"failed: {exc}")
            return ""

        spent = (time.time() - started) * 1000
        if key not in judge_protocol.KEYS:
            # Logged rather than swallowed. A backend answering something
            # else is a backend that has been changed or has broken, and the
            # panel carrying on quietly is how that goes unnoticed for weeks.
            self.client.log("warning",
                            f"[Judge] {self.label or 'the judge'} answered "
                            f"{key[:40]!r}, which is not a key.")
            return ""

        self.client.log("debug",
                        f"[Judge] {key} for {str(text)[:60]!r} "
                        f"in {spent:.0f}ms.")
        return key

    def _turn_before(self) -> dict:
        """
        What the panel last answered, for judging a fragment against it.

        A follow-up is only judgeable against what it follows. "Tuesday" is a
        complete reply to a question asked a moment ago and is nothing on its
        own, and a judge shown the fragment alone would be right to call it
        ambient.
        """
        try:
            entry = self.client.CONTEXT.last
        except Exception:
            entry = None
        if entry is None:
            return {"last_query": "", "last_answer": ""}
        return {"last_query": str(getattr(entry, "query", "") or ""),
                "last_answer": str(getattr(entry, "answer", "") or "")}

    ## -- lifecycle, the same shape as the voice

    def start(self) -> None:
        """
        Pick a backend, or report why there is none.

        **`judge_backend` decides whether the judge runs. The gate setting
        does not.** They govern different things and are asked at different
        places: `assistant.wake.gate_unaddressed` decides whether a phrase
        that matched no skill has to prove it was addressed, and is read by
        `SkillIntentEngine._should_gate()` at that one call site. The judge is
        also consulted INSIDE a conversation, where the gate never applies
        and where the rules are deliberately not asked at all - so tying the
        judge's existence to the gate would take the in-session judge away
        with a setting whose description says nothing about it.
        """
        self.detach()
        try:
            chosen = str(self.client.setting(
                "assistant.wake.judge_backend.value", "off")
                or "").strip().lower()
            if chosen in ("", "off"):
                self.client.log("info", "[Judge] Turned off in settings.")
                return
        except Exception:
            pass

        tried = []
        waiting, waiting_label = None, ""
        for label, backend in self.backends():
            try:
                candidate = backend(self.client)
            except Exception as exc:
                tried.append(f"{label}: {exc}")
                continue
            if candidate.available:
                self.attach(candidate, label)
                self.client.log("info", f"[Judge] Judging with {label}.")
                return
            tried.append(f"{label}: {candidate.error}")
            if waiting is None and getattr(candidate, "RECOVERS", False):
                waiting, waiting_label = candidate, label

        for reason in tried:
            self.client.log("info", f"[Judge]   {reason}")

        if waiting is not None:
            # Kept, for the same reason the voice keeps one: something that
            # talks to a server is not ready the instant it is built, and
            # discarding it throws away the only object that knows how to ask
            # again. `available` re-checks, and `judge()` reads it every time.
            self.attach(waiting, waiting_label)
            self.client.log(
                "info",
                f"[Judge] {waiting_label} is not ready yet - it will be asked "
                f"again, and the rules decide until it answers.")
            return

        if tried:
            self.client.log("info",
                            "[Judge] No judge available - the rules decide.")

    def backends(self) -> list:
        """
        The backends to try, in order, from whoever provides `assistant.judge`.

        A list rather than one, so the per-backend failure reporting survives
        - which is what tells a missing package apart from a missing model.
        """
        try:
            built = self.client.SERVICES.build(self.PROVIDER, self.client)
        except Exception as exc:
            self.client.log("warning",
                            f"[Judge] Could not ask who provides "
                            f"{self.PROVIDER}: {exc}")
            return []
        return list(built or [])

    def provider(self):
        """Whoever supplies the backends now."""
        return self.client.SERVICES.provider(self.PROVIDER)

    def stop(self) -> None:
        backend = self.detach()
        if backend is None:
            return
        try:
            closing = getattr(backend, "stop", None)
            if callable(closing):
                closing()
        except Exception as exc:
            self.client.log("warning", f"[Judge] Error stopping the judge: {exc}")

    ## -- settings

    def config(self) -> tuple:
        """
        The settings this backend was built against.

        Its own tuple, like the other two: a save compares them one at a time,
        so changing the judge rebuilds the judge and leaves the microphone and
        the voice alone.

        `gate_unaddressed` is deliberately NOT in here. It decides whether the
        fallback funnel gates at all, which is a question for the engine at
        that call site rather than a question about what is doing the judging
        - and rebuilding the judge when it moves would take the in-session
        judge down for a setting that has nothing to do with it.
        """
        def setting(path, default=None):
            try:
                return self.client.setting(path, default)
            except Exception:
                return default

        return (
            setting("assistant.wake.judge_backend.value", "off"),
            setting("assistant.wake.judge_where.value", "local"),
            setting("assistant.wake.judge_host.value", ""),
            setting("assistant.wake.judge_port.value",
                    judge_protocol.DEFAULT_PORT),
            setting("assistant.wake.judge_model.value", ""),
            setting("assistant.wake.judge_timeout.value", 1.0),
        )

    def remembered(self) -> tuple:
        return self._config

    def remember(self, config: tuple = None) -> tuple:
        self._config = tuple(config) if config is not None else self.config()
        return self._config

    def summary(self) -> dict:
        """Everything a page needs about the judge, in one call."""
        return {
            "available": self.available,
            "reason": self.unavailable_reason(),
            "backend": self.label,
        }
