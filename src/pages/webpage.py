from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
)
from PyQt6.QtCore import Qt, QUrl, QTimer

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

  // Native drag first. A press on a link or an image starts the browser's own
  // drag-and-drop, which swallows every move event after it - so the page
  // never scrolled and a link came away under the finger instead.
  var style = document.createElement('style');
  style.textContent =
    'a,img{-webkit-user-drag:none;user-drag:none}' +
    'html.ha-dragging,html.ha-dragging *{user-select:none !important;' +
    '-webkit-user-select:none !important}';
  (document.head || document.documentElement).appendChild(style);
  document.addEventListener('dragstart', function (e) { e.preventDefault(); }, true);

  var THRESHOLD = 8;
  var active = false, moved = false, lastY = 0, lastX = 0, velocity = 0, timer = null;
  var target = null;

  // The nearest thing that can actually scroll, starting from what was
  // touched. Always scrolling the window meant a sidebar - the docs
  // navigation, any panel with its own overflow - could not be dragged at
  // all, because the window behind it had nothing left to scroll.
  function scroller(node) {
    for (var el = node; el && el !== document.body; el = el.parentElement) {
      var style = window.getComputedStyle(el);
      var scrolls = /(auto|scroll|overlay)/.test(style.overflowY + style.overflow);
      if (scrolls && el.scrollHeight > el.clientHeight + 2) { return el; }
    }
    return null;
  }

  function scrollBy(dx, dy) {
    if (target) { target.scrollTop += dy; target.scrollLeft += dx; }
    else { window.scrollBy(dx, dy); }
  }

  function glide() {
    if (Math.abs(velocity) < 0.4) { timer = null; return; }
    scrollBy(0, velocity);
    velocity *= 0.94;
    timer = requestAnimationFrame(glide);
  }

  function begin(x, y, node) {
    active = true; moved = false;
    lastX = x; lastY = y; velocity = 0;
    target = scroller(node);
    if (timer) { cancelAnimationFrame(timer); timer = null; }
  }

  function move(x, y, event) {
    if (!active) { return; }
    var dy = lastY - y, dx = lastX - x;
    if (!moved && Math.abs(dy) + Math.abs(dx) < THRESHOLD) { return; }
    if (!moved) { document.documentElement.classList.add('ha-dragging'); }
    moved = true;
    scrollBy(dx, dy);
    velocity = dy;
    lastX = x; lastY = y;
    if (event && event.cancelable) { event.preventDefault(); }
  }

  function end(event) {
    if (moved) {
      if (event) { event.stopPropagation(); if (event.cancelable) { event.preventDefault(); } }
      timer = requestAnimationFrame(glide);
    }
    document.documentElement.classList.remove('ha-dragging');
    active = false;
  }

  // Touch, where the panel reports it. passive:false so preventDefault is
  // allowed - a passive listener cannot stop the browser's own scrolling and
  // the two fight each other.
  document.addEventListener('touchstart', function (e) {
    if (e.touches.length !== 1) { return; }
    begin(e.touches[0].clientX, e.touches[0].clientY, e.target);
  }, {capture: true, passive: true});

  document.addEventListener('touchmove', function (e) {
    if (e.touches.length !== 1) { return; }
    move(e.touches[0].clientX, e.touches[0].clientY, e);
  }, {capture: true, passive: false});

  document.addEventListener('touchend', end, {capture: true, passive: false});
  document.addEventListener('touchcancel', end, {capture: true, passive: false});

  // Mouse, for a panel driven by a pointer. Left button only - a middle or
  // right press means something else.
  document.addEventListener('mousedown', function (e) {
    if (e.button !== 0) { return; }
    begin(e.clientX, e.clientY, e.target);
  }, true);

  document.addEventListener('mousemove', function (e) {
    move(e.clientX, e.clientY, e);
  }, true);

  document.addEventListener('mouseup', end, true);

  // The click that ends a drag is swallowed, so letting go over a link does
  // not follow it. Reset afterwards, or the next real tap is eaten too.
  document.addEventListener('click', function (e) {
    if (moved) { e.stopPropagation(); e.preventDefault(); moved = false; }
  }, true);
})();

