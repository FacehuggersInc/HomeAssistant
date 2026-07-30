"""
What is playing, whatever is playing it.

Reads `client.PLAYER` and nothing else. It has no idea whether the sound is
coming from YouTube, a local library or a network player, which is the point:
a new source is a backend registration and no change here at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QRectF, QTimer
from PyQt6.QtGui import (
    QPainter, QColor, QPixmap, QPainterPath, QLinearGradient, QFontMetrics,
)
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSizePolicy,
)

from src.ui.widget import Widget
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons
from src.styling import set_style, make_font, SIZES

if TYPE_CHECKING:
    from src.main import Client


class NowPlayingWidget(Widget):
    """A cover, a title, an artist, a progress line and three buttons."""

    #the widget manager registers by this
    KEY = "nowplayingwidget"
    DISPLAY_NAME = "Now playing"

    RESIZABLE = True
    #Width only. The height is whatever a cover, two lines of text and a
    #progress line need, and there is nothing useful to do with more of it -
    #a taller card would be a cover with empty space beside it.
    MIN_W, MIN_H = 320, 108
    MAX_W, MAX_H = 900, 108

    ART = 72
    #how often the progress line is repainted while playing
    TICK_MS = 500

    def __init__(self, client: "Client"):
        super().__init__(
            client = client,
            key    = "nowplayingwidget",
            anchor = "bottom-left",
            width  = None,
            height = None,
        )

        self._art_url = ""
        self._art: QPixmap = None
        #the cover, blurred, as this card's backdrop. None means no art, and
        #the plain gradient is used instead.
        self._blurred: QPixmap = None
        #what was last drawn, so an unchanged republish costs nothing
        self._rendered = None
        #What visibility was last asked for. None means never asked, so the
        #first rebuild always applies one.
        self._wanted_visible = None

        # Painted rather than set as a stylesheet background: a stylesheet
        # gradient on a QWidget does not follow a resize, and this one is as
        # wide as its title.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setMinimumWidth(self.MIN_W)
        self.setFixedHeight(self.MIN_H)

        # The row of content, then the progress line beneath it. Two rows
        # rather than one, so the line can run the full width of the card
        # instead of only the width of the text column.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QWidget()
        set_style(body, "common", "transparent")
        outer.addWidget(body, 1)

        layout = QHBoxLayout(body)
        layout.setContentsMargins(14, 11, 16, 9)
        layout.setSpacing(14)

        self._cover = QLabel()
        self._cover.setFixedSize(self.ART, self.ART)
        self._cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self._cover, "player", "cover")
        layout.addWidget(self._cover)

        column = QVBoxLayout()
        column.setContentsMargins(0, 2, 0, 2)
        column.setSpacing(1)

        self._title = _ScrollingLabel(make_font(SIZES.S3, bold=True))
        set_style(self._title, "common", "text-strong")
        column.addWidget(self._title)

        self._artist = _ScrollingLabel(make_font(SIZES.S2))
        set_style(self._artist, "common", "text-muted")
        column.addWidget(self._artist)

        # "1:23 / 3:41". The progress line says roughly how far through, and
        # this says how far exactly - which is what somebody deciding whether
        # to wait for the end actually needs.
        self._time = QLabel("")
        self._time.setFont(make_font(SIZES.S1))
        set_style(self._time, "common", "text-muted")
        column.addWidget(self._time)

        column.addStretch()
        layout.addLayout(column, 1)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        # Play/pause and restart. No skip buttons: the queue is alternatives
        # for the song that was asked for, not a playlist, so "next" would
        # play something nobody chose.
        self._toggle  = IconButton(Icons.PLAY, self._on_toggle, size=30)
        self._restart = IconButton(Icons.RESTART, self._on_restart, size=22)
        for button in (self._toggle, self._restart):
            controls.addWidget(button)
        layout.addLayout(controls)

        # Across the whole bottom edge. Painted rather than a QProgressBar: a
        # progress bar arrives with a groove, a border and a chunk, all of
        # which would have to be styled away to get a line.
        self._bar = _ProgressLine(self)
        outer.addWidget(self._bar)

        # The framework's own tick, not a private QTimer: it is suspended and
        # resumed with the widget, so a hidden one costs nothing.
        self.client.PLAYER.subscribe(self._on_player)
        # Fires even when the widget is deleted rather than closed. The
        # closure holds the registry and the callback, never the widget, so
        # it stays valid after the C++ side has gone.
        registry, callback = self.client.PLAYER, self._on_player
        self.destroyed.connect(lambda *_: registry.unsubscribe(callback))
        self.rebuild()

    def tick(self) -> None:
        """Creep the progress line and the clock between publishes."""
        self._bar.update()
        try:
            playing = self.state()
            text = self._clock(playing)
            if text != self._time.text():
                self._time.setText(text)
        except RuntimeError:
            self.stop_tick()

    ## -- lifecycle

    def _on_toggle(self) -> None:
        self.client.PLAYER.toggle()

    def _on_restart(self) -> None:
        """Back to the beginning, and playing."""
        self.client.PLAYER.seek(0)
        if not self.state().playing:
            self.client.PLAYER.play()

    def resizeEvent(self, event) -> None:
        """
        Repaint on a resize.

        The backdrop is drawn stretched to the card rather than regenerated,
        so widening it only needs the paint to run again - but it does need to
        run, or the gradient keeps the old width.
        """
        super().resizeEvent(event)
        self.update()

    def _release(self) -> None:
        """
        Let go of the registry.

        Called from `destroyed` as well as `closeEvent`, because a widget can
        be deleted without ever being closed - dropped from a layout, or torn
        down with its page - and a listener still subscribed then keeps
        reaching for a C++ object that has gone.
        """
        try:
            self.client.PLAYER.unsubscribe(self._on_player)
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._release()
        self.stop_tick()
        super().closeEvent(event)

    def _on_player(self, kind: str) -> None:
        # Marshalled: a backend may publish from a network thread, and this
        # touches widgets.
        #
        # Wrapped rather than queueing a bound method. `call_on_ui(self._bar.update)`
        # hands the bridge a method bound to a C++ object, and by the time the
        # bridge runs it that object may have been deleted - which is a
        # RuntimeError inside the bridge, on every tick, forever.
        if kind == "ticked":
            self.client.call_on_ui(self._guarded_tick)
            return
        self.client.call_on_ui(self._guarded_rebuild)

    def _guarded_tick(self) -> None:
        try:
            self.tick()
        except RuntimeError:
            self._release()

    def _guarded_rebuild(self) -> None:
        try:
            self.rebuild()
        except RuntimeError:
            self._release()

    ## -- painting

    def state(self):
        return self.client.PLAYER.state()

    _KEY_FIELDS = ("active", "title", "artist", "state", "art", "duration",
                   "restart")

    def _render_key(self, playing) -> tuple:
        """Everything the card actually draws, and nothing that ticks."""
        return (playing.active, playing.title, playing.artist, playing.state,
                self._art_key(playing.art_url), round(playing.duration),
                self.client.PLAYER.can("seek") and playing.can_seek)

    @staticmethod
    def _art_key(url: str) -> str:
        """
        Cover art without whatever changes between reads of one track.

        The **path**, without the query string. A query is where a temporary
        token or a regenerated size lives, and comparing the raw URL would
        call the same picture a new one on every read.

        The filename alone is not enough: every YouTube thumbnail is called
        `maxresdefault.jpg`, so keying on that makes every video's art look
        identical and the cover never changes.
        """
        url = str(url or "")
        if not url:
            return ""
        return url.split("?")[0].rstrip("/")

    def rebuild(self) -> None:
        playing = self.state()

        # Idempotent on purpose. A source polled every couple of seconds
        # republishes the same track, and rebuilding then means clearing the
        # cover and re-laying out the row for no visible change - which reads
        # as the widget flashing.
        key = self._render_key(playing)
        if key == self._rendered:
            return
        if self._rendered is not None:
            # Named, so a source republishing something subtly different is
            # findable rather than just visible as a flicker.
            changed = [name for name, before, after
                       in zip(self._KEY_FIELDS, self._rendered, key)
                       if before != after]
            self.client.log("debug", f"[NowPlaying] Rebuilt: "
                                     f"{', '.join(changed) or 'nothing'}")
        self._rendered = key

        # Hidden rather than showing an empty card. A now-playing widget with
        # nothing playing is a blank rectangle on the wallpaper.
        # Compared against what was last ASKED for, not against isVisible().
        #
        # isVisible() is false for a widget whose parent has not been shown
        # yet, so at construction it matched `active` being false and
        # setVisible(False) was skipped - leaving the card never explicitly
        # hidden, and the framework showed it with everything else. A card with
        # nothing playing appeared on the wallpaper at every startup.
        if self._wanted_visible is not playing.active:
            self._wanted_visible = playing.active
            self.setVisible(playing.active)
        if not playing.active:
            self.stop_tick()
            return

        # Set whole. It scrolls if it does not fit rather than being cut -
        # a title is the one thing on this card worth reading in full.
        self._title.setText(playing.title or "Unknown")
        self._artist.setText(playing.artist)
        self._artist.setVisible(bool(playing.artist))

        self._toggle.update_icon(Icons.PAUSE if playing.playing else Icons.PLAY)
        # Restart needs a seek, and a live stream has nothing to go back to.
        can_restart = self.client.PLAYER.can("seek") and playing.can_seek
        self._restart.setEnabled(can_restart)
        self._restart.setVisible(can_restart)

        self._bar.setVisible(playing.duration > 0)
        self._time.setText(self._clock(playing))
        self._time.setVisible(bool(self._time.text()))
        self._load_art(playing.art_url)

        if playing.playing:
            self.start_tick(self.TICK_MS)
        else:
            self.stop_tick()
        self._bar.update()

    def paintEvent(self, event) -> None:
        """
        The cover blurred behind the card, or a gradient when there is none.

        Opaque either way, because the wallpaper behind it is a photograph: a
        translucent card over an arbitrary image is legible on some and
        unreadable on others, and there is no way to know which from here.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 14, 14)

        if self._blurred is not None and not self._blurred.isNull():
            # The cover, blurred, so the card takes its colour from whatever
            # is playing. Clipped to the same rounded rect as the gradient it
            # replaces.
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(self.rect(), self._blurred)
            painter.restore()
            # A wash over it, because a bright cover leaves white text on
            # white and there is no way to know from here which covers those
            # are.
            painter.fillPath(path, QColor(14, 17, 24, 150))
        else:
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0.0, QColor(28, 32, 42, 246))
            gradient.setColorAt(0.55, QColor(22, 26, 35, 246))
            gradient.setColorAt(1.0, QColor(16, 19, 26, 246))
            painter.fillPath(path, gradient)

        # A single hairline, so the card has an edge without a border that
        # reads as a box.
        painter.setPen(QColor(255, 255, 255, 26))
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)

    @staticmethod
    def _duration(seconds: float) -> str:
        """3661 -> '1:01:01'; 221 -> '3:41'."""
        total = max(0, int(seconds or 0))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _clock(self, playing) -> str:
        """
        "1:23 / 3:41", or just the position when the length is unknown.

        A live stream has no end, and "1:23 / 0:00" is worse than saying
        nothing about it.
        """
        if playing.duration <= 0:
            return self._duration(playing.position) if playing.position else ""
        return (f"{self._duration(playing.position)} / "
                f"{self._duration(playing.duration)}")

    @staticmethod
    def _elide(text: str, limit: int) -> str:
        text = " ".join(str(text or "").split())
        return text if len(text) <= limit else text[:limit - 1] + "\u2026"

    def _load_art(self, url: str) -> None:
        """
        Cover art, fetched once per URL.

        Compared by URL rather than reloaded on every rebuild: a rebuild
        happens on every pause and every track, and re-fetching the same
        picture each time would be a request per button press.
        """
        url = str(url or "")
        if self._art_key(url) == self._art_key(self._art_url) and self._art:
            # The same picture by a different path. Keep what is on screen.
            self._art_url = url
            return
        self._art_url = url

        if not url:
            self._art = None
            self._cover.clear()
            self._blurred = None
            self.update()
            return

        # Deliberately not cleared here. The old cover stays until the new one
        # has arrived, so a track change is a swap rather than a blank frame
        # followed by a picture.

        from threading import Thread

        def work():
            data = None
            try:
                import urllib.request
                if url.startswith("file://"):
                    # MPRIS usually gives a local path. A Request with headers
                    # is refused by the file handler, so it is read directly.
                    from urllib.parse import unquote, urlsplit
                    path = unquote(urlsplit(url).path)
                    with open(path, "rb") as handle:
                        data = handle.read(4 * 1024 * 1024)
                else:
                    request = urllib.request.Request(
                        url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(request, timeout=8) as response:
                        data = response.read(4 * 1024 * 1024)
            except Exception as e:
                self.client.log("debug", f"[NowPlaying] Art failed: {e}")
                return
            if data:
                self.client.call_on_ui(lambda: self._apply_art(url, data))

        Thread(target=work, name="__nowplaying_art", daemon=True).start()

    def _apply_art(self, url: str, data: bytes) -> None:
        # The track may have changed while this was in flight.
        if url != self._art_url:
            return
        try:
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                return
            size = self.ART
            scaled = pixmap.scaled(size, size,
                                   Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
            if scaled.width() > size or scaled.height() > size:
                scaled = scaled.copy((scaled.width() - size) // 2,
                                     (scaled.height() - size) // 2, size, size)

            rounded = QPixmap(size, size)
            rounded.fill(Qt.GlobalColor.transparent)
            painter = QPainter(rounded)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, size, size), 8, 8)
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, scaled)
            painter.end()

            self._art = rounded
            self._cover.setPixmap(rounded)
            self._blurred = self._blur(scaled)
            self.update()
        except RuntimeError:
            pass      # the widget went away mid-flight

    @staticmethod
    def _blur(source: QPixmap) -> QPixmap:
        """
        The cover, blurred, for use behind the card.

        Done by scaling down and back up rather than with a blur effect: a
        QGraphicsBlurEffect needs a scene and a render pass, and a 12px
        thumbnail stretched out is the same picture with none of that. It is
        also darkened here rather than at paint time, so the text over it has
        something predictable to sit on.
        """
        tiny = source.scaled(12, 12, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        wide = tiny.scaled(360, 120, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)

        darkened = QPixmap(wide.size())
        darkened.fill(Qt.GlobalColor.transparent)
        painter = QPainter(darkened)
        painter.drawPixmap(0, 0, wide)
        painter.fillRect(darkened.rect(), QColor(10, 12, 17, 188))
        painter.end()
        return darkened


class _ScrollingLabel(QWidget):
    """
    A single line that scrolls when it does not fit.

    Elision loses the end of the title, which on a music card is often the
    part that says which version this is. It scrolls instead, and only when
    it has to - a title that fits sits still, because text creeping past for
    no reason is worse than either.
    """

    #pixels a second
    SPEED = 26.0
    #how long it waits at each end before setting off
    PAUSE = 1.6
    #the gap between the end of the text and its repeat
    GAP = 44

    def __init__(self, font):
        super().__init__()
        self.setFont(font)
        self._text = ""
        self._offset = 0.0
        self._waiting = self.PAUSE
        self._needed = 0

        metrics = QFontMetrics(font)
        self.setFixedHeight(metrics.height())
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        set_style(self, "common", "transparent")

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._step)

    def setText(self, text: str) -> None:
        text = " ".join(str(text or "").split())
        if text == self._text:
            return
        self._text = text
        self._offset = 0.0
        self._waiting = self.PAUSE
        self._measure()
        self.update()

    def text(self) -> str:
        return self._text

    def _measure(self) -> None:
        self._needed = QFontMetrics(self.font()).horizontalAdvance(self._text)
        if self._needed > self.width() and self._text:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._offset = 0.0

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._measure()

    def hideEvent(self, event) -> None:
        # Nothing should be creeping along off screen.
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._measure()

    def _step(self) -> None:
        if self._waiting > 0:
            self._waiting -= 0.033
            return
        self._offset += self.SPEED * 0.033
        if self._offset >= self._needed + self.GAP:
            self._offset = 0.0
            self._waiting = self.PAUSE
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        baseline = QFontMetrics(self.font()).ascent()

        if self._needed <= self.width():
            painter.drawText(0, baseline, self._text)
            painter.end()
            return

        # Drawn twice, the second a gap behind the first, so the line wraps
        # continuously rather than sliding out and jumping back.
        start = -int(self._offset)
        painter.drawText(start, baseline, self._text)
        painter.drawText(start + self._needed + self.GAP, baseline, self._text)
        painter.end()


class _ProgressLine(QWidget):
    """A two-pixel line under the title."""

    #Taller than a hairline, so it reads as a progress line at arm's length
    #rather than as the card's bottom border.
    HEIGHT = 5

    def __init__(self, owner: NowPlayingWidget):
        super().__init__(owner)
        self.owner = owner
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        set_style(self, "common", "transparent")

    def paintEvent(self, event) -> None:
        playing = self.owner.state()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        # Clipped to the card's own bottom corners, or the line would square
        # them off and the card would look like it had a foot.
        path = QPainterPath()
        radius = 14.0
        rect = QRectF(self.rect())
        path.addRoundedRect(QRectF(rect.x(), rect.y() - radius,
                                   rect.width(), rect.height() + radius),
                            radius, radius)
        painter.setClipPath(path)

        painter.fillRect(self.rect(), QColor(255, 255, 255, 26))
        filled = int(self.width() * playing.progress)
        if filled:
            painter.fillRect(0, 0, filled, self.height(),
                             QColor(47, 240, 142, 225))
        painter.end()
