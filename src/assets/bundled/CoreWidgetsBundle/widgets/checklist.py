"""
A short list with things to tick off.

The sticky note holds text; this holds decisions. A shopping list on a kitchen
wall is the most-used thing a panel like this does, and reading one off a note
somebody has to remember they crossed out is not the same as tapping it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QPainter, QColor, QPen, QFont

from src.styling import make_font
from src.ui.widget import Widget

if TYPE_CHECKING:
    from src.main import Client

#The same set the sticky note uses, so a wall of both reads as one thing.
COLOURS = ["#f2d675", "#f29e7b", "#a8d8a0", "#9cc4f0", "#e0a8d8"]


class ChecklistWidget(Widget):
    """A title and a list of items, each tappable."""

    KEY         = "checklist"
    NAME        = "Checklist"
    ICON        = "mdi.format-list-checks"
    DESCRIPTION = "A list of things to tick off."

    RESIZABLE = True
    ROTATABLE = False
    FLOATABLE = True
    REMOVABLE = True
    MULTIPLE  = True

    MIN_W, MIN_H = 180, 150
    MAX_W, MAX_H = 700, 900
    DEFAULT_ANCHOR = "top-right"

    FONT_SIZES = (12, 15, 19, 24)
    #How many fit before the rest are summarised. A list longer than this is
    #one somebody scrolls, and a widget is not the place for that.
    VISIBLE = 12

    #Sized for a finger, not a cursor. A tick box somebody has to aim at is a
    #tick box they miss, and this is the control the widget exists for.
    ROW = 44
    BOX = 26
    #Space between the last item and Add, so the two are not one strip of
    #boxes to hit by accident.
    ADD_GAP = 12

    def __init__(self, client: "Client", key: str = "", title: str = "",
                 items: list = None, **_ignored):
        # The base takes geometry, not the class metadata - NAME, ICON and
        # DESCRIPTION are read off the class by the panel that lists it.
        super().__init__(client=client, key=key or self.KEY,
                         width=240, height=260, floating=True)
        self.title = str(title or "Checklist")
        # [{"text": str, "done": bool}]
        self.items: list = list(items or [])
        self.colour = COLOURS[3]
        self.font_size = self.FONT_SIZES[1]
        self.set_content_size(260, 320)

    ## -- state

    def layout_state(self) -> dict:
        state = super().layout_state()
        state["title"] = self.title
        state["items"] = self.items
        state["colour"] = self.colour
        state["font_size"] = self.font_size
        return state

    def apply_layout_state(self, state: dict) -> None:
        super().apply_layout_state(state)
        if not isinstance(state, dict):
            return
        self.title = str(state.get("title", self.title))
        self.colour = str(state.get("colour", self.colour))
        try:
            self.font_size = int(state.get("font_size", self.font_size))
        except (TypeError, ValueError):
            pass
        saved = state.get("items")
        if isinstance(saved, list):
            # Rebuilt rather than trusted: this comes off disk, and a malformed
            # entry would otherwise crash the paint that draws it.
            self.items = [
                {"text": str(e.get("text", "")), "done": bool(e.get("done"))}
                for e in saved if isinstance(e, dict)
            ]

    def _save(self) -> None:
        try:
            framework = self.parent()
            if framework is not None and hasattr(framework, "save_layout"):
                framework.save_layout()
        except Exception:
            pass

    ## -- editing

    def chrome_button(self):
        # Look only. Editing the list happens ON the list - see on_activate.
        return ("mdi.palette-outline", "Look", self.open_style)

    def open_style(self) -> None:
        from src.ui.dialogs_look import LookDialog

        self.client.dialog(LookDialog(
            self.client, self.title, COLOURS, self.colour,
            self.font_size, self.FONT_SIZES,
            on_colour=self.set_colour, on_size=self.set_font_size,
            on_rename=self.rename))

    def set_colour(self, colour: str) -> None:
        self.colour = str(colour)
        self.update()
        self._save()

    def add_item(self) -> None:
        """One new line, typed. This is the only place the keyboard opens."""
        def chosen(value: str) -> None:
            text = str(value or "").strip()
            if not text:
                return
            self.items.append({"text": text[:120], "done": False})
            self.update()
            self._save()

        # prompt(), which is the keyboard - not a dialog in front of it.
        self.client.prompt(f"Add to {self.title}", on_submit=chosen,
                           placeholder="Milk")

    @staticmethod
    def _parse(text: str) -> list:
        items = []
        for line in str(text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            done = line.lower().startswith(("[x]", "[X]"))
            if done:
                line = line[3:].strip()
            elif line.startswith("[]") or line.startswith("[ ]"):
                line = line.split("]", 1)[1].strip()
            if line:
                items.append({"text": line[:120], "done": done})
        return items

    def rename(self) -> None:
        def chosen(value: str) -> None:
            self.title = str(value or "").strip() or "Checklist"
            self.update()
            self._save()

        self.client.prompt("Name this list", on_submit=chosen,
                           default=self.title)

    def cycle_colour(self) -> None:
        index = (COLOURS.index(self.colour) + 1) % len(COLOURS) \
            if self.colour in COLOURS else 0
        self.colour = COLOURS[index]
        self.update()
        self._save()

    def set_font_size(self, size: int) -> None:
        self.font_size = int(size)
        self.update()
        self._save()

    def clear_done(self) -> None:
        self.items = [e for e in self.items if not e["done"]]
        self.update()
        self._save()

    ## -- ticking

    def on_activate(self) -> None:
        """
        Everything is on the list itself.

        A row's tick box toggles it, its X removes it, and the Add row at the
        bottom is the only thing that opens a keyboard. Tapping a list should
        not put a wall of text in front of somebody who wanted to cross off
        one thing.
        """
        point = self.mapFromGlobal(self.cursor().pos())

        if self._add_rect().contains(point):
            self.add_item()
            return

        index = self._row_at(point.y())
        if index is None:
            self.rename()
            return

        if point.x() >= self.width() - 46:
            del self.items[index]
        else:
            self.items[index]["done"] = not self.items[index]["done"]
        self.update()
        self._save()

    def _add_rect(self) -> QRect:
        y = (self._list_top()
             + min(len(self.items), self.VISIBLE) * self.ROW
             + self.ADD_GAP)
        return QRect(10, y, self.width() - 20, self.ROW - 6)

    def _row_at(self, y: int):
        top = self._list_top()
        if y < top:
            return None
        index = (y - top) // self.ROW
        if 0 <= index < min(len(self.items), self.VISIBLE):
            return int(index)
        return None

    def _list_top(self) -> int:
        return 14 + self.font_size + 10

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width, height = self.width(), self.height()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.colour))
        painter.drawRoundedRect(0, 0, width, height, 12, 12)

        ink = QColor("#2a2a22")
        painter.setPen(ink)

        title = make_font(self.font_size, bold=True)
        painter.setFont(title)
        painter.drawText(14, 10, width - 28, self.font_size + 8,
                         Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter, self.title)

        row_font = make_font(self.font_size)
        painter.setFont(row_font)
        y = self._list_top()
        shown = self.items[:self.VISIBLE]

        for entry in shown:
            box = QRect(15, y + (self.ROW - self.BOX) // 2, self.BOX, self.BOX)
            painter.setPen(QPen(ink, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 4, 4)

            if entry["done"]:
                painter.drawLine(box.left() + 4, box.center().y(),
                                 box.center().x() - 1, box.bottom() - 4)
                painter.drawLine(box.center().x() - 1, box.bottom() - 4,
                                 box.right() - 3, box.top() + 4)

            text_font = QFont(row_font)
            text_font.setStrikeOut(entry["done"])
            painter.setFont(text_font)
            painter.setPen(QColor(90, 90, 78) if entry["done"] else ink)
            painter.drawText(box.right() + 12, y, width - box.right() - 62,
                             self.ROW,
                             Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter, entry["text"])

            # A cross in the paper's own colour, darkened.
            #
            # A red X on a yellow note is an alarm; this is a quiet way to
            # take a line off a list. Dark enough to find when looked for,
            # not loud enough to be the first thing seen.
            cross = QColor(self.colour).darker(150)
            painter.setPen(QPen(cross, 2.5))
            cx = width - 26
            cy = y + self.ROW // 2
            painter.drawLine(cx - 8, cy - 8, cx + 8, cy + 8)
            painter.drawLine(cx + 8, cy - 8, cx - 8, cy + 8)
            y += self.ROW

        # The Add row, always at the end of what is shown.
        add = self._add_rect()
        painter.setPen(QPen(QColor(self.colour).darker(140), 2,
                            Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(add, 8, 8)
        painter.setPen(QColor(80, 80, 68))
        painter.setFont(make_font(max(11, self.font_size - 2)))
        painter.drawText(add, Qt.AlignmentFlag.AlignCenter, "+  Add")
        y = add.bottom() + 4

        left = len(self.items) - len(shown)
        if left > 0:
            painter.setFont(make_font(max(10, self.font_size - 3)))
            painter.setPen(QColor(90, 90, 78))
            painter.drawText(15, y, width - 30, self.ROW,
                             Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter,
                             f"and {left} more")
        painter.end()
