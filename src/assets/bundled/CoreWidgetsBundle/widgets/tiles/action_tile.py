"""
A tile that runs something.

The first step of it. Everything this eventually needs - picking a thing from
a registry, building its arguments, testing it, reading a value out of what
comes back - hangs off the chooser, so the chooser is what exists first and
the rest is added behind it.

The list dialog rather than the grid: two endpoints on the same plugin differ
by their path and by nothing else, and at tile size a path is three letters
and an ellipsis.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QSizePolicy

from src.ui.widgets.tile import Tile
from src.ui.grid_dialog import ItemGridDialog, GridItem
from src.assets.bundled.CoreWidgetsBundle.widgets.tiles import (
    action_sources, action_runner, action_rules)
from src.styling import set_style, make_font, SIZES, add_text_shadow
from src.ui.icons import icon

if TYPE_CHECKING:
    from src.main import Client


class ActionTile(Tile):
    """One thing, run when the tile is pressed."""

    KEY  = "action_tile"
    NAME = "Action"
    #An action to one thing is not a feature either - see BookmarkTile.
    MULTIPLE = True
    #It carries a whole setup - what it runs, with what, and what it reads -
    #so there has to be a way back to that without running it.
    EDITABLE = True

    PANEL_SIZES = [(1, 1), (2, 1), (2, 2)]

    #Side margins the label sits inside, from the layout below.
    LABEL_INSET = 16

    def __init__(self, client: "Client", grid_w: int = 1, grid_h: int = 1,
                 **kwargs):
        super().__init__(client, grid_w=grid_w, grid_h=grid_h,
                         on_click=self.pressed, **kwargs)

        # What this tile runs. Empty until something is picked.
        self.action: dict = {}

        #What the last run said. None until it has been run at all.
        self.state: Optional[bool] = None
        self.failed = False
        #How the rules said to draw it, if there are any.
        self.look = None
        #When it last polled. Zero so the first tick runs it.
        self._polled_at = 0.0

        self._icon_label: Optional[QLabel] = None
        self._name_label: Optional[QLabel] = None
        self._label_text = ""

        self.add_variant(1, 1, self._build)
        self.add_variant(2, 1, self._build)
        self.apply_span(grid_w, grid_h, force=True)

    ## -- look

    def _build(self) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")

        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # The picture takes what is left after the name - see BookmarkTile for
        # why a fixed icon height leaves nothing for the words under it.
        self._icon_label = QLabel()
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setMinimumHeight(0)
        self._icon_label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                       QSizePolicy.Policy.Expanding)
        set_style(self._icon_label, "common", "transparent")
        layout.addWidget(self._icon_label, stretch=1)

        self._name_label = QLabel("")
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setWordWrap(False)
        self._name_label.setFont(make_font(SIZES.S1, bold=True))
        self._name_label.setStyleSheet("color:#e8ecf4;background:transparent;")
        self._name_label.setSizePolicy(QSizePolicy.Policy.Preferred,
                                       QSizePolicy.Policy.Fixed)
        add_text_shadow(self._name_label, blur=8)
        layout.addWidget(self._name_label, stretch=0)

        self.refresh()
        return host

    def refresh(self) -> None:
        if self._icon_label is None:
            return
        look = self.look
        glyph = ((look.icon if look else "") or self.action.get("icon")
                 or "mdi.gesture-tap-button")
        try:
            side = max(20, min(64, self.height() // 3))
            self._icon_label.setPixmap(icon(glyph, color=self._ink()).pixmap(side, side))
        except Exception:
            pass
        self._label_text = ((look.label if look else "")
                            or self.action.get("label") or "Choose")
        self._fit_label()
        self._apply_surface()

    def _apply_surface(self) -> None:
        """The background and border a rule asked for, if it asked."""
        look = self.look
        background = (look.background if look else "") or ""
        border = (look.border if look else "") or ""
        if not background and not border:
            self.setStyleSheet("")
            return
        parts = ["border-radius: 14px"]
        if background:
            parts.append(f"background: {background}")
        if border:
            parts.append(f"border: 2px solid {border}")
        # Addressed by name, so the rule paints this tile rather than every
        # child of it - an unqualified background covers the labels too.
        self.setObjectName("action_surface")
        self.setStyleSheet(
            "QWidget#action_surface { " + "; ".join(parts) + "; }")

    def _fit_label(self) -> None:
        """The name at this size, or none - see Tile.label_for."""
        label = self._name_label
        if label is None:
            return
        text = self.label_for(label, self._label_text, self.LABEL_INSET)
        label.setText(text)
        # Hidden, not merely blank. An empty label still takes its line, and
        # on a 1x1 tile that line is a third of the picture.
        label.setVisible(bool(text))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        try:
            self.refresh()
        except Exception:
            pass

    ## -- pressing

    #How often a polling tile actually runs, in seconds. The grid ticks every
    #tile far more often than that, and a tile calling an endpoint on every
    #tick is a tile hammering something that did not ask to be hammered.
    POLL_SECONDS = 30

    def tick(self) -> None:
        """
        Run it on a timer, if it was asked to.

        Only for things that read. A tile set to poll something that changes
        the world would change it every half minute without anybody pressing
        anything, so the checkbox that turns this on says which it is - and a
        page cannot be polled at all, since polling one would keep opening it.
        """
        # The base constructor ticks once while laying the tile out, which is
        # before this class has set anything up.
        action = getattr(self, "action", None)
        if not action or not action.get("poll"):
            return
        if action.get("opens_page") or not action.get("name"):
            return

        every = max(5, int(action.get("poll_seconds") or self.POLL_SECONDS))
        now = time.time()
        if now - self._polled_at < every:
            return
        self._polled_at = now
        self.run(quiet=True)

    def pressed(self) -> None:
        """
        Run it, or set it up if there is nothing to run yet.

        A page opens rather than being read: an endpoint that answers with
        HTML has nothing a tile can show, and the point of pressing it is to
        look at it.
        """
        if not self.action.get("name"):
            self.choose()
            return
        self.run()

    def run(self, quiet: bool = False) -> None:
        """
        Run it and take the answer. `quiet` is the timer rather than a person.

        A failure on a poll is not worth a notification every half minute -
        the tile turns amber and says so by looking wrong, which is what a
        tile is for.
        """
        result = action_runner.run(self.client, self.action)
        self.failed = not result.ok

        if result.kind == action_runner.GOT_PAGE:
            self._open_page(result)
        elif result.ok:
            self._read(result.value)
        if not result.ok:
            self.look = None

        if not result.ok and not quiet:
            self.client.simple_notify("mdi.alert-outline",
                                      self.action.get("label") or "Action",
                                      result.describe())
        self.refresh()

    #Where the panel serves its own endpoints. The web page is pointed at the
    #endpoint rather than handed the HTML: the answer already came back once,
    #but a page wants its own address to resolve links and assets against, and
    #a document with no URL has nothing to resolve them from.
    LOCAL = "http://127.0.0.1:5000"

    def _open_page(self, result) -> None:
        """Open the endpoint that answered with a page."""
        name = str(self.action.get("name") or "")
        if self.action.get("kind") != "endpoint" or not name.startswith("/"):
            # A function returned HTML. There is no address to point at, so
            # this says so rather than opening something blank.
            self.client.simple_notify(
                "mdi.open-in-app", self.action.get("label") or "Action",
                "It answered with a page, which has no address to open.")
            return
        try:
            self.client.goto("webpage")
            page = self.client.PAGE
            if page is not None and page.has_feature("navigate"):
                page.features("navigate")(f"{self.LOCAL}{name}")
        except Exception as e:
            self.client.log("warning", f"[Action] Could not open the page: {e}")
            self.client.simple_notify(
                "mdi.alert-outline", self.action.get("label") or "Action",
                "It answered with a page, which could not be opened.")

    def _read(self, answer) -> None:
        """
        Work out how the tile should look, from what came back.

        Rules first, and the plain path only if there are none. A tile set up
        before rules existed keeps working, and one with rules is described
        entirely by them.
        """
        rules = self.action.get("rules") or []
        if rules:
            self.look = action_rules.decide(answer, rules, self._base_look())
            self.state = None if self.look.rule < 0 else True
            return

        self.look = None
        found, value = action_runner.follow(answer,
                                            self.action.get("path", ""))
        self.state = action_runner.as_state(value) if found else None

    def _base_look(self):
        """The tile's own look, which a rule may leave alone."""
        return action_rules.Look(
            label=self.action.get("label", ""),
            icon=self.action.get("icon", ""),
            ink=self.action.get("colour", ""),
        )

    ## -- choosing

    def choose(self) -> None:
        """
        Pick what this tile runs.

        Everything callable that any registry knows about. Not everything
        listed answers with something a tile can show, which the blurb says
        rather than the list hiding - a person who knows what they registered
        should be able to reach it, and one who does not should be warned
        before they pick.
        """
        found = action_sources.everything(self.client)

        items = []
        for runnable in found:
            takes = len(runnable.arguments)
            detail = runnable.source
            if takes:
                detail += f"  \u00b7  {takes} argument" + ("s" if takes != 1 else "")
            if runnable.danger:
                detail += "  \u00b7  changes something"
            items.append(GridItem(
                key=runnable.key,
                label=runnable.label,
                subtitle=detail,
                badge=runnable.badge,
                icon=runnable.icon,
                data=runnable,
            ))

        # Sorted by what somebody is actually looking for. Alphabetical over
        # a hundred entries from six registries puts an endpoint between two
        # plugin functions and calls that an ordering.
        sorts = [
            ("registry", "By registry",
             lambda i: (getattr(i.data, "kind", ""), i.subtitle.lower()),
             "mdi.folder-outline"),
            ("plugin", "By plugin",
             lambda i: (i.subtitle.lower(), i.label.lower()),
             "mdi.puzzle-outline"),
            ("az", "A\u2013Z", lambda i: i.label.lower(),
             "mdi.sort-alphabetical-ascending"),
            ("args", "Fewest arguments first",
             lambda i: (len(getattr(i.data, "arguments", []) or []),
                        i.label.lower()),
             "mdi.format-list-numbered"),
        ]

        self.client.dialog(ItemGridDialog(
            self.client,
            sorts=sorts,
            title="What should this run?",
            body="Anything registered that can be called. Not all of it "
                 "answers with something a tile can show - the next step "
                 "says what each one gives back, and lets you try it.",
            items=items,
            layout="list",
            on_chosen=self._chosen,
            choose_text="Set it up",
            empty_text="Nothing registered that a tile can run.",
            search_hint="Search everything registered",
        ))

    def _chosen(self, item) -> None:
        runnable = getattr(item, "data", None)
        if runnable is None:
            return
        self.action = runnable.to_dict()
        # Every argument the function declared, with whatever it would use
        # when left out. Filled in rather than asked for: these have names
        # nobody can see and defaults that say what happens without them.
        self.action["values"] = {
            argument.name: argument.default
            for argument in runnable.arguments
            if argument.default is not None
        }
        self.refresh()
        # Written now, before anything is run: choosing IS the change worth
        # keeping, and a test that restarts the panel must not lose it.
        self.request_save()
        self.set_up(runnable)

    def edit(self) -> None:
        """The chrome's pencil: back to the setup, without running anything."""
        if not self.action.get("name"):
            self.choose()
            return
        self.set_up()

    def set_up(self, runnable=None) -> None:
        """The second dialog: how it looks, and what it is called with."""
        if runnable is None:
            runnable = action_sources.find(self.client, self.action.get("key", ""))
        if runnable is None:
            self.client.simple_notify(
                "mdi.alert-outline", "Action",
                "Whatever this ran is no longer registered.")
            return
        from .action_setup import ActionSetupDialog
        self.client.dialog(ActionSetupDialog(
            self.client, runnable, self.action, on_saved=self._set_up,
            on_rechoose=self.choose))

    def _set_up(self, action: dict) -> None:
        self.action = dict(action)
        self.refresh()
        self.request_save()

    def _ink(self) -> str:
        """What colour the icon is drawn in."""
        if self.look is not None and self.look.ink:
            return self.look.ink
        return self._plain_ink()

    def _plain_ink(self) -> str:
        """
        What colour the tile is drawn in, which is its whole state display.

        Four answers, and each is a different thing: it has not been run, it
        ran and read as on, it ran and read as off, or it failed. Dimming for
        "off" rather than using a second colour keeps the tile's chosen colour
        as the thing that identifies it.
        """
        if self.failed:
            return "#e0855a"
        colour = self.action.get("colour") or "#e8ecf4"
        if self.state is True:
            return colour
        if self.state is False:
            return "rgba(232,236,244,90)"
        return "#e8ecf4"

    ## -- state

    def tile_state(self) -> dict:
        """Read by TileGrid.save_positions and merged into this tile's entry."""
        return {"action": dict(self.action)} if self.action else {}

    def apply_tile_state(self, state: dict) -> None:
        if isinstance(state, dict) and isinstance(state.get("action"), dict):
            self.action = dict(state["action"])
            self.refresh()
