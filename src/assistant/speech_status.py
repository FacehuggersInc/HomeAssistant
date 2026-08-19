"""
Status entries for the panel's own speech, driven from one place.

Speech recognition and speech itself are the two things the panel does that
take time and have nothing to show for it. Both already know what they are
doing - `STT.status()` and `TTS.is_speaking()` - and neither says so anywhere
somebody can see without going looking.

This watches both and keeps `client.STATUS` in step. It lives here rather
than inside either of them because the two are one story: the panel is not
listening while it is speaking, and an icon for each that disagreed about
that would be worse than none.

## Why a poll

Neither side emits anything when it changes state. Adding signals to both
would mean touching the audio path in two processes to fix a display, and a
display is the one thing that can afford to be a fraction of a second late.
The timer is cheap and only asks two objects for a value they already hold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QObject, QTimer

if TYPE_CHECKING:
    from src.main import Client


#How often to look. Fast enough that an icon appears while somebody is still
#waiting for the thing it stands for, slow enough to be free.
INTERVAL_MS = 400

#What each speech-recognition state looks like. Nothing for the resting ones:
#a panel that is idle, or listening for its wake word, is a panel doing what
#it always does - and a row that is never empty is a row nobody reads.
STT_ICONS = {
    "processing":  ("mdi.text-recognition", "accent"),
    "awake":       ("mdi.microphone", "accent"),
    "monitoring":  ("mdi.record-circle-outline", "warm"),
    "held":        ("mdi.microphone-off", "muted"),
    "error":       ("mdi.microphone-off", "bad"),
    "stopped":     ("mdi.microphone-off", "bad"),
}

#And speaking, which is one state.
TTS_ICON = ("mdi.volume-high", "accent")


class SpeechStatus(QObject):
    """
    Keeps two status entries in step with what speech is doing.

    Started by the client once it has both. Holds its own handles rather than
    reaching into the registry by key, so nothing else can end an entry this
    is still updating.
    """

    STT_KEY = "speech.listening"
    TTS_KEY = "speech.speaking"

    def __init__(self, client: "Client"):
        super().__init__()
        self.client = client
        self._stt: Optional[object] = None
        self._tts: Optional[object] = None
        self._last_stt: str = ""
        self._speaking: bool = False

        self._timer = QTimer(self)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        """
        Everything this put up, taken down.

        Called when the panel is going. An icon left behind by something that
        has stopped running is worse than no icon: it says the panel is busy
        with something that no longer exists.
        """
        self._timer.stop()
        for handle in (self._stt, self._tts):
            if handle is not None:
                try:
                    handle.stop()
                except Exception:
                    pass
        self._stt = self._tts = None

    ## -- colours

    #The panel's own four, by the names this file uses. Written here rather
    #than taken from COLORS: the Qt palette has BG, BORDER and TEXT and no
    #accent or warning of its own - those live in the web chrome, and the
    #quick settings panel already writes the same green literally.
    COLOURS = {
        "accent": "#2ff08e",
        "warm":   "#ffb454",
        "bad":    "#ff7a7a",
    }

    def _colour(self, name: str) -> str:
        from src.styling import COLORS

        return self.COLOURS.get(name, COLORS.DARK.TEXT.MUTED)

    ## -- the poll

    def _tick(self) -> None:
        try:
            self._update_stt()
            self._update_tts()
        except Exception as e:
            # A display must not be able to stop the panel. Reported once
            # per occurrence at debug, not raised.
            self.client.log("debug", f"[Status] Speech poll failed: {e}")

    def _update_stt(self) -> None:
        stt = getattr(self.client, "STT", None)
        status = getattr(stt, "status", None)
        state = status().get("state", "") if callable(status) else ""

        if state == self._last_stt:
            return
        self._last_stt = state

        look = STT_ICONS.get(state)
        if look is None:
            # Idle or listening: nothing worth an icon. Hidden rather than
            # stopped, because this comes back every few seconds and an entry
            # that is started and stopped that often is churn for nothing.
            if self._stt is not None:
                self._stt.hide()
            return

        icon, colour = look
        if self._stt is None:
            self._stt = self.client.STATUS.start(icon, self.STT_KEY, "client",
                                                 colour=self._colour(colour))
            return
        self._stt.set(icon=icon, colour=self._colour(colour))
        self._stt.show()

    def _update_tts(self) -> None:
        tts = getattr(self.client, "TTS", None)
        asked = getattr(tts, "is_speaking", None)
        speaking = bool(asked()) if callable(asked) else False

        if speaking == self._speaking:
            return
        self._speaking = speaking

        if not speaking:
            if self._tts is not None:
                self._tts.hide()
            return

        icon, colour = TTS_ICON
        if self._tts is None:
            self._tts = self.client.STATUS.start(icon, self.TTS_KEY, "client",
                                                 colour=self._colour(colour))
        else:
            self._tts.set(icon=icon, colour=self._colour(colour))
            self._tts.show()
