from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
)
from PyQt6.QtCore import Qt, QUrl

from src.ui.page import PageFramework
from src.ui.controls.buttons import IconButton
from src.styling import make_font, SIZES, set_style

if TYPE_CHECKING:
    from src.main import Client


# Chromium honours ::-webkit-scrollbar, so a page's own scrollbars can be
# restyled from inside it. The default is a white desktop scrollbar, which on
# a dark panel is the brightest thing on screen.
# Drag anywhere to scroll, with a flick carrying on afterwards.
#
# A panel sends mouse events, not touch ones, so the engine's own touch
# scrolling never engages - without this the only way down a page is the
# scrollbar, which is a 14px target on a wall.
#
# Links still work: the drag only starts past a threshold, and a press that
# never crosses it is left alone entirely.
DRAG_SCROLL_JS = """
(function(){
  if (window.__haDragScroll) { return; }
  window.__haDragScroll = true;

  var down = false, moved = false, lastY = 0, lastX = 0, velocity = 0, timer = null;
  var THRESHOLD = 8;

  function glide() {
    if (Math.abs(velocity) < 0.4) { timer = null; return; }
    window.scrollBy(0, velocity);
    velocity *= 0.94;
    timer = requestAnimationFrame(glide);
  }

  document.addEventListener('mousedown', function (e) {
    if (e.button !== 0) { return; }
    down = true; moved = false;
    lastY = e.clientY; lastX = e.clientX; velocity = 0;
    if (timer) { cancelAnimationFrame(timer); timer = null; }
  }, true);

  document.addEventListener('mousemove', function (e) {
    if (!down) { return; }
    var dy = lastY - e.clientY, dx = lastX - e.clientX;
    if (!moved && Math.abs(dy) + Math.abs(dx) < THRESHOLD) { return; }
    moved = true;
    window.scrollBy(dx, dy);
    velocity = dy;
    lastY = e.clientY; lastX = e.clientX;
    e.preventDefault();
  }, true);

  document.addEventListener('mouseup', function (e) {
    if (moved) {
      // Swallowed, so the release that ended a drag does not also follow
      // whatever link happened to be under the finger.
      e.stopPropagation(); e.preventDefault();
      timer = requestAnimationFrame(glide);
    }
    down = false;
  }, true);

  document.addEventListener('click', function (e) {
    if (moved) { e.stopPropagation(); e.preventDefault(); moved = false; }
  }, true);

  // Text selection fights the drag and there is no keyboard to copy with.
  document.addEventListener('selectstart', function (e) {
    if (down) { e.preventDefault(); }
  }, true);
})();
"""

SCROLLBAR_CSS = """
  ::-webkit-scrollbar { width: 14px; height: 14px; }
  ::-webkit-scrollbar-track { background: rgba(0,0,0,0.25); }
  ::-webkit-scrollbar-thumb {
      background: rgba(255,255,255,0.22);
      border-radius: 7px;
      border: 3px solid transparent;
      background-clip: content-box;
  }
  ::-webkit-scrollbar-thumb:hover { background-clip: content-box;
      background-color: rgba(255,255,255,0.34); }
  ::-webkit-scrollbar-corner { background: transparent; }
"""


def _install_scrollbar_style(view) -> None:
    """
    Inject the scrollbar styling into every document the view loads.

    A script rather than a one-off runJavaScript: it has to apply to whatever
    is navigated to next as well, and re-running it by hand on every load
    would miss frames and history navigations.
    """
    try:
        from PyQt6.QtWebEngineCore import QWebEngineScript

        script = QWebEngineScript()
        script.setName("ha-scrollbars")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        # ApplicationWorld, so a page's own scripts cannot see or remove it.
        script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode(
            "(function(){var s=document.createElement('style');"
            "s.setAttribute('data-ha','scrollbars');"
            f"s.textContent={SCROLLBAR_CSS!r};"
            "(document.head||document.documentElement).appendChild(s);})();"
            + DRAG_SCROLL_JS
        )
        view.page().scripts().insert(script)
    except Exception:
        pass


def _locked_page(view, base: str):
    """
    Refuse navigation outside `base`, if there is one.

    Checked at the engine rather than by watching urlChanged and going back:
    by the time a URL has changed the page has already been fetched, and a
    kiosk showing the docs should not be one mis-tapped link away from the
    open internet.
    """
    try:
        from PyQt6.QtWebEngineCore import QWebEnginePage

        class _Locked(QWebEnginePage):
            def acceptNavigationRequest(self, url, kind, is_main_frame):
                if not base or not is_main_frame:
                    return True
                target = url.toString()
                if target.startswith(base) or target.startswith("about:"):
                    return True
                self.blocked.append(target)
                return False

        page = _Locked(view)
        page.blocked = []
        view.setPage(page)
        return page
    except Exception:
        return None


