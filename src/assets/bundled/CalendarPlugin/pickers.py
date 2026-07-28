from __future__ import annotations

import calendar as calendar_module
import json
import urllib.parse
import urllib.request
from datetime import date, datetime
from threading import Thread
from typing import TYPE_CHECKING, Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter, QColor

from src.ui.controls.buttons import IconButton
# Moved to the client so the timer picker can use the same control - two
# copies of a stepper would drift apart.
from src.ui.controls.stepper import Stepper as _Stepper
from src.ui.icons import icon
from src.styling import make_font, SIZES, set_style

from .dialogs import _WideDialog

# A pannable, zoomable map needs a browser engine. PyQt6-WebEngine is a
# separate wheel and is often absent on a panel build, so it is optional and
# the static tile is what happens without it - a picture of the right place
# beats a blank box, and the address is the part that gets saved either way.
def _locked_page(view):
    """
    Refuse every navigation the page did not start itself.

    Leaflet's attribution is a pair of links, and on a touchscreen they are
    easy to hit - following one replaces the map with a web page and there is
    no back button on a wall panel to get out of it.
    """
    try:
        from PyQt6.QtWebEngineCore import QWebEnginePage

        class _Locked(QWebEnginePage):
            def acceptNavigationRequest(self, url, kind, is_main_frame):
                # Typed is what setHtml() uses; anything else is a link, a
                # form, or a redirect, and none of those belong here.
                return kind == QWebEnginePage.NavigationType.NavigationTypeTyped

            def createWindow(self, _kind):
                return None      # no popups either

        page = _Locked(view)
        view.setPage(page)
    except Exception:
        pass


def _web_view():
    """
    Build a QWebEngineView.

    Still wrapped, but no longer as a supported fallback: PyQt6-WebEngine is a
    dependency now. It imports fine on a machine where it cannot actually
    start - it needs AA_ShareOpenGLContexts set before the QApplication exists
    - so a failure here means a broken install rather than a missing extra,
    and the caller says so instead of quietly showing something lesser.
    """
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        view = QWebEngineView()
        _locked_page(view)
        try:
            # Set on the page, before anything loads. A QWebEngineView paints
            # white while a document is being replaced, which on setHtml is a
            # full-frame flash every time the map moves.
            from PyQt6.QtGui import QColor as _QColor
            view.page().setBackgroundColor(_QColor("#12141a"))
        except Exception:
            pass
        return view
    except Exception:
        return None


# The standard OpenStreetMap style already draws shops, restaurants, schools
# and the rest as labelled icons - the data is in the tiles. CARTO's dark_all
# is a deliberately minimal basemap that strips every POI label, which is why
# the dark map looked empty while OSM plainly knows what is there.
#
# So the light style is the default: seeing the places without asking for them
# is worth more than the map matching the panel's colour scheme. Dark is still
# available and the setting says what it costs.
LIGHT_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DARK_TILES  = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"


