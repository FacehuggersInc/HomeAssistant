"""
Speech from another machine.

The same interface as the local voice, so nothing above it knows the
difference: `available`, `claim()`, `play()`, `stop()`, `is_speaking()`,
`is_audible()`. What changes is where the work happens.

**Why this exists.** A neural voice holds a CPU for a second or two per
sentence. In the panel's own process that starves the window, the web server
and the microphone reader for the whole of it, and no amount of threading
helps - one interpreter, one lock. Worse, nothing can interrupt a model
mid-inference, so a wake word during generation had no effect at all.

Over a socket both problems go away for free. The panel does no synthesis, and
cancelling is a message on a second connection rather than a flag the model
never looks at.

**Playback begins on the first chunk.** The far end streams while it
generates, so a long sentence starts being heard before it has finished being
made - and the check for an interruption happens between chunks, which is as
close to instant as playback allows.
"""

from __future__ import annotations

import socket
import time
from threading import Lock, Thread
from typing import TYPE_CHECKING

from src.assistant.tts_protocol import (DEFAULT_PORT, ProtocolError, read_frame,
                                        read_line, send_line)

if TYPE_CHECKING:
    from src.main import Client


class SocketTTSProcessing:
    """A voice on another machine, reached over TCP."""

    #How long to wait for each stage. Connecting is quick or it is wrong;
    #`say` answers before the model runs, so it is quick too. Streaming has to
    #cover the model, which is the slow part and the reason this is remote.
    CONNECT_TIMEOUT = 4.0
    COMMAND_TIMEOUT = 10.0
    STREAM_TIMEOUT = 60.0

    #Checked once when the backend is built. A panel that cannot reach the
    #machine should say so at startup rather than the first time somebody
    #speaks to it.
    HELLO_TIMEOUT = 3.0

    #Read by VoiceFacade.start(). Says that a "no" from this backend may
    #become a "yes" without anything being rebuilt, so it is worth keeping
    #rather than discarding - a server on this machine is still loading its
    #model when the panel asks, and one on another machine may be started
    #after the panel was.
    RECOVERS = True

    #How long to wait before asking again after a failure.
    #
    #A server on this machine is still loading its model when the assistant
    #starts, and a remote one may be restarted long after the panel was. An
    #error stamped once and kept for ever would mean the panel had to be
    #restarted every time the far end was - which is the wrong way round.
    RETRY_EVERY = 8.0

    def __init__(self, client: "Client", host: str = "", port: int = 0):
        self.client = client
        self.error = ""
        self.speaking = False
        self._retry_at = 0.0

        self._pending = False
        self._interrupt = False
        self._owner = 0
        self._claim_lock = Lock()
        self._speak_lock = Lock()

        # Given, or read from settings. The subprocess mode passes them in:
        # it knows it is 127.0.0.1 and does not want a host somebody typed for
        # a machine that is not this one.
        self.host = str(host or client.setting("audio.speech.tts_host.value",
                                               "") or "").strip()
        try:
            self.port = int(port or client.setting(
                "audio.speech.tts_port.value", DEFAULT_PORT) or DEFAULT_PORT)
        except (TypeError, ValueError):
            self.port = DEFAULT_PORT
        self.voice = str(client.setting("audio.speech.tts_voice.value", "")
                         or "").strip()

        self.rate = 24000
        self._voices: tuple = ()
        self._hello()

    ## -- reachability

    @property
    def available(self) -> bool:
        """
        Whether it can speak, asking again if it could not last time.

        Not a value settled at startup. The server on this machine spends its
        first seconds loading a model, and a remote one can be restarted at
        any point - so an answer of no is re-checked rather than kept.
        """
        if not self.error:
            return True
        if self.is_speaking():
            return False
        now = time.time()
        if now < self._retry_at:
            return False
        self._retry_at = now + self.RETRY_EVERY
        self._hello(quiet=True)
        return not self.error

    def _hello(self, quiet: bool = False) -> None:
        """
        Ask the far end whether it is ready, once, at startup.

        Said now rather than discovered later. "The voice does not work" is a
        setting somebody got wrong nine times out of ten, and finding out at
        the moment of the first reply means finding out in silence.
        """
        if not self.host:
            self.error = ("No address for the speech machine. Set "
                          "Settings -> Audio -> Speech -> host.")
            return
        try:
            answer = self._ask({"cmd": "status"}, timeout=self.HELLO_TIMEOUT)
        except Exception as exc:
            self.error = (f"{self.host}:{self.port} did not answer ({exc}). "
                          f"{self._where_to_look()}")
            return
        if not answer.get("ok"):
            self.error = str(answer.get("reason") or "It refused to talk.")
            return
        if not answer.get("ready"):
            self.error = (f"{self.host}:{self.port} is running but has no "
                          f"voice loaded: {answer.get('error') or 'no reason given'}")
            return
        self.error = ""
        self.rate = int(answer.get("rate") or self.rate)
        self.client.log("info", f"[TTS] Speaking through {self.host}:"
                                f"{self.port} at {self.rate} Hz.")

    def _where_to_look(self) -> str:
        """
        What to check, which is a different thing on the loopback.

        "Is tts-socket-process.py running there?" is the right question about
        another machine and the wrong one about this one, where the panel
        started it a second ago and it is loading a model. Being told to check
        something the panel is responsible for sends somebody looking in the
        wrong place.
        """
        if self.host in ("127.0.0.1", "localhost", "::1"):
            return ("The panel starts it here, so it is most likely still "
                    "loading its model. This will be tried again.")
        return "Is tts-socket-process.py running there?"

    def _connect(self, timeout: float = None):
        sock = socket.create_connection((self.host, self.port),
                                        timeout=timeout or self.CONNECT_TIMEOUT)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock

    def _ask(self, message: dict, timeout: float = None) -> dict:
        """One command, one answer, connection closed. Raises on failure."""
        timeout = timeout or self.COMMAND_TIMEOUT
        sock = self._connect(timeout)
        try:
            send_line(sock, message)
            return read_line(sock, bytearray(), timeout=timeout)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    ## -- what the rest of the panel calls

    #Whether a caller may pick from `get_voices()`.
    #
    #No. The far end answers with the voices it has LOADED so far, not a
    #catalogue - any audio prompt is a voice there, so there is nothing to
    #enumerate. A picker built from it would offer one name on a fresh
    #server and grow as the server was used, which reads as a list of
    #choices and is not one.
    VOICE_CHOICE = False

    def get_voices(self) -> tuple:
        """
        What the far end currently has loaded. Diagnostic, not a menu -
        see VOICE_CHOICE.
        """
        if self._voices:
            return self._voices
        try:
            answer = self._ask({"cmd": "voices"})
        except Exception:
            return ()
        if not answer.get("ok"):
            return ()
        self._voices = tuple(answer.get("voices") or ())
        return self._voices

    def current_voice(self) -> str:
        return str(self.voice or "")

    def claim(self) -> int:
        with self._claim_lock:
            self._owner += 1
            return self._owner

    @property
    def owner(self) -> int:
        return self._owner

    def is_speaking(self) -> bool:
        """Generating or playing - see PocketTTSProcessing.is_speaking()."""
        return bool(self.speaking or self._pending)

    def is_audible(self) -> bool:
        """Only once sound is leaving the speaker."""
        return bool(self.speaking)

    def stop(self, owner: int = None) -> bool:
        """
        Stop now. Answers whether there was anything to stop.

        `owner` means "only if this is still mine" - a caller displaced by
        something newer must not silence what replaced it.
        """
        if owner is not None and int(owner) != self._owner:
            return False
        if not self.is_speaking():
            return False
        # Read by the streaming thread between chunks, and sent to the far end
        # so it stops generating as well. Both, because the two ends are
        # stopping different things: one the playback, the other the work.
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
                   name=f"__tts_socket({str(text)[:10]})", daemon=True).start()
        else:
            self._speak(text, voice)

    def stream(self, text: str, thread: bool = True, voice: str = "") -> None:
        """The same thing. Every reply here is streamed."""
        self.play(text, thread=thread, voice=voice)

    ## -- saying it

    def _speak(self, text: str, voice: str = "") -> None:
        """
        Ask for the audio, then play it as it arrives.

        Serialised on `_speak_lock`: the far end takes one sentence at a time,
        and two overlapping here would interleave two streams into one output
        device.
        """
        with self._speak_lock:
            self._interrupt = False
            self._pending = True
            key = ""
            try:
                key = self._begin(text, voice)
                if key:
                    self._stream(key)
            except Exception as exc:
                self.client.log("warning",
                                f"[TTS] {self.host}:{self.port} - {exc}")
            finally:
                self._pending = False
                self.speaking = False
                if key and self._interrupt:
                    # Told, so it stops generating rather than finishing a
                    # sentence nobody will hear.
                    self._cancel(key)
                self._told_stt()

    def _begin(self, text: str, voice: str = "") -> str:
        # `voice` is for this request only. The far end takes one per `say`,
        # so nothing here has to be put back afterwards.
        answer = self._ask({"cmd": "say", "text": text,
                            "voice": voice or self.voice})
        if not answer.get("ok"):
            self.client.log("warning",
                            f"[TTS] Refused: {answer.get('reason')}")
            return ""
        self.rate = int(answer.get("rate") or self.rate)
        return str(answer.get("key") or "")

    def _cancel(self, key: str) -> None:
        """
        On a connection of its own.

        The streaming connection is carrying audio, so nothing can be said on
        it - which is the entire reason a session key exists.
        """
        try:
            self._ask({"cmd": "cancel", "key": key}, timeout=2.0)
        except Exception:
            pass

    def _stream(self, key: str) -> None:
        sock = self._connect(self.STREAM_TIMEOUT)
        buffer = bytearray()
        try:
            send_line(sock, {"cmd": "stream", "key": key})
            head = read_line(sock, buffer, timeout=self.STREAM_TIMEOUT)
            if not head.get("ok"):
                if not head.get("cancelled"):
                    self.client.log("warning",
                                    f"[TTS] No audio: {head.get('reason')}")
                return
            rate = int(head.get("rate") or self.rate)
            self._play_stream(sock, buffer, rate)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _play_stream(self, sock, buffer, rate: int) -> None:
        """
        Write chunks to the speakers as they arrive.

        The output stream is opened before the first chunk is asked for, so
        the device is awake by the time there is audio for it. The interrupt
        is checked between chunks: that is the finest granularity playback
        offers, and at a fifth of a second a chunk it is close enough to
        instant that somebody talking over the panel hears it stop.
        """
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
        # Inside the routing block for the reason the audio registry gives:
        # PULSE_SINK is read when the stream is created, not when it is used.
        with server_sinks.routed(sink):
            stream = sd.OutputStream(samplerate=rate, device=chosen,
                                     channels=1, dtype="float32")
        began = time.time()
        played = 0
        try:
            stream.start()
            # Asked for by variable above, confirmed here - see
            # sinks.ensure_routed().
            server_sinks.ensure_routed(sink, log=self.client.log)
            # Silence first, so the first word is not eaten by a sink waking
            # up - see audio.wake_output().
            audio_backend.wake_output(stream, self.rate)
            while True:
                if self._interrupt:
                    self.client.log("debug", "[TTS] Interrupted mid-sentence.")
                    break
                chunk = read_frame(sock, buffer, timeout=self.STREAM_TIMEOUT)
                if chunk is None:
                    break
                if not self.speaking:
                    # Audible from the first chunk that reaches the device,
                    # not from the moment the sentence was asked for. Anything
                    # deciding whether to interrupt reads this.
                    self.speaking = True
                stream.write(np.frombuffer(chunk, dtype=np.float32))
                played += len(chunk) // 4
        except (OSError, ProtocolError) as exc:
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
            f"[TTS] Played {played / max(1, rate):.2f}s at {rate}Hz in "
            f"{time.time() - began:.2f}s.")

    def _told_stt(self) -> None:
        """
        Tell the STT the panel stopped talking.

        The same handshake the local voice does, and for the same reason: the
        microphone captures while the panel speaks, and the transcript arrives
        after it has finished. Without this the panel answers itself.
        """
        try:
            self.client.SERVICES.STT.note_speech_ended()
        except Exception:
            pass