def _web_view():
    """
    A QWebEngineView, or None.

    Still wrapped even though it is a dependency: it imports fine on a machine
    where it cannot start, and throws when a view is built. Catching that
    means the page explains itself rather than taking the app down.
    """
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        return QWebEngineView()
    except Exception:
        return None


class WebPage(PageFramework):
    """
    A browser, in the app.

    Registered by the client rather than a plugin, because several things want
    somewhere to put a web page - the docs, a plugin's own interface, a login
    that has to happen in a real browser engine - and none of them should have
    to carry a browser with them.
    """

    KEY  = "#webpage"
    HOME = "about:blank"

    # Edge to edge. A web page has its own margins and the chrome above it is
    # already inset; a second frame around the whole thing just loses screen.
    PADDING = 0

    ZOOM_STEPS = (0.75, 0.9, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0, 2.5)
    DEFAULT_ZOOM = 1.15     # a shade larger than a desktop, read standing up

    # Read by the client's idle check. A web page is something a person is
    # reading at their own pace, and there is no interaction to measure while
    # they do - so nothing should decide they have gone away.
    blocks_idle = True

    def __init__(self, client: "Client", data: dict = None):
        super().__init__(client=client, key=self.KEY, data=data)
        set_style(self, "common", "page-background")

        data = data or {}
        self.home_url = data.get("home") or self.HOME
        start = data.get("url") or self.home_url

        # Two separate locks, because they answer different questions.
        # `lock_address` is about who may type an address; `lock_base` is
        # about where any navigation may go, typed or clicked.
        self.lock_address = bool(data.get("lock_address", False))
        self.lock_base = (data.get("lock_base") or "").strip()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.PADDING, self.PADDING,
                                 self.PADDING, self.PADDING)
        outer.setSpacing(0)

        outer.addLayout(self._build_bar())

        # A two-pixel line rather than a spinner: it says "still working"
        # without taking any room from the page.
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(2)
        self.progress.setRange(0, 100)
        self.progress.hide()
        set_style(self.progress, "common", "load-bar")
        outer.addWidget(self.progress)

        self.view = _web_view()
        if self.view is not None:
            self._locked = _locked_page(self.view, self.lock_base)
            # After the page is swapped in by _locked_page - scripts belong to
            # the page, and setPage replaces the collection they were in.
            _install_scrollbar_style(self.view)
            try:
                from PyQt6.QtGui import QColor
                self.view.page().setBackgroundColor(QColor("#151517"))
            except Exception:
                pass
            self.lock_glyph.setVisible(bool(self.lock_base))
            # A kiosk should not offer "open in new window" or "view source".
            self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.view.loadProgress.connect(self._progress)
            self.zoom = data.get("zoom") or self.DEFAULT_ZOOM
            self.view.setZoomFactor(self.zoom)
            self._show_zoom()
            self.view.urlChanged.connect(self._url_changed)
            self.view.loadFinished.connect(lambda ok: self._refresh_buttons())
            outer.addWidget(self.view, stretch=1)
            self.navigate(start)
        else:
            missing = QLabel(
                "The browser engine did not start.\n\n"
                "PyQt6-WebEngine is a dependency, so this usually means an "
                "incomplete install rather than a missing extra:\n\n"
                "    pip install -r requirements.txt"
            )
            missing.setFont(make_font(SIZES.S2))
            missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
            missing.setWordWrap(True)
            set_style(missing, "common", "text-muted")
            outer.addWidget(missing, stretch=1)

        self.add_features({
            "navigate":  self.navigate,
            "set_home":  self.set_home,
            "current":   self.current_url,
        })

    ## -- chrome

    def _build_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self.back_btn    = IconButton("mdi.arrow-left", self.back, size=20)
        self.forward_btn = IconButton("mdi.arrow-right", self.forward, size=20)
        self.home_btn    = IconButton("mdi.home", self.go_home, size=20)
        self.reload_btn  = IconButton("mdi.refresh", self.reload, size=20)

        for button in (self.back_btn, self.forward_btn, self.reload_btn,
                       self.home_btn):
            row.addWidget(button)

        # Zoom, because the single most common problem with a web page on a
        # wall panel is that it was laid out for somebody sitting 60cm away.
        self.zoom_out_btn = IconButton("mdi.magnify-minus-outline",
                                       lambda: self.zoom_by(-1), size=20)
        self.zoom_in_btn  = IconButton("mdi.magnify-plus-outline",
                                       lambda: self.zoom_by(1), size=20)
        self.zoom_label = QLabel("")
        self.zoom_label.setFont(make_font(SIZES.S1, bold=True))
        self.zoom_label.setFixedWidth(46)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(self.zoom_label, "common", "text-muted")
        row.addWidget(self.zoom_out_btn)
        row.addWidget(self.zoom_label)
        row.addWidget(self.zoom_in_btn)

        self.address = QLineEdit("")
        self.address.setFont(make_font(SIZES.S2))
        self.address.setFixedHeight(44)
        # Read-only and tapped, like every other field in the app - a hardware
        # keyboard is not a thing this runs with.
        self.address.setReadOnly(True)
        self.address.setCursor(Qt.CursorShape.PointingHandCursor)
        self.address.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        set_style(self.address, "settings", "body-field")
        row.addWidget(self.address, stretch=1)

        self.lock_glyph = IconButton("mdi.lock-outline", lambda: None, size=16)
        self.lock_glyph.setEnabled(False)
        self.lock_glyph.setCursor(Qt.CursorShape.ArrowCursor)
        self.lock_glyph.setToolTip("This page is locked to one site")
        self.lock_glyph.hide()
        row.addWidget(self.lock_glyph)

        self.top_btn = IconButton("mdi.arrow-collapse-up", self.scroll_top, size=20)
        row.addWidget(self.top_btn)

        self.exit_btn = IconButton("mdi.close", self._leave, size=20)
        row.addWidget(self.exit_btn)

        host_margin = 10
        row.setContentsMargins(host_margin, host_margin, host_margin, 6)

        host = QWidget()
        set_style(host, "common", "transparent")
        host.setLayout(row)
        host.setCursor(Qt.CursorShape.PointingHandCursor)
        host.mouseReleaseEvent = lambda _event: self._edit_address()

        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.addWidget(host)
        return wrapper

    def _edit_address(self) -> None:
        if self.lock_address:
            # Said out loud rather than ignored - a field that does nothing
            # when tapped reads as broken.
            self.client.simple_notify("mdi.lock-outline", "Web",
                                      "The address is fixed for this page.")
            return
        from src.ui.keyboard import KeyboardDialog
        self.client.dialog(KeyboardDialog(
            self.client, self.address, mode="text", label="Address",
            on_done=lambda text: self.navigate(text)))

    ## -- navigation

    def navigate(self, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        # Typed addresses rarely include a scheme, and QUrl treats one without
        # it as a relative path that resolves to nothing.
        if "://" not in url and not url.startswith("about:"):
            url = "https://" + url

        # Checked here as well as at the engine: this is the typed path, and
        # refusing it with a reason is friendlier than a navigation that
        # silently does not happen.
        if self.lock_base and not url.startswith(self.lock_base) \
                and not url.startswith("about:"):
            self.client.simple_notify(
                "mdi.lock-outline", "Web",
                f"This page can only show {self.lock_base}")
            return

        self.address.setText(url)
        if self.view is not None:
            self.view.setUrl(QUrl(url))

    def set_home(self, url: str) -> None:
        self.home_url = url or self.HOME

    def current_url(self) -> str:
        if self.view is None:
            return self.address.text()
        return self.view.url().toString()

    def go_home(self, event=None) -> None:
        self.navigate(self.home_url)

    def back(self, event=None) -> None:
        if self.view is not None and self.view.history().canGoBack():
            self.view.back()

    def forward(self, event=None) -> None:
        if self.view is not None and self.view.history().canGoForward():
            self.view.forward()

    def reload(self, event=None) -> None:
        if self.view is not None:
            self.view.reload()

    def _leave(self, event=None) -> None:
        self.client.goto(self.client.DEFAULT_PAGE or "#root")

    ## -- zoom and scrolling

    def zoom_by(self, direction: int) -> None:
        steps = list(self.ZOOM_STEPS)
        current = min(range(len(steps)),
                      key=lambda i: abs(steps[i] - getattr(self, "zoom", 1.0)))
        self.zoom = steps[max(0, min(len(steps) - 1, current + direction))]
        if self.view is not None:
            self.view.setZoomFactor(self.zoom)
        self._show_zoom()

    def _show_zoom(self) -> None:
        self.zoom_label.setText(f"{int(round(self.zoom * 100))}%")

    def scroll_top(self, event=None) -> None:
        if self.view is not None:
            self.view.page().runJavaScript(
                "window.scrollTo({top: 0, behavior: 'smooth'});")

    def _progress(self, value: int) -> None:
        try:
            self.progress.setValue(value)
            self.progress.setVisible(0 < value < 100)
        except RuntimeError:
            pass

    ## -- state

    def _url_changed(self, url) -> None:
        try:
            self.address.setText(url.toString())
        except RuntimeError:
            pass
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        if self.view is None:
            self.back_btn.setEnabled(False)
            self.forward_btn.setEnabled(False)
            return
        try:
            history = self.view.history()
            self.back_btn.setEnabled(history.canGoBack())
            self.forward_btn.setEnabled(history.canGoForward())
        except RuntimeError:
            pass