(function(){
  if (window.__haFields) { return; }
  window.__haFields = true;

  // A page's own text fields would otherwise need a hardware keyboard. The
  // field is identified by an attribute rather than by a selector, because a
  // selector has to survive the page's own markup and an attribute we set
  // ourselves does not.
  var counter = 0;

  function editable(el) {
    if (!el || !el.tagName) { return false; }
    var tag = el.tagName.toLowerCase();
    if (tag === 'textarea') { return true; }
    if (el.isContentEditable) { return true; }
    if (tag !== 'input') { return false; }
    var type = (el.getAttribute('type') || 'text').toLowerCase();
    // What the field says about itself, beyond its type.
    //
    // A login form's email box is very often type="text" - Plex's is - so the
    // type alone identifies the password and nothing else. autocomplete is
    // the attribute that exists precisely to say "this is a username", and
    // name/id carry the same intent on forms that predate it.
    var hint = [
      el.getAttribute('autocomplete') || '',
      el.getAttribute('name') || '',
      el.getAttribute('id') || ''
    ].join(' ').toLowerCase();
    return ['text','search','url','email','tel','password','number',
            'date','time'].indexOf(type) !== -1;
  }

  function fieldLabel(el) {
    // A name attribute is the LAST resort, not the first.
    //
    // Google's search box is name="q" with no placeholder, so the dialog
    // was titled "q" - which says nothing about what is being typed. Every
    // other source is a phrase written for a person to read.
    var aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) { return aria.trim(); }

    var placeholder = el.getAttribute('placeholder');
    if (placeholder && placeholder.trim()) { return placeholder.trim(); }

    var title = el.getAttribute('title');
    if (title && title.trim()) { return title.trim(); }

    // A <label for="..."> pointing at this field, or one wrapped around it.
    if (el.id) {
      var tied = document.querySelector('label[for="' + el.id + '"]');
      if (tied && tied.textContent.trim()) { return tied.textContent.trim(); }
    }
    var wrapping = el.closest ? el.closest('label') : null;
    if (wrapping && wrapping.textContent.trim()) {
      return wrapping.textContent.trim().slice(0, 60);
    }

    var labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      var source = document.getElementById(labelledBy);
      if (source && source.textContent.trim()) {
        return source.textContent.trim().slice(0, 60);
      }
    }

    // Nothing the page offers is readable. Say where you are and what the
    // field probably does, which beats "q" and beats "Text".
    var host = location.hostname.replace(/^www[.]/, '');
    var type = (el.getAttribute('type') || '').toLowerCase();
    var name = (el.getAttribute('name') || '').toLowerCase();
    var searchy = type === 'search' || name === 'q' ||
                  /search|query/.test(name) ||
                  (el.getAttribute('role') || '') === 'searchbox';
    if (host) { return host + (searchy ? ' search' : ''); }
    return searchy ? 'Search' : 'Text';
  }

  function ask(el) {
    if (!el.hasAttribute('data-ha-field')) {
      el.setAttribute('data-ha-field', String(++counter));
    }
    var id = el.getAttribute('data-ha-field');
    var kind = el.tagName.toLowerCase() === 'textarea' ? 'body' :
               ((el.getAttribute('type') || 'text').toLowerCase() === 'number'
                 ? 'numeric' : 'text');
    var label = fieldLabel(el);
    var type = (el.getAttribute('type') || 'text').toLowerCase();
    // What the field says about itself, beyond its type.
    //
    // A login form's email box is very often type="text" - Plex's is - so the
    // type alone identifies the password and nothing else. autocomplete is
    // the attribute that exists precisely to say "this is a username", and
    // name/id carry the same intent on forms that predate it.
    var hint = [
      el.getAttribute('autocomplete') || '',
      el.getAttribute('name') || '',
      el.getAttribute('id') || ''
    ].join(' ').toLowerCase();
    var value = el.isContentEditable ? el.textContent : (el.value || '');
    // Through the console, not the title.
    //
    // document.title is a shared, observable thing: Google Analytics reads it
    // and sends it as the `dt` parameter, so every tap on a field published
    // the field's id, its label AND ITS CURRENT VALUE to whatever analytics
    // the page happens to run. It is also visibly wrong - the browser tab and
    // the panel's own address bar showed the JSON.
    //
    // The console is not transmitted anywhere and is already being read by
    // the page object below.
    console.log('__ha_field:' + JSON.stringify(
      {id: id, kind: kind, type: type, hint: hint, label: label,
       value: value, at: Date.now()}));
  }

  document.addEventListener('focusin', function (e) {
    // Ignored while we are the ones changing the field.
    //
    // Setting a value and submitting it makes the page put focus back, which
    // arrives here as a fresh focusin - so the dialog reopened the moment it
    // was dismissed, and pressing Done again did the same thing.
    if (window.__haWriting) { return; }
    if (editable(e.target)) {
      // Blurred first, or the engine keeps a caret blinking in a field the
      // person is no longer typing into.
      e.target.blur();
      ask(e.target);
    }
  }, true);

  window.__haSetField = function (id, text, submit) {
    var el = document.querySelector('[data-ha-field="' + id + '"]');
    if (!el) { return false; }
    // Held across the write and whatever focus it causes. Cleared on a timer
    // rather than at the end of this function: a page that refocuses does so
    // after its own handlers run, which is after this returns.
    window.__haWriting = true;
    setTimeout(function () { window.__haWriting = false; }, 1200);
    if (el.isContentEditable) { el.textContent = text; }
    else { el.value = text; }
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    if (!submit) { return true; }

    // Setting a value is not the same as submitting it.
    //
    // input and change tell a framework the field is different; neither tells
    // a page the person is done. A search box updated this way sat there with
    // the query in it and nothing happened, which is what pressing Done looked
    // like from the outside.
    //
    // Enter first, since sites that listen for it do so instead of using a
    // form, and a real form still gets requestSubmit() below.
    ['keydown', 'keypress', 'keyup'].forEach(function (kind) {
      el.dispatchEvent(new KeyboardEvent(kind, {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
        bubbles: true, cancelable: true
      }));
    });

    var form = el.form || (el.closest ? el.closest('form') : null);
    if (form) {
      try {
        // requestSubmit, not submit: it runs the page's own submit handlers
        // and its validation, where submit() skips both and can lose data the
        // page meant to attach.
        if (form.requestSubmit) { form.requestSubmit(); }
        else { form.submit(); }
      } catch (e) { /* the page refused it; Enter may still have worked */ }
    }
    return true;
  };
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
        # Anything calling into this later must ask for the same world -
        # runJavaScript defaults to the main one.
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


