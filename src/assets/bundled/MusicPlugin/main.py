"""
Music, from a page nobody sees.

The plugin owns three things and joins them up:

* a hidden page that plays (`player.py`)
* a way to turn a phrase into something playable (`search.py`)
* a registration with `client.PLAYER`, so the widget and any other consumer
  never learn that YouTube is involved

Nothing here paints. The now-playing widget reads the registry.
"""

from __future__ import annotations

import time
from pathlib import Path
from threading import Thread

from src.plugin.template import Plugin
from src.mixins import mixin
from src.assistant.skill import Skill
from src.registries.player_registry import NowPlaying, STOPPED

from .player import WebPlayer
from .system_player import SystemPlayer
from .search import search, split_request, comparable
from .aliases import ArtistAliases
from .history import History
from .history_panel import HistoryCard


class MusicPlugin(Plugin):

    KEY = "musicplugin"

    #how often the system is asked what it is playing
    SYSTEM_POLL = 2.0

    def __init__(self):
        self.player: WebPlayer = None
        self.system: SystemPlayer = None
        self._results: list = []
        self._searching = False
        self._last_system = 0.0
        #when the assistant last looked finished, for the duck to settle on
        self._settled_at = 0.0
        #names heard wrongly, and what they turned out to mean
        self.aliases: ArtistAliases = None
        #what has been played, as something to press
        self.history: History = None
        #the last id written, so one track is not recorded on every publish
        self._last_remembered = ""
        #what was said to get here, kept for the history entry
        self._last_asked = ""
        #whether the assistant stopped the music, so only it resumes it
        self._paused_by_us = False

    ## CORE

    def load(self, carryover=None):
        from pathlib import Path
        self.aliases = ArtistAliases(
            Path(self.client.DATAPATH) / "music_artist_aliases.json",
            log=self.client.log)
        if self.aliases.known():
            self.client.log("info", f"[Music] {self.aliases.known()} artist "
                                    f"name(s) learned.")

        self.history = History(
            Path(self.client.DATAPATH) / "music_history.json",
            log=self.client.log)

        self.player = WebPlayer(self.client, self.KEY)
        # Built on the UI thread: a QWebEnginePage is a QObject and Qt is
        # unforgiving about where those are created.
        self.client.call_on_ui(self.player.start)

        # Registered first, so it is the one showing until something is
        # deliberately played here. The panel sits next to whatever else the
        # machine is doing and should show that rather than nothing.
        self.system = SystemPlayer(self.client, self.KEY)
        if self.system.available:
            self.client.PLAYER.register(
                self.KEY, "system", "System audio",
                {
                    "play":     self.system.play,
                    "pause":    self.system.pause,
                    "toggle":   self.system.toggle,
                    "stop":     self.system.stop_playing,
                    "next":     self.system.next,
                    "previous": self.system.previous,
                    "volume":   self.system.volume,
                },
            )
            self.client.log("info", f"[Music] System audio via "
                                    f"{self.system.describe()}.")
        else:
            self.client.log("info", "[Music] No MPRIS tool found - install "
                                    "playerctl to show system audio.")

        self.client.PLAYER.register(
            self.KEY, "youtube", "YouTube",
            {
                "play":     self.play,
                "pause":    self.player.pause,
                "toggle":   self.player.toggle,
                "stop":     self.player.stop_playing,
                "next":     self.player.next,
                "previous": self.player.previous,
                "seek":     self.player.seek,
                "volume":   self.player.volume,
                # Its own fade, so the registry steps nothing: a wake word
                # cutting the music dead is more startling than the music.
                "duck":     lambda percent: self.player.fade(percent, 220),
                "unduck":   lambda: self.player.fade(self._volume(), 500),
                "search":   self.search_now,
            },
        )

        # The panel's own wake word, not one of this plugin's own. A second
        # word means the assistant listens for both and half the skills
        # answer to each, which is indistinguishable from it not hearing you.
        wake = self.client.wake_word

        self.client.SKILLS.register(self.KEY, [
            Skill(
                wake_word  = wake,
                skill_key  = "play-music",
                plugin_key = self.KEY,
                examples   = ["play something", "put on some music",
                              "play a song", "listen to something"],
                payload    = {"track": ["play", "put on", "listen to"]},
                func       = self.skill_play,
            ),
            Skill(
                wake_word  = wake,
                skill_key  = "pause-music",
                plugin_key = self.KEY,
                examples   = ["pause the music", "stop the music",
                              "pause the song"],
                func       = self.skill_pause,
            ),
            Skill(
                wake_word  = wake,
                skill_key  = "resume-music",
                plugin_key = self.KEY,
                examples   = ["resume the music", "carry on with the music",
                              "keep playing"],
                func       = self.skill_resume,
            ),
            Skill(
                wake_word  = wake,
                skill_key  = "skip-music",
                plugin_key = self.KEY,
                examples   = ["skip this song", "next track", "skip this"],
                func       = self.skill_skip,
            ),
            Skill(
                wake_word  = wake,
                skill_key  = "whats-playing",
                plugin_key = self.KEY,
                examples   = ["what is playing", "what song is this",
                              "what is this song"],
                func       = self.skill_whats_playing,
            ),
        ])

        # Quieten for the assistant, and put it back when it has finished.
        self.client.subscribe_to_event("on_woke_assistant", self._on_wake)
        self.client.subscribe_to_event("on_assistant_cancelled", self._on_settled)
        self.client.subscribe_to_event("on_update", self._watch_assistant)
        self.client.subscribe_to_event("on_update", self._poll_system)

        # Recorded from what actually plays rather than from what was searched
        # for. The two differ more often than they look: a queue skips past a
        # video that refuses, the watch-page fallback opens a different URL for
        # the same track, and a history replay does not search at all. Watching
        # the player is the only place all of those agree.
        self.client.PLAYER.subscribe(self._on_player_changed)

        # Served rather than handed to the page as a string: a document set
        # with setHtml has no real origin, and the YouTube embed checks one.
        #
        # Unauthenticated because the only thing that fetches it is this
        # panel's own hidden page, which has no token - and it is a static
        # shell with nothing in it worth protecting.
        self.client.QUICK.register(
            self.KEY, "music_history", "Music", "mdi.history",
            on_press=self.open_history,
            order=48,
            # It opens a panel, so the quick settings sheet gets out of the way.
            closes_panel=True)

        self.client.API.register(
            self.KEY, "music_shell", self.api_shell, requires_auth=False,
            description="The hidden player page. Not meant to be opened.")

        # "Stop" and "shut up" stop music. "Nevermind" deliberately does not:
        # it belongs to a question somebody has thought better of asking, and
        # music is not a question.
        #
        # A lower priority than an answer panel, so with one open over music
        # "stop" closes the panel first - whatever is in front is what somebody
        # means.
        self.client.CANCEL.register(
            self.KEY, "stop_music",
            keywords=["stop", "stop it", "stop the music", "stop playing",
                      "stop the song", "shut up", "silence", "quiet",
                      "be quiet", "turn it off", "turn off the music",
                      "enough"],
            handler=self._stop_everything,
            is_active=lambda: self.client.PLAYER.state().playing,
            priority=20,
            description="stop the music",
            # Somebody stopping the music has not finished talking - they may
            # be about to ask for something else.
            stops_listening=False,
        )

        self.client.public.expose(self.KEY, "music", {
            "play":    self.play,
            "search":  self.search_now,
            "results": lambda: [r.to_dict() for r in self._results],
        })

    def unload(self, carryover=None):
        for name, handler in (("on_woke_assistant", self._on_wake),
                              ("on_assistant_cancelled", self._on_settled),
                              ("on_update", self._watch_assistant),
                              ("on_update", self._poll_system)):
            try:
                self.client.unsubscribe_from_event(name, handler)
            except Exception:
                pass
        try:
            self.client.PLAYER.unregister(self.KEY)
        except Exception:
            pass
        try:
            self.client.CANCEL.unregister(self.KEY)
        except Exception:
            pass
        try:
            self.client.QUICK.unregister(self.KEY)
        except Exception:
            pass
        try:
            self.client.PLAYER.unsubscribe(self._on_player_changed)
        except Exception:
            pass
        if self.player is not None:
            self.client.call_on_ui(self.player.stop)
        self.player = None

    ## WIDGET

    @mixin("sub.home.__init__", "musicplugin", "after")
    def _add_widget(self, sub_home, *args):
        """
        Registered, not placed - the saved layout decides what is on screen,
        the same way every other widget arrives.
        """
        from src.ui.widgets.now_playing import NowPlayingWidget
        sub_home.features().register_widget(NowPlayingWidget)

    ## SETTINGS

    def _volume(self) -> int:
        try:
            return max(0, min(100, int(self.settings.volume.value)))
        except Exception:
            return 80

    def _duck_to(self) -> int:
        try:
            return max(0, min(100, int(self.settings.duck_volume.value)))
        except Exception:
            return 20

    def _api_key(self) -> str:
        try:
            return str(self.client.SECRETS.get(self.KEY, "youtube_api_key") or "")
        except Exception:
            return ""

    ## DUCKING

    #the assistant is dealing with somebody while it is in one of these
    BUSY = ("LISTENING", "THINKING", "ACTING")
    #how long it has to be settled before the volume comes back
    SETTLE = 1.2

    def _on_wake(self, event=None) -> None:
        """A recognised request. Duck at once rather than waiting for a tick."""
        self._duck_now()

    def _on_settled(self, event=None) -> None:
        self._settled_at = time.time()

    def _paused_for_assistant(self) -> bool:
        try:
            return bool(self.settings.pause_on_wake.value)
        except Exception:
            return False

    def _duck_now(self) -> None:
        if not bool(self.settings.duck_on_wake.value):
            return
        if not self.client.PLAYER.state().playing:
            return
        self._settled_at = 0.0

        if self._paused_for_assistant():
            # Surer than any volume. The microphone hears the speakers, and
            # music quiet enough not to be transcribed is often quiet enough
            # to be inaudible anyway.
            self._paused_by_us = True
            self.client.PLAYER.pause()
            return
        self.client.PLAYER.duck(self._duck_to())

    def _watch_assistant(self, event=None) -> None:
        """
        Duck while the assistant is busy, and restore once it has settled.

        Driven by the status rather than by events. `on_woke_assistant` only
        fires once a skill has been recognised, so the wake word alone would
        never quieten anything - and the status passes through LIVE on the way
        to LISTENING, so unducking the moment it reads LIVE undoes the duck
        within a frame of making it.

        Settling is therefore a duration, not a value: LIVE has to hold for
        SETTLE seconds. Every way a request can end - answered, cancelled,
        timed out, fallen through - comes back to LIVE and stays there.
        """
        status = getattr(self.client, "ASSIST_STATUS", "LIVE")

        if status in self.BUSY:
            self._settled_at = 0.0
            if not self.client.PLAYER.ducked and not self._paused_by_us:
                self._duck_now()
            return

        if not self.client.PLAYER.ducked and not self._paused_by_us:
            return

        now = time.time()
        if not self._settled_at:
            self._settled_at = now
            return
        if now - self._settled_at >= self.SETTLE:
            self._settled_at = 0.0
            if self._paused_by_us:
                # Only resumed if we were the ones who stopped it. Somebody
                # who pressed pause during a request meant it.
                self._paused_by_us = False
                self.client.PLAYER.play()
            self.client.PLAYER.unduck()

    ## API

    def api_shell(self, **_ignored):
        """The player page, told which origin it is being served from."""
        from .shell import build as build_shell
        origin = self.player.origin() if self.player else ""
        return (build_shell(origin), 200,
                {"Content-Type": "text/html; charset=utf-8"})

    def _stop_everything(self) -> None:
        """
        Stop, rather than pause.

        "Stop the music" means stop, and leaving it paused mid-track so the
        card still sits there showing a song is not what was asked for.
        """
        self._paused_by_us = False
        self.client.PLAYER.stop()
        self.client.PLAYER.unduck()
        self.client.PLAYER.publish("youtube", NowPlaying(state=STOPPED))
        self.client.call_on_ui(self._hand_back)

    ## HISTORY

    def _on_player_changed(self, kind: str) -> None:
        """
        Note what is playing, once it actually is.

        Only a track of ours with an id, and only once it has started - a
        search that found something is not the same as something playing, and
        a video that turned out to be unplayable should not be offered as
        something to press again.
        """
        if kind != "changed" or self.history is None:
            return

        playing = self.client.PLAYER.state()
        if playing.source != "youtube" or not playing.track_id:
            return
        if playing.state not in ("playing", "paused"):
            return
        if playing.track_id == self._last_remembered:
            return
        # An advert is not the track.
        if playing.title == "Advert":
            return

        self._last_remembered = playing.track_id
        self.history.remember(playing.track_id, playing.title, playing.artist,
                              playing.art_url, asked=self._last_asked)

    def open_history(self) -> None:
        """A panel of what has been played, each row a thing to press."""
        from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QScroller
        from src.styling import set_style, make_font, SIZES

        entries = list(self.history.items) if self.history else []

        content = QWidget()
        set_style(content, "common", "transparent")
        column = QVBoxLayout(content)
        column.setContentsMargins(18, 20, 18, 18)
        column.setSpacing(8)

        heading = QLabel("Recently played")
        heading.setFont(make_font(SIZES.M1, bold=True))
        set_style(heading, "common", "text-strong")
        column.addWidget(heading)

        if not entries:
            empty = QLabel("Nothing yet. Ask for a song and it will appear "
                           "here.")
            empty.setFont(make_font(SIZES.S2))
            empty.setWordWrap(True)
            set_style(empty, "common", "text-muted")
            column.addWidget(empty)
            column.addStretch()
        else:
            panel_holder = {}

            def play_it(entry: dict) -> None:
                # Closed first, so the press feels like it did something even
                # while the page is still loading.
                panel = panel_holder.get("panel")
                if panel is not None:
                    try:
                        panel.close_panel(destroy=True)
                    except Exception:
                        pass
                self.play_history_entry(entry)

            for entry in entries:
                column.addWidget(HistoryCard(self.client, entry, play_it))
            column.addStretch()

        # Pressing anywhere else closes it. There is no close button and
        # nothing else to press, so without this the panel cannot be got rid
        # of at all.
        panel = self.client.create_panel(content, width=self._panel_width(),
                                         key="music_history",
                                         dismiss_on_outside_click=True)
        if entries:
            panel_holder["panel"] = panel
        try:
            QScroller.grabGesture(
                content, QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        except Exception:
            pass

    def _panel_width(self) -> int:
        try:
            host = self.client.OVERLAYS
            if host is not None and host.width() > 0:
                return max(360, min(520, int(host.width() * 0.28)))
        except Exception:
            pass
        return 400

    def play_history_entry(self, entry: dict) -> None:
        """
        Play something straight from the history.

        By id, with no search at all. The id is what was played last time, so
        searching again would be asking a question that has already been
        answered - and could answer it differently.
        """
        video_id = str((entry or {}).get("video_id") or "")
        if not video_id:
            return

        self._take_over()
        self._last_asked = entry.get("asked") or entry.get("title") or ""
        self.client.PLAYER.publish("youtube", NowPlaying(
            title=entry.get("title") or "", artist=entry.get("artist") or "",
            art_url=entry.get("art_url") or "", state="loading",
            source="youtube", track_id=video_id))

        known = [{"title": entry.get("title") or "",
                  "artist": entry.get("artist") or ""}]
        self.client.call_on_ui(
            lambda: self.player.load([video_id], 0,
                                     [entry.get("artist") or ""], known))

    ## SYSTEM AUDIO

    def _poll_system(self, event=None) -> None:
        """
        Ask the machine what it is playing.

        Only while the system backend is the active one. Reading MPRIS starts
        a subprocess, and doing that twice a second while this plugin is
        playing its own music would be work for an answer nobody reads.
        """
        if self.system is None or not self.system.available:
            return
        backend = self.client.PLAYER.active()
        if backend is None or backend.key != "system":
            return

        now = time.time()
        if now - self._last_system < self.SYSTEM_POLL:
            return
        self._last_system = now

        def work():
            playing = None
            try:
                playing = self.system.read()
            except Exception as e:
                self.client.log("debug", f"[Music] System read failed: {e}")
            if playing is not None:
                self.client.PLAYER.publish("system", playing)

        # On a worker: this shells out, and on_update runs twenty times a
        # second on the UI thread.
        Thread(target=work, name="__mpris_read", daemon=True).start()

    def _take_over(self) -> None:
        """This plugin is about to play something, so it becomes the source."""
        self.client.PLAYER.set_active("youtube")

    def _hand_back(self) -> None:
        """
        Nothing of ours is playing, so let the machine show through again.

        Only when a system backend exists, and only when the web player really
        has stopped - handing back mid-track would show a paused Spotify over
        music that is still going.
        """
        if self.system is None or not self.system.available:
            return
        if self.client.PLAYER.state().state not in ("stopped",):
            return
        self.client.PLAYER.set_active("system")

    ## PLAYING

    def play(self, query: str = "", **_ignored):
        """
        The backend's `play`.

        With nothing to look for this resumes, which is what a bare "play"
        means when something is already loaded.
        """
        query = str(query or "").strip()
        if not query:
            self.player.play()
            return True
        self.search_and_play(query)
        return True

    def search_now(self, query: str) -> list:
        """Search on this thread. For callers that already have one."""
        results = search(query, key=self._api_key(), log=self.client.log)
        self._results = results
        return results

    def _corrected(self, query: str) -> str:
        """
        The request with a known mishearing put right.

        Applied before searching rather than after: an artist name that does
        not exist returns nothing however well the results are ranked.
        """
        if self.aliases is None:
            return query
        title, artist = split_request(query)

        # A title first: a song listed under another name will not be found
        # by the name somebody says for it, however the artist is spelled.
        listed = self.aliases.resolve_title(title)
        if listed and listed != title:
            self.client.log("info", f"[Music] '{title}' is listed as "
                                    f"'{listed}'.")
            title = listed

        if artist:
            real = self.aliases.resolve(artist)
            if real and real.lower() != artist.lower():
                self.client.log("info", f"[Music] Heard '{artist}', searching "
                                        f"for '{real}'.")
                artist = real

        return f"{title} by {artist}" if artist else title

    def search_and_play(self, query: str) -> None:
        """
        Look it up, then play it - without blocking.

        On a worker because this arrives from a spoken request, and a network
        round trip on the UI thread would freeze the panel mid-sentence.
        """
        if self._searching:
            return
        self._searching = True

        # Said out loud immediately, so the panel is visibly doing something
        # while the search runs.
        self._take_over()
        self.client.PLAYER.publish("youtube", NowPlaying(
            title=f"Searching for {query}", state="loading", source="youtube"))

        self._last_asked = query

        def work():
            asked = self._corrected(query)
            title, artist = split_request(asked)
            dropped_artist = False

            try:
                results = self.search_now(asked)
                if not results and artist:
                    # One retry, without the artist. A title is usually heard
                    # correctly and a name usually is not, so the name is the
                    # part worth giving up on - and giving up on it is often
                    # the difference between a result and silence.
                    self.client.log("info", f"[Music] Nothing for '{asked}' - "
                                            f"trying just '{title}'.")
                    results = self.search_now(title)
                    dropped_artist = bool(results)
            except Exception as e:
                self.client.log("warning", f"[Music] Search failed: {e}")
                results = []
            finally:
                self._searching = False

            if not results:
                self.client.log("info", f"[Music] Nothing found for '{query}'.")
                self.client.PLAYER.publish("youtube", NowPlaying(state=STOPPED))
                self.client.simple_notify(
                    "magnify", "Music",
                    f"Could not find anything matching \u201c{query}\u201d.")
                self.client.call_on_ui(self._hand_back)
                return

            ids = [r.video_id for r in results]
            self.client.log("info", f"[Music] '{query}' -> {results[0].title!r} "
                                    f"by {results[0].artist!r} "
                                    f"(+{len(ids) - 1} queued)")
            # The player starts at whatever it was left at; the setting is
            # what the person chose.
            self.player._volume = self._volume()
            # Back to the UI thread: this ends in runJavaScript on a page.
            # The uploader travels with each id, so a channel that blocks
            # embedding can be skipped past in one step rather than one video
            # at a time.
            owners = [r.artist for r in results]
            # What the search found, so the card can show an artist even when
            # the page does not report one.
            known = [{"title": r.title, "artist": r.artist} for r in results]
            self.client.call_on_ui(
                lambda: self.player.load(ids, 0, owners, known))

            # Only when the artist had to be dropped to find anything. That is
            # the case where the name was probably misheard, and the only one
            # where an answer is worth interrupting somebody for.
            top = results[0]
            if dropped_artist and artist:
                self.client.call_on_ui(
                    lambda: self._ask_about_artist(artist, top))
            elif not comparable(title, top.title):
                # The right song under a name nobody said. Worth remembering,
                # because the search will find it directly next time.
                self.client.call_on_ui(
                    lambda: self._ask_about_title(title, top))

        Thread(target=work, name="__music_search", daemon=True).start()

    def _ask_about_artist(self, heard: str, result) -> None:
        """
        Ask whether that was the right song, and learn the name if it was.

        Asked rather than assumed: the panel guessed by throwing away part of
        what was said, and a guess written down as fact would make every
        future search for that name worse.
        """
        real = (result.artist or "").strip()
        if not real:
            # Nothing to learn. Asking anyway would be a question with no
            # answer worth storing.
            self.client.log("debug", "[Music] The result has no artist, so "
                                     "there is nothing to remember.")
            return

        def yes():
            if self.aliases.remember(heard, real):
                self.client.simple_notify(
                    "check", "Music",
                    f"\u201c{heard}\u201d now means \u201c{real}\u201d.")

        # The artist is the question, so it goes in the line somebody reads
        # first rather than in the small print underneath.
        self.client.confirm(
            f"Did you mean {real}?",
            f"{result.title}\n\u2014 {real}",
            detail=f"Nothing was found for \u201c{heard}\u201d, so the artist "
                   f"was dropped and this is what came back. Shall I remember "
                   f"that \u201c{heard}\u201d means \u201c{real}\u201d?",
            confirm_text=f"Yes, that is {real}",
            cancel_text="No",
            on_confirm=yes,
        )

    def _ask_about_title(self, said: str, result) -> None:
        """
        Ask whether a song listed under another name was the right one.

        A song has one title on YouTube Music and another on YouTube, so the
        two can share no character and be the same track. Nothing can be
        decided from here - only the person who asked knows.
        """
        actual = (result.title or "").strip()
        if not actual or not said:
            return

        def yes():
            if self.aliases.remember_title(said, actual):
                self.client.simple_notify(
                    "check", "Music",
                    f"\u201c{said}\u201d is listed as \u201c{actual}\u201d.")

        self.client.confirm(
            "Is this the right song?",
            actual,
            detail=f"You asked for \u201c{said}\u201d. This is listed under a "
                   f"different name - shall I remember they are the same?",
            confirm_text="Yes, remember it",
            cancel_text="No",
            on_confirm=yes,
        )

    ## SKILLS

    def skill_play(self, track: str = "", **_ignored):
        track = str(track or "").strip()
        if not track:
            self.client.PLAYER.play()
            return
        self.search_and_play(track)

    def skill_pause(self, **_ignored):
        self.client.PLAYER.pause()

    def skill_resume(self, **_ignored):
        self.client.PLAYER.play()

    def skill_skip(self, **_ignored):
        self.client.PLAYER.next()

    def skill_whats_playing(self, **_ignored):
        playing = self.client.PLAYER.state()
        if not playing.active:
            self.client.assistant.say("Nothing is playing.")
            return
        self.client.assistant.say(playing.describe() or "I am not sure.")
