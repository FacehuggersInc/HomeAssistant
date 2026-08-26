"""
Speech from Deepgram's Aura API.

The same interface as every other voice: `available`, `claim()`, `play()`,
`stop()`, `is_speaking()`, `is_audible()`. What changes is that the panel does
no synthesis at all and somebody is being billed per character.

**Billing is what makes this different from the others.** A local model that
fails is silent; one that runs out of credit is silent AND has an account
behind it that somebody has to do something about. So the two failures are
told apart everywhere: a key that is wrong, a machine that cannot be reached,
and an account with nothing left in it are three different messages and only
one of them is fixed by trying again.

Audio comes back as raw linear16 and playback starts on the first bytes, so a
long reply begins being heard while the rest is still arriving.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from threading import Lock, Thread
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.main import Client

#Where the audio comes from, and where the account is.
SPEAK_URL = "https://api.deepgram.com/v1/speak"
PROJECTS_URL = "https://api.deepgram.com/v1/projects"
#The published catalogue. A `tts` array of models, each with a
#`canonical_name` that is the string /v1/speak is asked with.
MODELS_URL = "https://api.deepgram.com/v1/models"

#Only the family this backend speaks with.
#
#The catalogue also carries Deepgram's newer Flux voices, which are served on
#/v2/speak and answer 400 here - so a picker built from the raw list offers
#voices that fail at the first reply, which is the shape of bug this fetch
#exists to stop rather than to introduce.
SPEAKABLE_PREFIX = "aura-"

#The environment variable the key lives in.
SECRET_KEY = "DEEPGRAM_API_KEY"

#Aura's model strings are `[family]-[voice]-[language]`, so the voice on its
#own is not enough to ask with. Kept as the full string.
DEFAULT_MODEL = "aura-2-thalia-en"

#Raw samples, no WAV header.
#
#`container=none` on purpose: with the default the response carries a header
#that has to be found and stepped over before the samples start, and playback
#is meant to begin on the first bytes that arrive.
ENCODING = "linear16"
SAMPLE_RATE = 24000


class DeepgramError(Exception):
    """Something the panel should say out loud rather than retry blindly."""

    def __init__(self, message: str, permanent: bool = False,
                 out_of_credit: bool = False):
        super().__init__(message)
        self.message = message
        # Permanent means asking again changes nothing until a person does
        # something - a wrong key, an empty account. The difference decides
        # whether the panel keeps trying every few seconds or stops.
        self.permanent = permanent
        self.out_of_credit = out_of_credit


class DeepgramTTSProcessing:
    """Aura, over HTTPS, played as it arrives."""

    #A no from this one can become a yes without anything being rebuilt: an
    #account gets topped up, a network comes back. See VoiceFacade.start().
    RECOVERS = True

    CONNECT_TIMEOUT = 6.0
    SPEAK_TIMEOUT = 30.0
    #The account is asked about at startup and then only occasionally. It
    #changes when somebody pays for something, not between sentences.
    BALANCE_EVERY = 900.0
    #How long to wait before asking again after a failure that might pass.
    RETRY_EVERY = 15.0

    #Roughly a fifth of a second at 24kHz, two bytes a sample. Small enough
    #that playback starts promptly, large enough that the read loop is not
    #most of the work.
    CHUNK = 4800 * 2

    def __init__(self, client: "Client"):
        self.client = client
        self.error = ""
        self.speaking = False

        self._pending = False
        self._interrupt = False
        self._owner = 0
        self._claim_lock = Lock()
        self._speak_lock = Lock()
        self._retry_at = 0.0
        # The key this was last checked against. A different one is a reason
        # to look again immediately rather than at the end of the next wait.
        self._checked_key = ""

        self.model = str(client.setting("audio.speech.deepgram_voice.value",
                                        DEFAULT_MODEL) or DEFAULT_MODEL).strip()
        self.rate = SAMPLE_RATE

        # The catalogue as Deepgram published it, or () when it could not be
        # reached. Empty means fall back - see get_voices().
        self.voices: tuple = ()
        # And what to fall back TO, read once, here, before anything has had a
        # chance to move it. `fetch_voices()` writes the catalogue into the
        # same key so the settings dropdown holds it, so a fallback that read
        # that key when it was needed would answer with the fetch - which is
        # not a fallback at all, and looks like one right up until the panel
        # is offline.
        self.fallback: tuple = self._settings_voices()

        # What has been spent since the panel started, in characters, which is
        # the unit Deepgram bills in and reports back on every request.
        self.characters = 0
        self.requests = 0
        self.balance = None
        self.balance_units = ""
        self.project = ""
        self._balance_at = 0.0
        self.out_of_credit = False

        self._hello()

    ## -- the key

    @property
    def api_key(self) -> str:
        try:
            return str(self.client.secret(SECRET_KEY, "") or "").strip()
        except Exception:
            return ""

    @property
    def available(self) -> bool:
        """
        Whether it can speak, asking again if it could not last time.

        An account that was empty an hour ago may not be now, and a network
        that was down may be up. What does NOT get re-asked is a key that was
        rejected: that is a person's job, and hammering an auth endpoint every
        fifteen seconds is a good way to have the key taken away.
        """
        if not self.error:
            return True
        if self.is_speaking():
            return False
        # A key that has changed since it was last checked is worth checking
        # NOW, whatever the previous answer was and however long the wait had
        # left. Somebody pasting a key has just told the panel to try again.
        changed = self.api_key != self._checked_key
        if self._permanent and not changed:
            return False
        now = time.time()
        if not changed and now < self._retry_at:
            return False
        self._retry_at = now + self.RETRY_EVERY
        self._hello(quiet=True)
        return not self.error

    def _hello(self, quiet: bool = False) -> None:
        """
        Check the key and read the balance, without spending anything.

        Listing projects rather than speaking: it costs no characters, it
        proves the key works, and it hands back the project the balance lives
        under. Saying "the voice does not work" at the moment of the first
        reply is saying it in silence.
        """
        self._permanent = False
        self._checked_key = self.api_key
        if not self.api_key:
            # NOT permanent. A key that is absent becomes present the moment
            # somebody pastes one in, which is the most recoverable state
            # there is - and it is the state the panel is in when the backend
            # is chosen before the key is entered, which is the ordinary way
            # round. Only a key that was REFUSED is a person's job.
            self.error = (f"No Deepgram key yet. Paste one into "
                          f"Settings -> Audio -> Speech.")
            return
        try:
            answer = self._ask(PROJECTS_URL)
        except DeepgramError as exc:
            self.error = exc.message
            self._permanent = exc.permanent
            self.out_of_credit = exc.out_of_credit
            return

        projects = answer.get("projects") or []
        if not projects:
            self.error = "That Deepgram key has no projects on it."
            self._permanent = True
            return
        self.project = str(projects[0].get("project_id") or "")
        self.error = ""
        self.out_of_credit = False
        self.fetch_voices()
        self.refresh_balance(force=True)
        if not quiet:
            self.client.log("info",
                            f"[TTS] Deepgram ready as '{self.model}'"
                            f"{self.describe_balance(' - ')}.")

    ## -- the account

    def describe_balance(self, prefix: str = "") -> str:
        if self.balance is None:
            return ""
        return f"{prefix}{self.balance:.2f} {self.balance_units} left"

    def refresh_balance(self, force: bool = False) -> None:
        """
        What is left on the account.

        Asked rarely. It changes when somebody pays for something, not
        between sentences, and a request per reply would be a second round
        trip on the path that is meant to be fast.
        """
        now = time.time()
        if not force and (now - self._balance_at) < self.BALANCE_EVERY:
            return
        if not self.project:
            return
        self._balance_at = now
        try:
            answer = self._ask(f"{PROJECTS_URL}/{self.project}/balances")
        except DeepgramError as exc:
            self.client.log("debug", f"[TTS] Could not read the balance: {exc}")
            return
        balances = answer.get("balances") or []
        if not balances:
            return
        total = 0.0
        units = ""
        for entry in balances:
            try:
                total += float(entry.get("amount") or 0.0)
            except (TypeError, ValueError):
                continue
            units = str(entry.get("units") or units)
        self.balance = total
        self.balance_units = units or "credits"
        if total <= 0:
            self._went_broke("The Deepgram account has no credit left.")

    def _went_broke(self, why: str) -> None:
        """
        Say it once, and stop trying.

        Out of credit is not a network blip: every request from here answers
        the same until somebody pays. Retrying it is noise in the log and a
        panel that is silent for a reason it never explains.
        """
        if self.out_of_credit:
            return
        self.out_of_credit = True
        self.error = why
        self._permanent = True
        self.client.log("error", f"[TTS] {why}")
        try:
            self.client.simple_notify(
                "error", "Assistant",
                "Deepgram has no credit left, so the panel cannot speak. "
                "Top the account up, or set the voice back to local.")
        except Exception:
            pass

    ## -- talking to it

    def _request(self, url: str, body: bytes = None,
                 timeout: float = None) -> "urllib.request.addinfourl":
        request = urllib.request.Request(url, data=body)
        request.add_header("Authorization", f"Token {self.api_key}")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(
            request, timeout=timeout or self.CONNECT_TIMEOUT)

    def _ask(self, url: str) -> dict:
        """A management call, as JSON. Raises DeepgramError."""
        try:
            with self._request(url) as answer:
                return json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise self._from_status(exc)
        except urllib.error.URLError as exc:
            raise DeepgramError(f"Could not reach Deepgram ({exc.reason}).")
        except (ValueError, OSError) as exc:
            raise DeepgramError(f"Deepgram answered with something unreadable "
                                f"({exc}).")

    def _from_status(self, exc: urllib.error.HTTPError) -> DeepgramError:
        """
        An HTTP failure, as something worth saying.

        The three that matter are told apart because only one of them is
        fixed by waiting: a key is a person's job, an empty account is a
        person's job, and everything else may pass on its own.
        """
        detail = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:200]
            detail = json.loads(body).get("err_msg") or body
        except Exception:
            detail = ""

        if exc.code in (401, 403):
            return DeepgramError(
                f"Deepgram refused the key ({exc.code}). Check {SECRET_KEY} "
                f"in Settings -> Secrets.", permanent=True)
        if exc.code == 402:
            return DeepgramError(
                "The Deepgram account has no credit left.",
                permanent=True, out_of_credit=True)
        if exc.code == 429:
            # Slowing down is the answer, not retrying - a retry on a rate
            # limit is what caused the rate limit.
            return DeepgramError("Deepgram is rate limiting the panel.")
        return DeepgramError(
            f"Deepgram answered {exc.code}{' - ' + detail if detail else ''}.")

    ## -- what the rest of the panel calls

    #Whether a caller may pick from `get_voices()`. Aura is a published
    #list of model strings, so yes.
    VOICE_CHOICE = True

    #Every character sent is billed, so a caller offering a choice should
    #say so rather than presenting it as free.
    BILLED = True

    def get_voices(self) -> tuple:
        """
        The voices this panel offers.

        The published catalogue when it could be read, and the list the panel
        started with when it could not - somewhere with no network still has
        to offer something, and the last list this panel knew is the best
        guess available.
        """
        return self.voices or self.fallback

    def _settings_voices(self) -> tuple:
        """
        The list in `deepgram_voice.options` as it stands right now.

        Read from settings rather than held in code so there is one list
        rather than two: the dropdown is built from the same key, and a second
        copy here is a second thing to update and forget. Called once, at
        construction - see `self.fallback`.
        """
        try:
            options = self.client.SETTINGS.audio.speech.deepgram_voice.options
            names = tuple(str(name) for name in options if str(name).strip())
        except Exception:
            names = ()
        return names or (DEFAULT_MODEL,)

    def fetch_voices(self) -> tuple:
        """
        The catalogue, from Deepgram, and into the settings dropdown with it.

        Aura gains voices between releases of this panel, and a list written
        into the template is a list that is wrong by the time somebody reads
        it. `canonical_name` is the exact string `/v1/speak` is asked with, so
        nothing has to be assembled from parts here.

        Failure is not an error, and in particular is not a bad key. A panel
        that cannot reach the catalogue can still speak with every voice it
        already knows, so this logs at debug and leaves `voices` empty for
        `get_voices()` to fall through.
        """
        try:
            answer = self._ask(MODELS_URL)
        except DeepgramError as exc:
            self.client.log("debug",
                            f"[TTS] Could not read the voice catalogue: {exc}")
            return self.voices

        found = []
        for entry in (answer.get("tts") or []):
            try:
                name = str(entry.get("canonical_name") or "").strip()
            except AttributeError:
                continue
            if name.startswith(SPEAKABLE_PREFIX) and name not in found:
                found.append(name)
        if not found:
            self.client.log("debug",
                            "[TTS] The voice catalogue held no Aura voices.")
            return self.voices

        # Sorted, because Deepgram returns them in no particular order and a
        # picker that reshuffles between starts is one nobody can find
        # anything in twice.
        #
        # Plainly, and that is enough to put the newer family first: every
        # aura-2 name has a digit where an aura-1 name has a letter, and a
        # digit sorts first. A key spelling that out would be a line that
        # cannot be wrong.
        found.sort()
        self.voices = tuple(found)
        self._offer_voices(self.voices)
        self.client.log("info",
                        f"[TTS] Deepgram offers {len(self.voices)} voices.")
        return self.voices

    def _offer_voices(self, names: tuple) -> None:
        """
        Put them in the dropdown - see `Client.fill_device_options()`, which
        does the same job for audio devices and for the same reason: the
        settings page reads `options` when it builds the control, so a list
        that only exists in this object is a list nobody can pick from.

        The current voice is kept even when the catalogue does not name it.
        Dropping it rewrites the setting to whatever came first, so a voice
        Deepgram has retired would silently become a different one.
        """
        try:
            setting = self.client.SETTINGS.audio.speech.deepgram_voice
            offered = list(names)
            current = str(getattr(setting, "value", "") or "").strip()
            if current and current not in offered:
                offered.append(current)
            setting.options = offered
        except Exception as exc:
            self.client.log("debug",
                            f"[TTS] Could not offer the voice list: {exc}")

    def current_voice(self) -> str:
        return str(self.model or "")

    def claim(self) -> int:
        with self._claim_lock:
            self._owner += 1
            return self._owner

    @property
    def owner(self) -> int:
        return self._owner

    def is_speaking(self) -> bool:
        return bool(self.speaking or self._pending)

    def is_audible(self) -> bool:
        return bool(self.speaking)

    def stop(self, owner: int = None) -> bool:
        if owner is not None and int(owner) != self._owner:
            return False
        if not self.is_speaking():
            return False
        self._interrupt = True
        return True

    def play(self, text: str = None, audio: list = None,
             thread: bool = True, voice: str = "") -> None:
        if not self.available or not text:
            return
        try:
            if self.client.sounds_muted():
                return
        except Exception:
            pass
        if thread:
            Thread(target=self._speak, args=[text, voice],
                   name=f"__tts_deepgram({str(text)[:10]})",
                   daemon=True).start()
        else:
            self._speak(text, voice)

    def stream(self, text: str, thread: bool = True, voice: str = "") -> None:
        """The same thing. Every reply here is streamed."""
        self.play(text, thread=thread, voice=voice)

    ## -- saying it

    def _speak(self, text: str, voice: str = "") -> None:
        with self._speak_lock:
            self._interrupt = False
            self._pending = True
            try:
                self._fetch_and_play(text, voice)
            except DeepgramError as exc:
                self.error = exc.message
                self._permanent = exc.permanent
                if exc.out_of_credit:
                    self._went_broke(exc.message)
                else:
                    self.client.log("warning", f"[TTS] {exc.message}")
            except Exception as exc:
                self.client.log("warning", f"[TTS] Deepgram failed: {exc}")
            finally:
                self._pending = False
                self.speaking = False
                self._told_stt()

    def _fetch_and_play(self, text: str, voice: str = "") -> None:
        # `voice` is a model string for this request only. Every request
        # names its model, so nothing has to be put back afterwards.
        query = urllib.parse.urlencode({
            "model": voice or self.model,
            "encoding": ENCODING,
            "sample_rate": str(self.rate),
            # No WAV header to step over, so the first bytes are samples.
            "container": "none",
        })
        body = json.dumps({"text": text}).encode("utf-8")
        began = time.time()
        try:
            answer = self._request(f"{SPEAK_URL}?{query}", body,
                                   timeout=self.SPEAK_TIMEOUT)
        except urllib.error.HTTPError as exc:
            raise self._from_status(exc)
        except urllib.error.URLError as exc:
            raise DeepgramError(f"Could not reach Deepgram ({exc.reason}).")

        with answer:
            self._note_usage(answer)
            self._play_stream(answer, began)

    def _note_usage(self, answer) -> None:
        """
        What that sentence cost, from the headers Deepgram sends back.

        `dg-char-count` is the billed unit, so this is the real figure rather
        than a count of the text the panel sent - which differs once
        punctuation and normalisation are taken into account.
        """
        self.requests += 1
        try:
            self.characters += int(answer.headers.get("dg-char-count") or 0)
        except (TypeError, ValueError):
            pass
        # Cheap, and only every so often - see refresh_balance.
        try:
            self.refresh_balance()
        except Exception:
            pass

    def _play_stream(self, answer, began: float) -> None:
        import numpy as np
        from src.assistant import audio as audio_backend

        sd = audio_backend._sd()
        if sd is None:
            self.client.log("warning",
                            "[TTS] No audio output available to speak through.")
            return

        chosen = None
        try:
            chosen = self.client.AUDIO.device_index(
                str(self.client.setting("audio.devices.output_device.value", "")),
                "output")
        except Exception:
            chosen = None
        sink = ""
        try:
            sink = self.client.AUDIO.chosen_sink()
        except Exception:
            sink = ""

        from src.system import sinks as server_sinks
        with server_sinks.routed(sink):
            stream = sd.OutputStream(samplerate=self.rate, device=chosen,
                                     channels=1, dtype="float32")
        played = 0
        try:
            stream.start()
            # Asked for by variable above, confirmed here - see
            # sinks.ensure_routed().
            server_sinks.ensure_routed(sink, log=self.client.log)
            # Silence first, so the first word is not eaten by a sink waking
            # up - see audio.wake_output().
            audio_backend.wake_output(stream, self.rate)
            leftover = b""
            while True:
                if self._interrupt:
                    self.client.log("debug", "[TTS] Interrupted mid-sentence.")
                    break
                chunk = answer.read(self.CHUNK)
                if not chunk:
                    break
                # Samples are two bytes, and a read can end between them.
                # Writing an odd byte count reinterprets every sample after
                # it, which sounds like the voice turning to noise.
                chunk = leftover + chunk
                if len(chunk) % 2:
                    chunk, leftover = chunk[:-1], chunk[-1:]
                else:
                    leftover = b""
                if not self.speaking:
                    # Audible from the first chunk that reaches the device,
                    # not from the moment the sentence was asked for.
                    self.speaking = True
                samples = (np.frombuffer(chunk, dtype=np.int16)
                           .astype(np.float32) / 32768.0)
                stream.write(samples)
                played += samples.size
        except (OSError, ValueError) as exc:
            self.client.log("warning", f"[TTS] The audio stopped: {exc}")
        finally:
            self.speaking = False
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self.client.log(
            "debug",
            f"[TTS] Deepgram played {played / max(1, self.rate):.2f}s in "
            f"{time.time() - began:.2f}s "
            f"({self.characters} characters this session).")

    def _told_stt(self) -> None:
        """Tell the STT the panel stopped talking - see SocketTTSProcessing."""
        try:
            self.client.SERVICES.STT.note_speech_ended()
        except Exception:
            pass

    ## -- for a page to draw

    def usage(self) -> dict:
        return {"characters": self.characters, "requests": self.requests,
                "balance": self.balance, "units": self.balance_units,
                "out_of_credit": self.out_of_credit, "model": self.model,
                "error": self.error}