# Picks come back through document.title. QWebChannel is the proper route and
# needs a transport object, a registered bridge and a JS shim on the page;
# titleChanged is one signal and carries everything needed here.
LEAFLET_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{margin:0;height:100%;background:#12141a}
.leaflet-control-attribution{font-size:9px;opacity:.55}</style>
</head><body><div id="map"></div><script>
var map = L.map('map', {zoomControl: true}).setView([__LAT__, __LON__], __ZOOM__);
L.tileLayer('__TILES__', {maxZoom: 19, subdomains: 'abcd',
            attribution: 'OpenStreetMap / CARTO'}).addTo(map);

var chosen = null;
function mark(lat, lon) {
    if (chosen) { map.removeLayer(chosen); }
    chosen = L.circleMarker([lat, lon], {radius: 10, color: '#2ff08e',
             fillColor: '#2ff08e', fillOpacity: .9, weight: 3}).addTo(map);
}
function pick(lat, lon) {
    mark(lat, lon);
    // Nudged with a counter so two picks of the same point still register as
    // a change - titleChanged only fires when the string actually differs.
    document.title = 'pick:' + lat + ',' + lon + ':' + (Date.now());
}

var places = __PLACES__;
places.forEach(function (p) {
    L.circleMarker([p.lat, p.lon], {radius: 7, color: '#7fb2ff',
        fillColor: '#7fb2ff', fillOpacity: .75, weight: 2})
     .addTo(map).bindTooltip(p.name)
     .on('click', function (e) { L.DomEvent.stop(e); pick(p.lat, p.lon); });
});

map.on('click', function (e) { pick(e.latlng.lat, e.latlng.lng); });
if (__MARK__) { mark(__LAT__, __LON__); }
</script></body></html>"""


class MapView(QWidget):
    """A live map where the engine exists, a rendered one where it does not."""

    TILE = 256
    DARK = False     # the light style is the one that shows places

    def __init__(self, height: int = 420, expanding: bool = True,
                 client=None):
        super().__init__()
        if client is not None:
            try:
                self.DARK = bool(client.public.calendar["option"](
                    "general.dark_map", False))
            except Exception:
                pass
        self.setMinimumHeight(height)
        if expanding:
            # Fills whatever the dialog has. A map in a fixed band with empty
            # space under it is the one thing worse than no map.
            self.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.web = _web_view()
        self.fallback = QLabel("Search to see a map.")
        self.fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fallback.setFont(make_font(SIZES.S1))
        set_style(self.fallback, "settings", "setting-block")

        if self.web is not None:
            layout.addWidget(self.web)
        else:
            layout.addWidget(self.fallback)

    def show_point(self, lat, lon, client=None, places=None,
                   zoom: int = 16, mark: bool = True) -> None:
        if self.web is not None:
            import json as _json
            payload = [
                {"lat": float(p.get("lat")), "lon": float(p.get("lon")),
                 "name": (p.get("display_name") or "").split(",")[0]}
                for p in (places or [])
                if p.get("lat") and p.get("lon")
            ]
            self.web.setHtml(
                LEAFLET_PAGE
                .replace("__LAT__", str(lat))
                .replace("__LON__", str(lon))
                .replace("__ZOOM__", str(zoom))
                .replace("__MARK__", "true" if mark else "false")
                .replace("__PLACES__", _json.dumps(payload))
                .replace("__TILES__", DARK_TILES if self.DARK else LIGHT_TILES))
            return
        self._failed("PyQt6-WebEngine is not available.")

    ## -- picking

    def on_picked(self, callback) -> None:
        """callback(lat, lon) whenever a marker or the map itself is tapped."""
        self._picked = callback
        if self.web is not None:
            self.web.titleChanged.connect(self._title_changed)

    def _title_changed(self, title: str) -> None:
        if not title.startswith("pick:") or not callable(getattr(self, "_picked", None)):
            return
        try:
            body = title.split(":", 1)[1]
            lat, lon = body.split(":")[0].split(",")
            self._picked(float(lat), float(lon))
        except (ValueError, IndexError):
            pass

    def set_message(self, text: str) -> None:
        if self.web is None:
            self.fallback.setText(text)

    def _failed(self, reason: str) -> None:
        """No engine. Says why rather than showing an empty frame."""
        self.fallback.setText(
            f"The map needs a browser engine.\n\n{reason}\n\n"
            "pip install PyQt6-WebEngine")

class TimePickerDialog(_WideDialog):
    """
    Hours and minutes as steppers rather than a typed field.

    A time is two small numbers with hard bounds, and typing it on an on-screen
    keyboard means switching to the numeric layout, getting the colon right and
    being told off for 25:70. Steppers cannot produce an invalid time at all.
    """

    # Fits its content. Two steppers and a row of buttons do not need a
    # screen-sized dialog, and stretching them across one just separates the
    # two numbers you are comparing.
    WIDTH_RATIO  = 0.40
    HEIGHT_RATIO = 0.0          # 0 means "no minimum, shrink to content"
    MIN_WIDTH    = 420
    MINUTE_STEP  = 5

    def __init__(self, client: "Client", value: str = "",
                 title: str = "Pick a time", on_chosen: Callable = None,
                 floor: str = ""):
        super().__init__(client, title, "")
        self.on_chosen = on_chosen
        # The other end of the pair, when it is already set. An end time that
        # starts at midnight when the event begins at two in the afternoon is
        # a guaranteed extra dozen taps.
        self.floor = floor

        seed = value or floor or "09:00"
        try:
            hour, _, minute = seed.partition(":")
            hour, minute = int(hour), int(minute or 0)
        except ValueError:
            hour, minute = 9, 0

        row = QHBoxLayout()
        row.setSpacing(24)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.hours = _Stepper("Hour", max(0, min(23, hour)), 0, 23,
                              on_change=lambda _: self._update_readout())
        self.minutes = _Stepper("Minute", max(0, min(59, minute)), 0, 59,
                                on_change=lambda _: self._update_readout())

        row.addWidget(self.hours)
        colon = QLabel(":")
        colon.setFont(make_font(SIZES.L1, bold=True))
        set_style(colon, "common", "text-muted")
        row.addWidget(colon)
        row.addWidget(self.minutes)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(row)
        self.content.addWidget(holder)

        self.readout = QLabel("")
        self.readout.setFont(make_font(SIZES.M1))
        self.readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.readout, "common", "text-muted")
        self.content.addWidget(self.readout)
        self._update_readout()

        # Coarse and fine, rather than one or the other. Fives get you across
        # an hour quickly; the steppers themselves still move a minute at a
        # time for the cases that need it.
        jumps = QHBoxLayout()
        jumps.setSpacing(8)
        jumps.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for label, delta in (("-15", -15), ("-5", -5), ("+5", 5), ("+15", 15)):
            button = QPushButton(label)
            button.setFont(make_font(SIZES.S2, bold=True))
            button.setFixedHeight(42)
            button.setMinimumWidth(72)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(button, "overlays", "dialog-button-secondary")
            button.clicked.connect(lambda _=False, d=delta: self._jump(d))
            jumps.addWidget(button)
        self.content.addLayout(jumps)

        self.add_button("Use this time", self._accept, "primary")
        self.add_button("All day", self._clear, "secondary")
        self.add_button("Cancel", self.close, "secondary")

    def _jump(self, minutes: int) -> None:
        """Move the whole time, so -15 from 09:05 is 08:50 rather than 09:50."""
        total = (self.hours.value * 60 + self.minutes.value + minutes) % (24 * 60)
        self.hours.value, self.minutes.value = divmod(total, 60)
        self.hours._show()
        self.minutes._show()
        self._update_readout()

    def _update_readout(self) -> None:
        moment = datetime(2000, 1, 1, self.hours.value, self.minutes.value)
        self.readout.setText(moment.strftime("%I:%M %p").lstrip("0"))

    def value(self) -> str:
        return f"{self.hours.value:02d}:{self.minutes.value:02d}"

    def _accept(self) -> None:
        if callable(self.on_chosen):
            self.on_chosen(self.value())
        self.close()

    def _clear(self) -> None:
        if callable(self.on_chosen):
            self.on_chosen("")
        self.close()


class DatePickerDialog(_WideDialog):
    """A month at a time, with month and year stepping above it."""

    # Half the screen. A date picker is a fixed seven-by-six grid - giving it
    # the whole display just spreads six rows of buttons across it.
    WIDTH_RATIO  = 0.46
    HEIGHT_RATIO = 0.52
    MIN_WIDTH    = 420
    CHEVRON      = 26

    def __init__(self, client: "Client", value: date = None,
                 title: str = "Pick a date", on_chosen: Callable = None):
        super().__init__(client, title, "")
        self.on_chosen = on_chosen
        self.chosen = value or date.today()
        self.year, self.month = self.chosen.year, self.chosen.month

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(IconButton("mdi.chevron-double-left",
                                    lambda: self._step_year(-1), size=self.CHEVRON))
        header.addWidget(IconButton("mdi.chevron-left",
                                    lambda: self._step_month(-1), size=self.CHEVRON))
        self.title_label = QLabel("")
        self.title_label.setFont(make_font(SIZES.M1, bold=True))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.title_label, "common", "text-strong")
        header.addWidget(self.title_label, stretch=1)
        header.addWidget(IconButton("mdi.chevron-right",
                                    lambda: self._step_month(1), size=self.CHEVRON))
        header.addWidget(IconButton("mdi.chevron-double-right",
                                    lambda: self._step_year(1), size=self.CHEVRON))
        self.content.addLayout(header)

        self.grid = QGridLayout()
        self.grid.setSpacing(4)
        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(self.grid)
        holder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # stretch, so the grid takes the dialog rather than sitting in a band
        # at the top of it with empty space underneath.
        self.content.addWidget(holder, stretch=1)

        self.cells: list = []
        self.rebuild()

        self.add_button("Use this date", self._accept, "primary")
        self.add_button("Today", self._today, "secondary")
        self.add_button("Cancel", self.close, "secondary")

    def _step_month(self, by: int) -> None:
        month = self.month + by
        self.year += (month > 12) - (month < 1)
        self.month = 1 if month > 12 else (12 if month < 1 else month)
        self.rebuild()

    def _step_year(self, by: int) -> None:
        self.year += by
        self.rebuild()

    def _today(self) -> None:
        self.chosen = date.today()
        self.year, self.month = self.chosen.year, self.chosen.month
        self.rebuild()

    def rebuild(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.cells = []

        self.title_label.setText(
            f"{calendar_module.month_name[self.month]} {self.year}")

        for column in range(7):
            self.grid.setColumnStretch(column, 1)
        for row in range(1, 7):
            self.grid.setRowStretch(row, 1)

        for column, name in enumerate(["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]):
            head = QLabel(name)
            head.setFont(make_font(SIZES.S1, bold=True))
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_style(head, "common", "text-muted")
            self.grid.addWidget(head, 0, column)

        weeks = calendar_module.Calendar(firstweekday=6).monthdatescalendar(
            self.year, self.month)
        for row, week in enumerate(weeks, start=1):
            for column, day in enumerate(week):
                button = QPushButton(str(day.day))
                button.setFont(make_font(SIZES.S2,
                                         bold=(day == self.chosen)))
                button.setMinimumHeight(46)
                button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Expanding)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                # Days from the neighbouring months are shown but not
                # selectable - tapping one and silently jumping months is
                # disorienting on a grid this small.
                in_month = (day.month == self.month)
                button.setEnabled(in_month)
                set_style(button, "overlays",
                          "dialog-button-primary" if day == self.chosen
                          else "dialog-button-secondary")
                if in_month:
                    button.clicked.connect(lambda _=False, d=day: self._choose(d))
                self.grid.addWidget(button, row, column)
                self.cells.append(button)

    def _choose(self, day: date) -> None:
        self.chosen = day
        self.rebuild()

    def _accept(self) -> None:
        if callable(self.on_chosen):
            self.on_chosen(self.chosen)
        self.close()


class PlaceCache:
    """
    Remembers what was found for an area, so the same city is looked up once.

    Encrypted with Fernet, keyed from the install's own identifier. Worth
    being clear about what that buys: the contents are public OpenStreetMap
    data, so this protects the record of *what was searched for*, not the
    places themselves.
    """

    VERSION = 1

    def __init__(self, path, client=None):
        self.path = path
        self.client = client
        self.entries: dict = {}
        self._fernet = self._make_fernet(client)
        self.load()

    @staticmethod
    def _make_fernet(client):
        try:
            import base64, hashlib
            from cryptography.fernet import Fernet
            seed = str(getattr(client, "CLIENT_ID", "") or "homeassistant")
            key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())
            return Fernet(key)
        except Exception:
            return None

    ## -- disk

    def load(self) -> None:
        try:
            if not self.path.is_file():
                return
            raw = self.path.read_bytes()
            if self._fernet is not None and raw.startswith(b"gAAAAA"):
                raw = self._fernet.decrypt(raw)
            payload = json.loads(raw.decode("utf-8"))
            if payload.get("version") == self.VERSION:
                self.entries = payload.get("areas") or {}
        except Exception as e:
            # A cache that will not load is a cache that gets rebuilt, not an
            # error worth showing anyone.
            self.entries = {}
            if self.client is not None:
                self.client.log("debug", f"[Calendar] Place cache unreadable: {e}")

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps({"version": self.VERSION,
                               "areas": self.entries}).encode("utf-8")
            if self._fernet is not None:
                body = self._fernet.encrypt(body)
            else:
                # Only reachable on a broken install. Better an unencrypted
                # cache than losing the feature, but it is worth a line.
                self.client.log("warning", "[Calendar] cryptography missing - "
                                           "the place cache is not encrypted.")
            self.path.write_bytes(body)
        except Exception as e:
            if self.client is not None:
                self.client.log("debug", f"[Calendar] Could not save place cache: {e}")

    ## -- use

    @staticmethod
    def key(lat, lon, box) -> str:
        """Rounded, so the same city from two slightly different taps agrees."""
        if box and len(box) == 4:
            return "box:" + ",".join(f"{float(v):.3f}" for v in box)
        return f"pt:{float(lat):.3f},{float(lon):.3f}"

    def get(self, lat, lon, box):
        return self.entries.get(self.key(lat, lon, box))

    def put(self, lat, lon, box, places: list) -> None:
        self.entries[self.key(lat, lon, box)] = places
        # Bounded. This is a convenience, not a database.
        if len(self.entries) > 60:
            for stale in list(self.entries)[:-60]:
                del self.entries[stale]
        self.save()

    def forget(self, lat, lon, box) -> None:
        self.entries.pop(self.key(lat, lon, box), None)
        self.save()


# OSM category to glyph. Only the kinds a person actually puts on a calendar -
# anything unmapped gets the generic pin rather than a wrong picture.
KIND_ICONS = {
    "restaurant": "mdi.silverware-fork-knife", "fast food": "mdi.hamburger",
    "cafe": "mdi.coffee", "bar": "mdi.glass-mug-variant",
    "pub": "mdi.glass-mug-variant", "biergarten": "mdi.glass-mug-variant",
    "bakery": "mdi.bread-slice", "supermarket": "mdi.cart",
    "convenience": "mdi.storefront", "mall": "mdi.shopping",
    "clothes": "mdi.tshirt-crew", "hairdresser": "mdi.content-cut",
    "bank": "mdi.bank", "atm": "mdi.cash", "pharmacy": "mdi.pill",
    "hospital": "mdi.hospital-box", "clinic": "mdi.hospital-box",
    "doctors": "mdi.stethoscope", "dentist": "mdi.tooth",
    "veterinary": "mdi.paw", "school": "mdi.school",
    "university": "mdi.school", "college": "mdi.school",
    "library": "mdi.book-open-variant", "museum": "mdi.bank-outline",
    "cinema": "mdi.movie-open", "theatre": "mdi.drama-masks",
    "place of worship": "mdi.church", "post office": "mdi.email",
    "police": "mdi.police-badge", "fire station": "mdi.fire-truck",
    "fuel": "mdi.gas-station", "parking": "mdi.parking",
    "charging station": "mdi.ev-station", "hotel": "mdi.bed",
    "guest house": "mdi.bed", "park": "mdi.tree", "garden": "mdi.flower",
    "playground": "mdi.slide", "fitness centre": "mdi.dumbbell",
    "gym": "mdi.dumbbell", "sports centre": "mdi.basketball",
    "swimming pool": "mdi.pool", "attraction": "mdi.star",
    "hardware": "mdi.hammer-wrench", "car repair": "mdi.car-wrench",
}


def icon_for(kind: str) -> str:
    return KIND_ICONS.get((kind or "").strip().lower(), "mdi.map-marker")


class _PlaceRow(QFrame):
    """
    A tappable row: glyph, name, and each piece of metadata on its own line.

    Everything used to be joined with separators into one paragraph, which
    reads as a wall of text when four fields are present and looks broken when
    only one is.
    """

    FIELDS = (
        ("kind",          "mdi.tag-outline"),
        ("cuisine",       "mdi.silverware"),
        ("address",       "mdi.map-marker-outline"),
        ("opening_hours", "mdi.clock-outline"),
        ("phone",         "mdi.phone-outline"),
        ("website",       "mdi.web"),
    )

    def __init__(self, entry: dict, subtitle: str, on_pick):
        super().__init__()
        self.entry   = entry
        self.on_pick = on_pick

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        set_style(self, "settings", "setting-block")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 9, 10, 9)
        outer.setSpacing(10)

        glyph = QLabel()
        try:
            glyph.setPixmap(icon(icon_for(entry.get("kind") or subtitle),
                                 color="#7fb2ff").pixmap(24, 24))
        except Exception:
            pass
        glyph.setFixedWidth(28)
        glyph.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(glyph)

        column = QVBoxLayout()
        column.setSpacing(3)

        name = QLabel(entry.get("display_name", ""))
        name.setFont(make_font(SIZES.S1, bold=True))
        name.setWordWrap(True)
        set_style(name, "common", "text-strong")
        column.addWidget(name)

        for key, field_icon in self.FIELDS:
            value = str(entry.get(key) or "").strip()
            if not value or (key == "kind" and not entry.get("lat")):
                continue
            line = QHBoxLayout()
            line.setSpacing(6)

            mark = QLabel()
            try:
                mark.setPixmap(icon(field_icon, color="#8a8f99").pixmap(13, 13))
            except Exception:
                pass
            mark.setFixedWidth(15)
            mark.setAlignment(Qt.AlignmentFlag.AlignTop)
            line.addWidget(mark)

            text = QLabel(value)
            text.setFont(make_font(11))
            text.setWordWrap(True)
            set_style(text, "common", "text-muted")
            line.addWidget(text, stretch=1)
            column.addLayout(line)

        outer.addLayout(column, stretch=1)

    def mouseReleaseEvent(self, event) -> None:
        self.on_pick(self.entry)


class LocationPickerDialog(_WideDialog):
    """
    Search for somewhere, see it on a map, keep the name it is known by.

    The stored value is the display name rather than coordinates, because that
    is what a person reads off the event later - the map is regenerated from it
    when the event is opened.
    """

    WIDTH_RATIO  = 0.92
    HEIGHT_RATIO = 0.9
    MAP_H = 460

    def __init__(self, client: "Client", value: str = "",
                 on_chosen: Callable = None):
        super().__init__(client, "Where is it?", "")
        self.on_chosen = on_chosen
        self.chosen = value
        self.results: list = []

        search = QHBoxLayout()
        search.setSpacing(8)

        self.query = QLineEdit(value)
        self.query.setPlaceholderText("Search for a place or address")
        self.query.setFont(make_font(SIZES.S2))
        self.query.setFixedHeight(46)
        self.query.setReadOnly(True)
        self.query.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(self.query, "settings", "body-field")
        # Assigning over the bound method works, but only because nothing else
        # needs the original. Kept explicit rather than made mouse-transparent,
        # since there is no wrapper widget here to receive the press instead.
        self.query.mouseReleaseEvent = lambda _event: self._open_keyboard()
        search.addWidget(self.query, stretch=1)
        search.addWidget(IconButton("mdi.magnify", self.search, size=20))
        # Only useful once something has been looked up, so it says what it
        # does rather than sitting there as a mystery arrow.
        self.refresh_btn = IconButton("mdi.map-marker-multiple", self._refresh_places, size=20)
        self.refresh_btn.setToolTip("Look for places here again")
        search.addWidget(self.refresh_btn)
        self.content.addLayout(search)

        self.status = QLabel("")
        self.status.setFont(make_font(SIZES.S1))
        set_style(self.status, "common", "text-muted")
        self.content.addWidget(self.status)

        self.places: list = []
        self._area = None          # (lat, lon, box) the current places came from

        from src.constants import get_data_dir, APP_NAME
        self.cache = PlaceCache(
            get_data_dir(APP_NAME) / "calendar" / "places.cache", client)

        self.list_host = QWidget()
        # Bounded, so the wrapped labels inside actually have a width to wrap
        # against. Without it they size to their content and the panel clips
        # them instead.
        self.list_host.setFixedWidth(276)
        set_style(self.list_host, "common", "transparent")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Hidden, and dragged instead. A scrollbar is a mouse control, and on a
        # panel it is a stripe of chrome nobody can hit accurately anyway.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        try:
            from PyQt6.QtWidgets import QScroller
            QScroller.grabGesture(scroll.viewport(),
                                  QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        except Exception:
            pass
        scroll.setFixedWidth(300)
        set_style(scroll, "common", "transparent")
        scroll.setWidget(self.list_host)

        self.map = MapView(self.MAP_H, client=client)
        self.map.on_picked(self._picked_on_map)

        # Side by side. A list above a map competes with it for height; beside
        # it, both get the full height of the dialog.
        split = QHBoxLayout()
        split.setSpacing(12)
        split.addWidget(self.map, stretch=1)
        split.addWidget(scroll)
        self.content.addLayout(split, stretch=1)

        self.add_button("Use this place", self._accept, "primary")
        self.add_button("Clear", self._clear, "secondary")
        self.add_button("Cancel", self.close, "secondary")

        if value:
            # Searched on a trimmed form. A saved location is a full Nominatim
            # display name - "Starbucks, 123 Main St, Omaha, Douglas County,
            # Nebraska, 68102, United States" - and searching that verbatim
            # almost always matches nothing, which is why reopening an event
            # reported a failed lookup until something was tapped.
            self.search(self._searchable(value))

    ## -- input

    def _open_keyboard(self) -> None:
        from src.ui.keyboard import KeyboardDialog
        # Searches on Done rather than waiting for the magnifier. Typing a
        # place and then having to find a second button to act on it is a step
        # that exists only because the keyboard did not say when it was
        # finished.
        self.client.dialog(KeyboardDialog(
            self.client, self.query, mode="text", label="Location",
            on_done=lambda text: self.search(text)))

    ## -- nearby places

    def _refresh_places(self) -> None:
        """Discard what was remembered for this area and ask again."""
        if not self._area:
            self.status.setText("Search for somewhere first.")
            return
        lat, lon, box = self._area
        self.cache.forget(lat, lon, box)
        self._load_places(lat, lon, box, use_cache=False)

    def _load_places(self, lat, lon, box=None, use_cache: bool = True) -> None:
        """
        Shops, restaurants and the like around a point.

        Nominatim answers "where is this name", not "what is near here" - a
        city search returns the city, which is why the map had one dot on it.
        Overpass is the query that actually lists what is there.
        """
        self._area = (lat, lon, box)

        if use_cache:
            remembered = self.cache.get(lat, lon, box)
            if remembered is not None:
                # Straight from disk. A city looked up once should not cost a
                # network round trip every time it is opened.
                self._show_places(remembered, lat, lon, cached=True)
                return

        self.status.setText("Looking for places nearby...")

        def work():
            places = []
            failed = ""
            try:
                # Nominatim hands back the extent of what it matched. A city
                # centroid with a fixed 1.3km box around it lands in a park and
                # finds nothing, which is why searching a city showed no places
                # at all - so the result's own bounding box is used where there
                # is one, capped so a whole county does not become one query.
                # A radius, always, and a modest one. A city-wide bbox is a
                # minute of server time and gets rate-limited or refused - and
                # it is no longer needed, because the map tiles show the whole
                # city's places by themselves. This list is the tappable
                # shortlist near wherever you are looking, not a directory.
                radius = 1200
                # nwr, not node. Most businesses are mapped as building
                # polygons - a way, or a relation - and a node-only query
                # misses nearly all of a real high street, which is what made
                # a whole city look empty while a dropped pin found things.
                # `out center` gives each of them a single point.
                #
                # One combined filter rather than four separate blocks: the
                # union of four full-bbox scans is what was timing out on
                # anything city-sized.
                kinds = ("restaurant|cafe|bar|pub|fast_food|bakery|pharmacy|"
                         "bank|hospital|clinic|doctors|dentist|school|library|"
                         "cinema|theatre|museum|post_office|police|fuel|"
                         "parking|place_of_worship|university|college")
                # around: is indexed, where a large bbox is a scan.
                near = f"around:{radius},{float(lat)},{float(lon)}"
                query = (
                    "[out:json][timeout:25];("
                    f'nwr["amenity"~"^({kinds})$"]["name"]({near});'
                    f'nwr["shop"]["name"]({near});'
                    f'nwr["tourism"~"^(hotel|guest_house|museum|attraction)$"]["name"]({near});'
                    ");out center 80;"
                )
                # Two mirrors. The main one rate-limits aggressively and a
                # single refusal is indistinguishable from "nothing is here".
                payload = None
                last = None
                for host in ("https://overpass-api.de/api/interpreter",
                             "https://overpass.kumi.systems/api/interpreter"):
                    try:
                        request = urllib.request.Request(
                            host,
                            data=urllib.parse.urlencode({"data": query}).encode(),
                            headers={
                                "User-Agent": "DesktopHomeAssistant",
                                # This was missing, and it is the whole bug.
                                # Overpass rejects a POST body with no content
                                # type - so every city lookup came back 400 and
                                # looked exactly like "nothing is here".
                                "Content-Type": "application/x-www-form-urlencoded",
                            })
                        with urllib.request.urlopen(request, timeout=40) as response:
                            payload = json.loads(response.read().decode())
                        break
                    except urllib.error.HTTPError as e:
                        # The body says which of rate limit, timeout or syntax
                        # it was; the status alone does not.
                        detail = ""
                        try:
                            detail = e.read().decode()[:200]
                        except Exception:
                            pass
                        last = RuntimeError(f"{e.code} from {host}: {detail}")
                    except Exception as e:
                        last = e
                if payload is None:
                    raise last or RuntimeError("no response")

                for element in payload.get("elements", []):
                    tags = element.get("tags") or {}
                    name = tags.get("name")
                    if not name:
                        continue      # unnamed nodes are noise on a map this size

                    # A node carries lat/lon directly; a way or relation only
                    # has the centre `out center` computed for it.
                    centre = element.get("center") or {}
                    lat_v = element.get("lat", centre.get("lat"))
                    lon_v = element.get("lon", centre.get("lon"))
                    if lat_v is None or lon_v is None:
                        continue
                    kind = (tags.get("amenity") or tags.get("shop")
                            or tags.get("tourism") or tags.get("leisure") or "")
                    house = tags.get("addr:housenumber", "")
                    street = tags.get("addr:street", "")
                    places.append({
                        "lat": lat_v, "lon": lon_v,
                        "display_name": name,
                        "kind": kind.replace("_", " "),
                        "address": f"{house} {street}".strip(),
                        "phone": tags.get("phone") or tags.get("contact:phone") or "",
                        "website": tags.get("website") or tags.get("contact:website") or "",
                        "opening_hours": tags.get("opening_hours", ""),
                        "cuisine": (tags.get("cuisine") or "").replace("_", " "),
                    })
            except Exception as e:
                failed = str(e)
                self.client.log("warning", f"[Calendar] Overpass lookup failed: {e}")

            if not failed:
                self.cache.put(lat, lon, box, places)
            self.client.call_on_ui(
                lambda: self._show_places(places, lat, lon, failed))

        Thread(target=work, name="__overpass", daemon=True).start()

    def _show_places(self, places: list, lat, lon, failed: str = "",
                     cached: bool = False) -> None:
        try:
            self.places = places
            self._fill_side_panel()
            self.map.show_point(lat, lon, self.client,
                                places=(self.results or []) + places,
                                zoom=16, mark=True)
            if failed:
                # The reason, not a generic apology. A rate limit and a dead
                # network need different responses from whoever is standing
                # there, and "could not reach" covers both badly.
                # Downgraded to a note. The map itself is drawing the places
                # now, so a failed shortlist is a missing convenience rather
                # than a missing feature.
                reason = ("rate limited" if "429" in failed
                          else "timed out" if "timeout" in failed.lower()
                          else "unavailable")
                self.status.setText(
                    f"Shortlist {reason} - the map still shows places, "
                    "tap one to drop a pin.")
            elif places:
                self.status.setText(
                    f"{len(places)} place{'s' if len(places) != 1 else ''} here"
                    + (" (remembered)" if cached else ""))
            else:
                self.status.setText(
                    "No named places here - tap the map to drop a pin.")
        except RuntimeError:
            pass

    def _fill_side_panel(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        def add_heading(text: str):
            label = QLabel(text)
            label.setFont(make_font(SIZES.S1, bold=True))
            set_style(label, "common", "text-muted")
            self.list_layout.addWidget(label)

        def add_entry(entry: dict, subtitle: str = ""):
            # A QPushButton will not wrap its label, and an address in a 300px
            # column is always longer than one line - so these are frames with
            # a wrapped label in them rather than buttons.
            self.list_layout.addWidget(
                _PlaceRow(entry, subtitle, self._pick))

        if self.results:
            add_heading("Matches")
            for entry in self.results:
                add_entry(entry)
        if self.places:
            add_heading("Nearby")
            for entry in sorted(self.places, key=lambda e: e.get("display_name", "")):
                add_entry(entry, entry.get("kind", ""))
        self.list_layout.addStretch()

    ## -- searching

    @staticmethod
    def _searchable(value: str) -> str:
        """The first few parts of an address - specific enough, loose enough."""
        parts = [p.strip() for p in str(value or "").split(",") if p.strip()]
        return ", ".join(parts[:3]) if len(parts) > 3 else str(value or "").strip()

    def search(self, term: str = None) -> None:
        term = (term if term is not None else self.query.text()).strip()
        if not term:
            self.status.setText("Type something to search for.")
            return
        self.status.setText("Searching…")

        def work():
            found = []
            try:
                query = urllib.parse.urlencode(
                    {"q": term, "format": "json", "limit": 6})
                request = urllib.request.Request(
                    f"https://nominatim.openstreetmap.org/search?{query}",
                    headers={"User-Agent": "DesktopHomeAssistant"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    found = json.loads(response.read().decode())
            except Exception as e:
                self.client.log("debug", f"[Calendar] Location search failed: {e}")
            if not found:
                # One retry on a shorter form before giving up. The first two
                # parts are usually the name and the street, which is what
                # Nominatim actually indexes.
                short = ", ".join(term.split(",")[:2]).strip()
                if short and short != term:
                    try:
                        query = urllib.parse.urlencode(
                            {"q": short, "format": "json", "limit": 6})
                        request = urllib.request.Request(
                            f"https://nominatim.openstreetmap.org/search?{query}",
                            headers={"User-Agent": "DesktopHomeAssistant"})
                        with urllib.request.urlopen(request, timeout=10) as response:
                            found = json.loads(response.read().decode())
                    except Exception:
                        pass

            self.client.call_on_ui(lambda: self._show_results(found, term))

        Thread(target=work, name="__location_search", daemon=True).start()

    def _show_results(self, found: list, term: str) -> None:
        try:
            while self.list_layout.count():
                item = self.list_layout.takeAt(0)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()

            self.results = found or []
            if not self.results:
                # Offline is a normal state for a wall panel, so what was typed
                # is still usable - it just does not get a map.
                self.status.setText(
                    f"Nothing found. '{term}' will still be saved as typed.")
                self.map.set_message(term)
                self._fill_side_panel()
                return

            self.status.setText(f"{len(self.results)} result"
                                f"{'s' if len(self.results) != 1 else ''}")
            for entry in self.results:
                name = entry.get("display_name", "")
                button = QPushButton(name)
                button.setFont(make_font(SIZES.S1))
                button.setMinimumHeight(40)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                set_style(button, "overlays", "dialog-button-secondary")
                button.clicked.connect(lambda _=False, e=entry: self._pick(e))
                self.list_layout.addWidget(button)

            # Every result as a dot, centred on the first and zoomed out far
            # enough to see them together - searching a city and then choosing
            # the actual place off the map is the point.
            first = self.results[0]
            self.chosen = first.get("display_name", "")
            self.query.setText(self.chosen)
            self.map.show_point(first.get("lat"), first.get("lon"), self.client,
                                places=self.results,
                                zoom=13 if len(self.results) > 1 else 16,
                                mark=len(self.results) == 1)
            self._fill_side_panel()
            self._load_places(first.get("lat"), first.get("lon"),
                              box=first.get("boundingbox"))
        except RuntimeError:
            pass

    def _pick(self, entry: dict) -> None:
        self.chosen = entry.get("display_name", "") or self.query.text().strip()
        self.query.setText(self.chosen)
        # Everything stays on the map. Choosing one place used to redraw with
        # only the search matches, so the rest of the area vanished the moment
        # you touched anything.
        self.map.show_point(entry.get("lat"), entry.get("lon"), self.client,
                            places=(self.results or []) + (self.places or []),
                            zoom=17)

    def _picked_on_map(self, lat: float, lon: float) -> None:
        """
        A tap on the map. Reverse-geocoded so the saved value is a name rather
        than a pair of numbers - coordinates are not something anyone reads off
        an event later.
        """
        self.status.setText("Looking up that spot...")

        def work():
            name = ""
            try:
                query = urllib.parse.urlencode(
                    {"lat": lat, "lon": lon, "format": "json", "zoom": 18})
                request = urllib.request.Request(
                    f"https://nominatim.openstreetmap.org/reverse?{query}",
                    headers={"User-Agent": "DesktopHomeAssistant"})
                with urllib.request.urlopen(request, timeout=8) as response:
                    name = json.loads(response.read().decode()).get("display_name", "")
            except Exception as e:
                self.client.log("debug", f"[Calendar] Reverse lookup failed: {e}")

            def apply():
                try:
                    self.chosen = name or f"{lat:.5f}, {lon:.5f}"
                    self.query.setText(self.chosen)
                    # Refresh the side panel around wherever the pin landed, so
                    # dropping it near a parade of shops lists them.
                    self._load_places(lat, lon)   # a pin, so the tight box is right
                except RuntimeError:
                    pass
            self.client.call_on_ui(apply)

        Thread(target=work, name="__reverse_geocode", daemon=True).start()

    ## -- map

    def _load_map(self, lat, lon) -> None:
        if not lat or not lon:
            return
        self.map.show_point(lat, lon, self.client)

    ## -- result

    def _accept(self) -> None:
        chosen = self.chosen or self.query.text().strip()
        if callable(self.on_chosen):
            self.on_chosen(chosen)
        self.close()

    def _clear(self) -> None:
        if callable(self.on_chosen):
            self.on_chosen("")
        self.close()