#Console output that says nothing about this panel.
#
#A page's CSP is the page's own configuration. Reporting it makes the log look
#like the panel is broken when the site is merely strict - and one page load
#can produce a dozen.
#Qt's console levels, as the panel's own.
#
#Resolved lazily rather than at import: QWebEngineCore may not be importable on
#a machine without the engine, and this module is imported either way.
def _js_levels() -> dict:
    try:
        from PyQt6.QtWebEngineCore import QWebEnginePage as _Page
        kind = _Page.JavaScriptConsoleMessageLevel
        return {kind.InfoMessageLevel: "info",
                kind.WarningMessageLevel: "warning",
                kind.ErrorMessageLevel: "error"}
    except Exception:
        return {}


_JS_LEVELS = _js_levels()

_IGNORED_CONSOLE = re.compile(
    r"Content Security Policy|Refused to (connect|apply|load|execute)"
    r"|violates the (following|document)", re.I)


def _locked_page(view, base: str):
    """
    The page object: a navigation guard, and the channel a field asks through.

    Installed whether or not there is a `base`. It used to be worth skipping
    when nothing was locked; it now also carries the field signal, so a page
    without it cannot open the keyboard at all.

    Navigation is refused outside `base` when one is set.

    Checked at the engine rather than by watching urlChanged and going back:
    by the time a URL has changed the page has already been fetched, and a
    kiosk showing the docs should not be one mis-tapped link away from the
    open internet.
    """
    try:
        from PyQt6.QtWebEngineCore import QWebEnginePage

        class _Locked(QWebEnginePage):
            #Set by the page that owns this, so a console line can be routed
            #back without the page needing to know what a WebPage is.
            on_field = None
            on_log = None

            def javaScriptConsoleMessage(self, level, message, line, source):
                """
                The page's own channel back.

                Everything else is left to the default, which is what puts
                a site's console output in the log - useful, and how the CSP
                refusals on this page were noticed at all.
                """
                text = str(message or "")
                if text.startswith("__ha_field:") and self.on_field:
                    try:
                        self.on_field(text[len("__ha_field:"):])
                    except Exception:
                        pass
                    return

                # A site's own Content Security Policy complaints are not the
                # panel's business.
                #
                # Scryfall refuses its own analytics because google.com is not
                # in its connect-src; that is between them, and it arrives
                # several times a page. Dropped rather than logged, so what is
                # left in the log is a page actually failing.
                if _IGNORED_CONSOLE.search(text):
                    return

                # Through the panel's log, not Qt's default.
                #
                # The base implementation writes to stderr with a "js:" prefix.
                # That is not timestamped, carries no level, never reaches the
                # log file, and so never appears on the Logs page - which is
                # where somebody looking for it would look.
                if self.on_log:
                    where = f" ({source}:{line})" if source else ""
                    self.on_log(_JS_LEVELS.get(level, "info"),
                                f"[WebPage] {text[:400]}{where}")
                    return
                super().javaScriptConsoleMessage(level, message, line, source)

            def acceptNavigationRequest(self, url, kind, is_main_frame):
                if not base or not is_main_frame:
                    return True

                # Back and forward always go through.
                #
                # They can only reach somewhere this session has already been,
                # so they cannot escape to anywhere new - and refusing them
                # left the two most obvious buttons in the toolbar doing
                # nothing, which reads as the page being broken rather than
                # locked.
                try:
                    from PyQt6.QtWebEngineCore import QWebEnginePage
                    if kind == QWebEnginePage.NavigationType.NavigationTypeBackForward:
                        return True
                except Exception:
                    pass

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
    #Served by the panel's own backend: bookmarks, a clock and a search box.
    #
    #about:blank was the old home, which is a white rectangle - a browser
    #opening on nothing gives somebody standing at a wall panel no way in
    #except the address bar, and typing a URL on a touch keyboard is the thing
    #a home page exists to avoid.
    HOME = "http://127.0.0.1:5000/webhome"

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
        # Resumed when the caller asked for nothing.
        #
        # A caller naming a page means go there. The night clock returning, or a
        # swipe back, passes no data at all - and rebuilding from HOME throws
        # away whatever was being read. The saved entry carries the lock it was
        # opened under, so resuming does not quietly drop it.
        saved = {} if data.get("url") else (self._store().get("webpage") or {})

        self.home_url = data.get("home") or saved.get("home") or self.HOME
        self.lock_address = bool(data.get("lock_address",
                                          saved.get("lock_address", False)))
        self.lock_base = (data.get("lock_base")
                          or saved.get("lock_base") or "").strip()

        start = data.get("url") or self._remembered_url() or self.home_url

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.PADDING, self.PADDING,
                                 self.PADDING, self.PADDING)
        outer.setSpacing(0)

        outer.addLayout(self._build_bar())

        # A two-pixel line rather than a spinner: it says "still working"
        # without taking any room from the page.
        # Tall enough to notice from standing height. Two pixels is a hairline
        # on a 1440 panel and reads as an artifact rather than as progress.
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setRange(0, 100)
        self.progress.hide()
        set_style(self.progress, "common", "load-bar")
        outer.addWidget(self.progress)

        self.view = _web_view()
        if self.view is not None:
            self._locked = _locked_page(self.view, self.lock_base)
            if self._locked is not None:
                self._locked.on_field = self._field_requested
                self._locked.on_log = self.client.log
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
            self.view.loadStarted.connect(self._load_started)
            self.view.loadProgress.connect(self._progress)
            self.view.titleChanged.connect(self._title_changed)
            self.zoom = data.get("zoom") or self.DEFAULT_ZOOM
            self.view.setZoomFactor(self.zoom)
            self._show_zoom()
            self.view.urlChanged.connect(self._url_changed)
            self.view.loadFinished.connect(self._on_load_finished)

            # Polled, because there is no scroll signal to connect to.
            #
            # QWebEngineView does not emit one, and the alternative - injecting
            # a listener that posts back on every scroll event - is a message
            # per frame while a finger is dragging. Two seconds is close enough
            # for "where I was" and costs one tiny JavaScript call.
            self._scroll_timer = QTimer(self)
            self._scroll_timer.setInterval(2000)
            self._scroll_timer.timeout.connect(self._remember_scroll)
            self._scroll_timer.start()
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
        # Filled when the page on screen is already saved, so the button says
        # what pressing it will do rather than only what it is for.
        self.bookmark_btn = IconButton("mdi.star-outline",
                                       self.toggle_bookmark, size=20)

        for button in (self.back_btn, self.forward_btn, self.reload_btn,
                       self.home_btn, self.bookmark_btn):
            row.addWidget(button)

        # Zoom, because the single most common problem with a web page on a
        # wall panel is that it was laid out for somebody sitting 60cm away.
        self.zoom_out_btn = IconButton("mdi.magnify-minus-outline",
                                       lambda: self.zoom_by(-1), size=20)
        self.zoom_in_btn  = IconButton("mdi.magnify-plus-outline",
                                       lambda: self.zoom_by(1), size=20)
        self.zoom_label = QLabel("")
        self.zoom_label.setFont(make_font(SIZES.S1, bold=True))
        # A minimum, not a fixture. "250%" in bold does not fit 46px, and a
        # fixed width has nowhere to put the overflow.
        self.zoom_label.setMinimumWidth(58)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setCursor(Qt.CursorShape.PointingHandCursor)
        # Transparent, not themed. The bar sits over the page and a filled
        # label reads as a control that does something.
        self.zoom_label.setStyleSheet(
            "color: rgba(255,255,255,190); background: transparent; border: 0;")
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
        self.address.setStyleSheet(
            "color: rgba(255,255,255,225); background: transparent;"
            "border: 0; border-bottom: 1px solid rgba(255,255,255,40);"
            "padding: 2px 6px;")
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
        host.setStyleSheet("background: transparent;")
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
        self.client.web_event("home", url=self.home_url)
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
            try:
                self.client.web_event("refreshed",
                                      url=self.view.url().toString())
            except RuntimeError:
                pass

    def _leave(self, event=None) -> None:
        self.client.goto(self.client.DEFAULT_PAGE or "#root")

    ## -- fields in the page

    def _title_changed(self, title: str) -> None:
        """
        The page's real title, for the address bar.

        The field signal used to come through here. document.title is read by
        analytics and sent as the `dt` parameter, so every tap published the
        field's label and its current value to whichever tracker the page runs
        - and the JSON was visible in the panel's own title. It goes through
        the console now; see the page object in _locked_page().
        """
        if title.startswith("field:"):
            # An older cached script, from a page loaded before this changed.
            self._field_requested(title[len("field:"):])

    #Fields filled but never submitted. See _field_requested.
    NO_SUBMIT_TYPES = ("password", "email")
    #And the words that mean the same thing when the type does not say so.
    #
    #Matched against autocomplete, name and id together. "user" rather than
    #"username" because forms use both, and "login" because plenty of them
    #call the field that. Deliberately NOT "search" or "query": those want
    #submitting, which is the whole point of them.
    NO_SUBMIT_HINTS = ("password", "passwd", "email", "username", "user",
                       "login", "signin", "sign-in")

    def _is_login_field(self, data: dict) -> bool:
        """
        Whether this is something to fill and leave alone.

        The type first, since a password field always says so. Then what the
        field calls itself: a login form's email box is very often
        type="text" - Plex's is - and `autocomplete="username"` is the
        attribute that exists to say what it really is.
        """
        if str(data.get("type", "")).lower() in self.NO_SUBMIT_TYPES:
            return True
        hint = str(data.get("hint", "")).lower()
        return any(word in hint for word in self.NO_SUBMIT_HINTS)

    def _field_requested(self, payload: str) -> None:
        """A text field in the page was tapped - offer the keyboard for it."""
        import json
        from PyQt6.QtWidgets import QLineEdit, QTextEdit
        from src.ui.keyboard import KeyboardDialog

        try:
            data = json.loads(payload)
        except ValueError:
            return

        # A throwaway widget as the keyboard's target. The dialog writes into
        # a Qt widget by design and the page is not one, so it types into this
        # and the result is handed across afterwards.
        holder = QTextEdit() if data.get("kind") == "body" else QLineEdit()
        if isinstance(holder, QTextEdit):
            holder.setPlainText(data.get("value", ""))
        else:
            holder.setText(data.get("value", ""))

        def done(text: str):
            if self.view is None:
                return
            # ApplicationWorld, explicitly. The helper was injected into that
            # world so a page could not tamper with it, but runJavaScript
            # defaults to the main one - so it was calling into a world where
            # __haSetField does not exist, and nothing happened.
            from PyQt6.QtWebEngineCore import QWebEngineScript
            # Filled, and usually submitted. Done means done - a value left
            # sitting in a search box is indistinguishable from the dialog
            # having failed.
            #
            # Not for a login, though. Submitting a password the moment it is
            # typed sends the form with whatever the OTHER field happens to
            # hold - usually nothing, because the email is filled second half
            # the time - and a failed sign-in attempt is not something to
            # trigger on somebody's behalf. Fill it and get out of the way.
            submit = not self._is_login_field(data)
            self.view.page().runJavaScript(
                "window.__haSetField(%s, %s, %s);" % (
                    json.dumps(str(data.get("id", ""))), json.dumps(text),
                    "true" if submit else "false"),
                QWebEngineScript.ScriptWorldId.ApplicationWorld)

        self.client.dialog(KeyboardDialog(
            self.client, holder, mode=data.get("kind", "text"),
            label=data.get("label") or "Text", on_done=done))

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

    def _load_started(self) -> None:
        """
        Show the bar as soon as a load begins, not when progress arrives.

        loadProgress reports nothing until the server answers, and the wait
        before that - DNS, the connection, TLS - is the part that feels like
        the panel has stopped. Hiding the bar at zero meant showing nothing
        during exactly the period somebody needs to be told something is
        happening.
        """
        try:
            # Indeterminate: a bar at zero looks like a bar that is stuck,
            # while a busy one says "waiting" without claiming a fraction it
            # does not know.
            self.progress.setRange(0, 0)
            self.progress.show()
        except RuntimeError:
            pass

    def _progress(self, value: int) -> None:
        try:
            if value <= 0:
                # Still waiting to hear back.
                self.progress.setRange(0, 0)
                self.progress.show()
                return
            self.progress.setRange(0, 100)
            self.progress.setValue(value)
            self.progress.setVisible(value < 100)
        except RuntimeError:
            pass

    ## -- state

    def _url_changed(self, url) -> None:
        try:
            self.address.setText(url.toString())
        except RuntimeError:
            pass
        self._remember(url.toString())
        self._refresh_buttons()
        self._refresh_bookmark_button()
        self.client.web_event("changed", url=url.toString())

    ## -- where it was

    #Kept on the CLIENT, not on the page.
    #
    #goto() destroys the outgoing page, so anything stored on `self` goes with
    #it. Coming back from the night clock rebuilt this page from `data["url"]`,
    #which is whatever the original caller passed - so the address was the one
    #it opened on and the scroll position was the top, whatever had been read
    #in between.
    STATE_ATTR = "_webpage_state"

    def _store(self) -> dict:
        state = getattr(self.client, self.STATE_ATTR, None)
        if not isinstance(state, dict):
            state = {}
            setattr(self.client, self.STATE_ATTR, state)
        return state

    def _state_key(self) -> str:
        """
        One entry, and the context is stored inside it.

        Keying on `lock_base` and `home_url` did not work: both come from the
        `data` the page was opened with, and the return path - the night clock
        calling `goto(key)` with nothing - has no data to rebuild them from. The
        key computed on the way back was therefore not the key written on the
        way in, and nothing was ever found.

        So the context travels *with* the entry rather than identifying it, and
        a resume restores the lock along with the address.
        """
        return "webpage"

    def _remember(self, url: str) -> None:
        if not url or url.startswith("about:"):
            return
        entry = self._store().setdefault(self._state_key(), {})
        entry["url"] = url
        # The context, so a resume comes back to the same place under the same
        # restrictions rather than to a bare browser at the same address.
        entry["lock_base"] = self.lock_base
        entry["lock_address"] = self.lock_address
        entry["home"] = self.home_url

    def _remember_scroll(self) -> None:
        """
        Ask the page where it is scrolled to, and keep the answer.

        Asked continuously rather than on the way out: teardown gives no chance
        to wait for a JavaScript result, and a value that arrives after the view
        is gone is a value nobody can store.
        """
        if self.view is None:
            return

        def keep(value):
            try:
                position = int(float(value or 0))
            except (TypeError, ValueError):
                return
            entry = self._store().setdefault(self._state_key(), {})
            entry["scroll"] = max(0, position)

        try:
            self.view.page().runJavaScript("window.scrollY", keep)
        except Exception:
            pass

    def _restore_scroll(self) -> None:
        entry = self._store().get(self._state_key()) or {}
        position = int(entry.get("scroll") or 0)
        if position <= 0 or self.view is None:
            return
        try:
            # Instant, not smooth: an animated jump on a restored page looks
            # like the page moving under you rather than like where you were.
            self.view.page().runJavaScript(
                f"window.scrollTo({{top: {position}, behavior: 'instant'}});")
        except Exception:
            pass

    ## -- bookmarks

    def toggle_bookmark(self) -> None:
        """
        Save the page on screen, or drop it if it is already saved.

        The icon comes from the view rather than from the network: the engine
        has already downloaded the favicon to draw with, and asking again would
        need the network up at the exact moment somebody pressed the button.
        """
        if self.view is None:
            return
        try:
            url = self.view.url().toString()
            title = self.view.title() or ""
        except RuntimeError:
            return
        if not url or url.startswith("about:"):
            return

        store = self.client.BOOKMARKS
        if store.has(url):
            store.remove(url)
            self.client.web_event("unbookmarked", url=url, title=title)
        else:
            icon = None
            try:
                icon = self.view.icon()
            except Exception:
                icon = None
            store.add(url, title=title, icon=icon)
            self.client.web_event("bookmarked", url=url, title=title)
        self._refresh_bookmark_button()

    def _refresh_bookmark_button(self) -> None:
        """Filled when this page is saved, hollow when it is not."""
        button = getattr(self, "bookmark_btn", None)
        if button is None or self.view is None:
            return
        try:
            saved = self.client.BOOKMARKS.has(self.view.url().toString())
            button.update_icon("mdi.star" if saved else "mdi.star-outline",
                               "#ffd479" if saved else "white")
        except (RuntimeError, Exception):
            pass

    def _on_load_finished(self, ok: bool) -> None:
        # Hidden here as well as at 100. A load that fails, or one the engine
        # abandons, never reports 100 - and the bar would spin for good.
        try:
            self.progress.setRange(0, 100)
            self.progress.hide()
        except RuntimeError:
            pass
        self._refresh_buttons()
        self._refresh_bookmark_button()
        if ok:
            self._restore_scroll()

        try:
            url = self.view.url().toString() if self.view else ""
            title = self.view.title() if self.view else ""
        except RuntimeError:
            url, title = "", ""
        # A failed load is its own kind. Anything listening for trouble should
        # not have to notice that a "loaded" arrived with nothing behind it.
        self.client.web_event("loaded" if ok else "error",
                              url=url, title=title, ok=bool(ok))

    def _remembered_url(self) -> str:
        entry = self._store().get(self._state_key()) or {}
        url = str(entry.get("url") or "")
        if not url:
            return ""
        # Refused if it has drifted outside the lock this context was opened
        # with - a remembered address is not a way around it.
        if self.lock_base and not url.startswith(self.lock_base):
            return ""
        return url

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
