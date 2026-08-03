"""
A player with no window.

A `QWebEnginePage` with no view attached loads and runs scripts exactly as a
visible one does - it simply never paints. That is the whole trick: the page
plays, and the panel shows its own widget rather than a browser.

A page rather than a hidden `QWebEngineView`, because a view is a widget and
would want a place in a layout, a parent, and a size, none of which mean
anything here.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QUrl, QTimer, QObject
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings, QWebEngineProfile

from src.registries.player_registry import (
    NowPlaying, PLAYING, PAUSED, STOPPED, LOADING,
)
from .shell import build as build_shell
from .watch_page import CONTROLS, WATCH_URL

if TYPE_CHECKING:
    from src.main import Client


#YT.PlayerState
YT_UNSTARTED = -1
YT_ENDED = 0
YT_PLAYING = 1
YT_PAUSED = 2
YT_BUFFERING = 3
YT_CUED = 5

STATE_MAP = {
    YT_UNSTARTED: STOPPED,
    YT_ENDED:     STOPPED,
    YT_PLAYING:   PLAYING,
    YT_PAUSED:    PAUSED,
    YT_BUFFERING: LOADING,
    YT_CUED:      PAUSED,
}


class _QuietPage(QWebEnginePage):
    """
    A page that does not narrate.

    YouTube's own pages log a steady stream of console warnings - unused
    preloads, unrecognised permissions policies - none of which this program
    can act on and all of which Qt prints to stderr. A page nobody can see
    reporting problems nobody can fix is noise that buries the lines that do
    matter.

    Genuine errors are kept, at debug, so something breaking is still findable.
    """

    IGNORE = ("was preloaded using link preload",
              "Unrecognized feature",
              "unreachable code",
              "Failed to load resource",
              "was preloaded using link prefetch",
              "sourceURL")

    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        self.log = None

    def javaScriptConsoleMessage(self, level, message, line, source):
        text = str(message or "")
        if any(part in text for part in self.IGNORE):
            return
        if self.log and level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.log("debug", f"[Music] page: {text[:160]}")


class WebPlayer(QObject):
    """Drives the shell page and publishes what it finds into the registry."""

    #how often the page is asked what it is doing
    POLL_MS = 1000
    #Where the shell is fetched from.
    #
    #Served over HTTP by the panel's own backend rather than handed to the
    #page as a string. `setHtml` with a base URL does not give the document a
    #real origin, and the embed checks one - without it every video is
    #refused with error 152, including ones that embed perfectly elsewhere.
    HOST = "127.0.0.1"

    def __init__(self, client: "Client", owner: str, key: str = "youtube"):
        super().__init__()
        self.client = client
        self.owner = owner
        #the backend this publishes as. One plugin registers several, and
        #the registry keeps only the active one's state.
        self.key = key

        self.page: Optional[QWebEnginePage] = None
        self.ready = False
        self._last: Optional[dict] = None
        self._pending_queue: Optional[list] = None
        self._volume = 100
        #error code -> how many videos in this queue failed with it
        self._failures: dict = {}
        #video id -> what the search said about it
        self._known: dict = {}
        #When set, the page is on a watch URL rather than the embed shell and
        #every command goes through HAW instead of HA. See play_directly().
        self.watching = ""
        self._queue: list = []
        self._owners: list = []
        self._index = -1

        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._read_state)

    ## -- lifecycle

    def start(self) -> None:
        """Build the page. Must run on the UI thread."""
        if self.page is not None:
            return

        from src.system import safemode
        if safemode.no_webengine():
            # Nothing plays through this backend with the page absent, which is
            # the point: an embedded browser is the largest thing in the
            # process and the first thing worth ruling out when the panel will
            # not start.
            self.client.log("warning", "[Music] Hidden player page off "
                                       "(HA_NO_WEBENGINE) - YouTube playback "
                                       "is unavailable this run.")
            return

        # Its own profile, off the record. The panel is a shared device in a
        # house and nothing here should be leaving cookies or history behind.
        self._profile = QWebEngineProfile(self)
        self._profile.setHttpUserAgent(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

        # Parented to the PROFILE, not to this object.
        #
        # A profile must outlive every page using it. With both parented here
        # Qt destroys children in the order they were added - the profile
        # first, while the page is still alive - and says so:
        # "Release of profile requested but WebEnginePage still not deleted."
        # A parent destroys its children before itself, so this ordering is
        # guaranteed rather than incidental.
        self.page = _QuietPage(self._profile, self._profile)
        settings = self.page.settings()
        # Without this nothing starts: Chromium requires a click before audio,
        # and there is nothing here to click.
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.ShowScrollBars, False)

        self.page.log = self.client.log
        self.page.loadFinished.connect(self._on_loaded)
        self.page.load(QUrl(self.shell_url()))
        self.client.log("info", f"[Music] Hidden player page loading from "
                                f"{self.shell_url()}")

    def origin(self) -> str:
        from src.backend import PORT
        return f"http://{self.HOST}:{PORT}"

    def shell_url(self) -> str:
        return f"{self.origin()}/public/music_shell"

    def stop(self) -> None:
        self._poll.stop()
        if self.page is not None:
            try:
                # Emptied first so nothing is still decoding while it is torn
                # down, then deleted immediately rather than on the next event
                # loop pass - the profile follows on the line after.
                self.page.setHtml("", QUrl(self.origin()))
                self.page.setParent(None)
                self.page.deleteLater()
            except RuntimeError:
                pass
        self.page = None

        profile, self._profile = getattr(self, "_profile", None), None
        if profile is not None:
            try:
                profile.deleteLater()
            except RuntimeError:
                pass

        self.ready = False
        self.publish(NowPlaying(state=STOPPED))

    def _on_loaded(self, ok: bool) -> None:
        if not ok:
            self.client.log("warning", "[Music] The player page failed to load.")
            return
        if self.watching:
            # A watch page has no shell in it, so the controls are injected.
            self._install_watch_controls()
            self._poll.start()
            self.client.log("info", "[Music] Watch page loaded.")
            return
        # Loaded is not ready: the IFrame API is fetched by the page and the
        # player is built in its callback, so it is polled for rather than
        # assumed.
        self._poll.start()
        self.client.log("info", "[Music] Player page loaded, waiting for the API.")

    ## -- talking to the page

    def _run(self, script: str, callback=None) -> None:
        """
        Run a script in the page, from any thread.

        Marshalled unconditionally. A `QWebEnginePage` may only be touched from
        the UI thread, and the callers are not all on it: ducking is triggered
        from the assistant's own thread and from the update loop, so every
        volume change reached `runJavaScript` from the wrong thread. Qt does
        not raise for that - it aborts the process, which is why this only
        ever showed as a crash while music was playing.
        """
        if self.page is None:
            return

        def go():
            if self.page is None:
                return
            try:
                if callback is None:
                    self.page.runJavaScript(script)
                else:
                    self.page.runJavaScript(script, callback)
            except RuntimeError:
                # The page went while a command was in flight.
                self.page = None

        self.client.call_on_ui(go)

    def _read_state(self) -> None:
        source = "HAW" if self.watching else "HA"
        self._run(f"JSON.stringify({source}.snapshot())", self._on_state)

    def _on_state(self, raw) -> None:
        try:
            snapshot = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return
        if not snapshot.get("ready"):
            return

        if not self.ready:
            self.ready = True
            self.client.log("info", "[Music] Player ready.")
            if self._pending_queue is not None:
                # Something was asked for before the API finished loading.
                queue, self._pending_queue = self._pending_queue, None
                self.load(queue)

        # Said out loud, once each. A queue where every entry refuses to
        # embed ends in silence, and without this there is nothing at all to
        # go on.
        errors = snapshot.get("errors") or []
        refused = [e for e in errors
                   if self._as_int(e.get("code")) in self.EMBED_REFUSED]

        for error in errors:
            code = error.get("code")
            self.client.log("warning", f"[Music] Video "
                                       f"{error.get('id') or '?'} refused to "
                                       f"play ({self.error_reason(code)}).")
            self._failures[int(code) if str(code).isdigit() else 0] = \
                self._failures.get(int(code) if str(code).isdigit() else 0, 0) + 1

        # An embed refusal is not a dead end. The video is fine; the frame is
        # the problem, so the same id is opened on its own watch page instead
        # of a different song being played in its place.
        if refused and not self.watching:
            video_id = refused[0].get("id") or ""
            if video_id:
                self._last = snapshot
                self.client.log("info", f"[Music] {video_id} refuses to be "
                                        f"embedded - opening its watch page.")
                self.play_directly(video_id)
                return

        # Anything else that stalled the player: move on, since the
        # alternatives are why there is a queue.
        if snapshot.get("stalled") and not refused:
            self._last = snapshot
            self.skip()
            return

        if snapshot.get("exhausted") and not (self._last or {}).get("exhausted"):
            self.client.log("info", "[Music] Reached the end of the queue.")
            # A whole queue failing the same way is one problem, not ten. Said
            # once, with what to do about it, rather than leaving somebody to
            # notice the codes are identical.
            if self._failures:
                worst = max(self._failures.items(), key=lambda kv: kv[1])
                code, count = worst
                if count >= 3:
                    self.client.log(
                        "error", f"[Music] Every video in that queue failed "
                                 f"the same way ({self.error_reason(code)}). "
                                 f"{self.advice(code)}")
            self._failures = {}
        if snapshot.get("muted"):
            self.client.log("warning", "[Music] The player is muted.")

        # Said once per advert rather than once per second, so a pre-roll does
        # not fill the log while somebody waits for the song.
        advert = bool(snapshot.get("advert"))
        if advert != bool((self._last or {}).get("advert")):
            self.client.log("info", "[Music] An advert is playing."
                                    if advert else "[Music] Advert over.")

        self._last = snapshot
        self._volume = int(snapshot.get("volume") or 100)
        self.publish(self._as_now_playing(snapshot))

    def _as_now_playing(self, snapshot: dict) -> NowPlaying:
        state = STATE_MAP.get(int(snapshot.get("state", -1)), STOPPED)
        # A watch page has no thumbnail URL in its snapshot, but the id is
        # enough to build one.
        art = snapshot.get("art") or ""
        video_id = (snapshot.get("id") or self.watching
                    or (self._queue[self._index]
                        if 0 <= self._index < len(self._queue) else ""))
        if not art and video_id:
            art = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
        if snapshot.get("advert"):
            # The advert's title is not the song's, and showing it would put a
            # brand on the card where the track should be.
            return NowPlaying(
                title    = "Advert",
                artist   = "",
                art_url  = art,
                state    = LOADING,
                source   = "youtube",
                track_id = video_id,
            )

        # The page first, then what the search knew. A page that reports
        # nothing is common; a search that found nothing is not, since it is
        # how the track was chosen in the first place.
        known = self._known.get(video_id) or {}
        title = (snapshot.get("title") or "").strip() or known.get("title", "")
        artist = self._tidy_artist(snapshot.get("author") or "")
        if not artist:
            artist = self._tidy_artist(known.get("artist", ""))

        return NowPlaying(
            title    = title,
            artist   = artist,
            art_url  = art,
            state    = state,
            position = snapshot.get("position") or 0,
            duration = snapshot.get("duration") or 0,
            source   = "youtube",
            track_id = video_id,
        )

    #what the IFrame API's onError codes mean
    ERRORS = {
        2:   "the id was rejected",
        5:   "the player cannot play it",
        100: "removed or private",
        101: "the owner does not allow embedding",
        150: "the owner does not allow embedding",
        # Undocumented, and the one that matters: the embed did not like where
        # it was being shown. Every video failing with this is an origin
        # problem rather than ten unlucky videos.
        152: "the embed rejected this page's origin",
        153: "the embed got no referrer",
    }

    #what to do about a whole queue failing the same way
    ADVICE = {
        152: "The page is served from the panel's own backend so the embed "
             "has an origin - check the backend is reachable on its port.",
        153: "The page needs to be served over HTTP rather than set as a "
             "string.",
        101: "Nothing to be done - those uploaders block embedding.",
        150: "Nothing to be done - those uploaders block embedding.",
    }

    #Refused *as an embed*, which is not the same as unplayable. The same
    #video plays on its own watch page, so these switch rather than skip.
    EMBED_REFUSED = (101, 150)

    @classmethod
    def advice(cls, code) -> str:
        try:
            return cls.ADVICE.get(int(code), "")
        except (TypeError, ValueError):
            return ""

    @classmethod
    def error_reason(cls, code) -> str:
        try:
            code = int(code)
        except (TypeError, ValueError):
            return "unknown"
        return cls.ERRORS.get(code, f"code {code}")

    @staticmethod
    def _tidy_artist(name: str) -> str:
        """
        'Foo Fighters - Topic' -> 'Foo Fighters'.

        YouTube's auto-generated artist channels carry that suffix, and it is
        on most of what a music search returns.
        """
        name = str(name or "").strip()
        for suffix in (" - Topic", " - Tópico", "VEVO"):
            if name.endswith(suffix):
                return name[: -len(suffix)].strip()
        return name

    def publish(self, playing: NowPlaying) -> None:
        self.client.PLAYER.publish(self.key, playing)

    @staticmethod
    def _as_int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _load(self, url: str) -> None:
        """Point the page somewhere, from any thread."""
        def go():
            if self.page is None:
                return
            try:
                self.page.load(QUrl(url))
            except RuntimeError:
                self.page = None
        self.client.call_on_ui(go)

    ## -- the watch page

    def play_directly(self, video_id: str) -> None:
        """
        Open a video on its own watch page.

        For the videos an embed refuses. A browser on youtube.com is not an
        embed, so the restriction does not apply - and the HTML5 video element
        is a web standard, which is why this does not depend on YouTube's
        markup the way clicking its buttons would.
        """
        if self.page is None:
            return
        self.watching = str(video_id)
        self.ready = False
        self.client.log("debug", f"[Music] Loading the watch page for "
                                 f"{video_id}.")
        # Marshalled for the same reason as _run: a skill calls next() from
        # its own thread, and loading a page from there aborts Qt.
        self._load(WATCH_URL.format(video_id=self.watching))

    def return_to_shell(self) -> None:
        """Back to the embed player, for the next ordinary request."""
        if self.page is None or not self.watching:
            return
        self.watching = ""
        self.ready = False
        self._load(self.shell_url())

    def _install_watch_controls(self) -> None:
        self._run(CONTROLS)

    def skip(self) -> None:
        """The next thing in the queue, from Python rather than the page."""
        if self._index + 1 >= len(self._queue):
            self.client.log("info", "[Music] Nothing else in the queue.")
            self.publish(NowPlaying(state=STOPPED))
            return
        self._index += 1
        self.at(self._index)

    def at(self, index: int) -> None:
        if not (0 <= index < len(self._queue)):
            return
        self._index = index
        video_id = self._queue[index]
        if self.watching:
            self.play_directly(video_id)
            return
        self._run(f"HA.at({int(index)})")

    ## -- commands

    def load(self, video_ids: list, start_at: int = 0,
             owners: list = None, known: list = None) -> None:
        ids = [str(v) for v in (video_ids or []) if v]
        if not ids:
            return
        owners = [str(o or "") for o in (owners or [])]
        owners = (owners + [""] * len(ids))[:len(ids)]
        # Kept on this side too, so a watch-page fallback can walk the queue
        # without the shell - the shell is not loaded while that is happening.
        self._queue, self._owners = ids, owners
        self._index = int(start_at)
        # What the search knew, kept per id. A page does not always report the
        # artist - the watch page has to be scraped for it, and a scrape that
        # misses leaves the card with a title and nothing else - and the search
        # already knew, so throwing it away was the only reason it was blank.
        self._known = {video_id: dict(meta or {})
                       for video_id, meta in zip(ids, (known or []))}

        if self.watching:
            # Coming back from a watch page: the shell has to be reloaded
            # before it can be told anything.
            self.return_to_shell()
            self._pending_queue = ids
            return
        if not self.ready:
            # Held rather than dropped: a spoken request arriving during the
            # first few seconds after boot should still play.
            self._pending_queue = ids
            self.client.log("debug", "[Music] Queued until the player is ready.")
            return
        self._run(f"HA.load({json.dumps(ids)}, {int(start_at)}, "
                  f"{json.dumps(owners)})")
        # Unmuted and set to the wanted level on every load. A player left
        # muted plays perfectly and silently, which is the hardest kind of
        # "it does not work" to find.
        source = "HAW.volume" if self.watching else "HA.wake"
        self._run(f"{source}({int(self._volume)})")

    def play(self) -> None:
        # HA.play rather than playVideo: after a track ends, the embed's end
        # screen has its own opinion about what comes next, and resuming
        # through the page took it. Replaying what finished is the only thing
        # pressing play on a stopped player can reasonably mean.
        self._run("HAW.play()" if self.watching else "HA.play()")

    def pause(self) -> None:
        self._run("HAW.pause()" if self.watching else "HA.player && HA.player.pauseVideo()")

    def stop_playing(self) -> None:
        self._run("HAW.stop()" if self.watching else "HA.player && HA.player.stopVideo()")

    def next(self) -> None:
        self.skip() if self.watching else self._run("HA.next()")

    def previous(self) -> None:
        self.at(max(0, self._index - 1)) if self.watching else self._run("HA.previous()")

    def seek(self, seconds: float) -> None:
        if self.watching:
            self._run(f"HAW.seek({float(seconds)})")
            return
        self._run(f"HA.player && HA.player.seekTo({float(seconds)}, true)")

    def volume(self, percent: int = None):
        if percent is None:
            return self._volume
        percent = max(0, min(100, int(percent)))
        self._volume = percent
        if self.watching:
            self._run(f"HAW.volume({percent})")
        else:
            self._run(f"HA.player && HA.player.setVolume({percent})")
        return None

    def fade(self, percent: int, ms: int = 250) -> None:
        level = max(0, min(100, int(percent)))
        source = "HAW" if self.watching else "HA"
        self._run(f"{source}.fade({level}, {int(ms)})")

    def toggle(self) -> None:
        state = (self._last or {}).get("state", -1)
        if state == YT_PLAYING:
            self.pause()
        else:
            self.play()
