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
from .paper import PaperWidget, COLOURS

if TYPE_CHECKING:
    from src.main import Client

#Re-exported: the endpoint that puts a list up offers this palette.
COLOURS = COLOURS


class ChecklistWidget(PaperWidget):
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

    #Its own ladder: a list carries a row of text per item and a note carries
    #one block, so the same point size fills a list twice as fast. The middle
    #step is still 20 - the default a wall panel is read from across a room.
    FONT_SIZES = (14, 17, 20, 24, 28)
    #What a new list is WIDE, at 20pt. Its height comes from its contents -
    #see _refit - so only the width is scaled from here.
    BASE_W, BASE_H = 260, 320
    BASE_FONT = 20
    DEFAULT_FONT = 2
    #A list is mostly ink on paper, so it starts on the coolest of the set.
    DEFAULT_COLOUR = 3
    RENAMEABLE = True

    #Sized for a finger, not a cursor. A tick box somebody has to aim at is a
    #tick box they miss, and this is the control the widget exists for.
    ROW = 44
    BOX = 26
    #Space between the last item and Add, so the two are not one strip of
    #boxes to hit by accident.
    ADD_GAP = 12
    #The Add row and the "and N more" line, and what is left below them.
    ADD_H = 38
    MORE_H = 22
    PAD_BOTTOM = 12
    #A ceiling on rows however tall the widget is dragged. A list longer than
    #this is one somebody scrolls, and a widget is not the place for that.
    MAX_ROWS = 40

    def __init__(self, client: "Client", key: str = "", title: str = "",
                 items: list = None, **_ignored):
        # The base takes geometry, not the class metadata - NAME, ICON and
        # DESCRIPTION are read off the class by the panel that lists it.
        super().__init__(client=client, key=key or self.KEY,
                         width=260, height=320, floating=True)
        self.title = str(title or "Checklist")
        # [{"text": str, "done": bool}]
        self.items: list = list(items or [])
        # Not chosen: it is a starting point, and the widget grows from it as
        # items arrive. Marking it chosen would freeze it at three rows.
        self.set_content_size(self.BASE_W, self.BASE_H, chosen=False)
        self._refit()

    ## -- geometry
    #
    # Everything below is derived from the content height rather than from a
    # fixed row count. A count that did not know how tall the widget was put
    # the Add row past the bottom edge on any list of more than about six, and
    # left _row_at() reporting rows that were never drawn - so a tap near the
    # bottom ticked off something the person could not see.

    def _list_top(self) -> int:
        return 14 + self.font_size + 10

    def _natural_height(self, count: int = None) -> int:
        """How tall this widget wants to be to show `count` items whole."""
        count = len(self.items) if count is None else count
        return (self._list_top() + max(count, 1) * self.ROW
                + self.ADD_GAP + self.ADD_H + self.PAD_BOTTOM)

    def _rows_that_fit(self) -> int:
        """How many rows the current height can actually draw."""
        height = self.content_size()[1]
        room = (height - self._list_top() - self.ADD_GAP
                - self.ADD_H - self.PAD_BOTTOM)
        fit = int(room // self.ROW)
        if fit < len(self.items):
            # The "and N more" line needs a row of its own, so making it fit
            # costs one of the entries it is counting.
            fit = int((room - self.MORE_H) // self.ROW)
        return max(0, min(fit, self.MAX_ROWS))

    def _shown(self) -> list:
        return self.items[:self._rows_that_fit()]

    def _refit(self) -> None:
        """
        Grow or shrink to hold the list, unless somebody chose a size.

        A size that was dragged is a decision and is left alone - the list
        then shows what fits and summarises the rest. A widget nobody has
        resized follows its contents, which is how a list on paper behaves.
        """
        if self.has_chosen_size():
            self.update()
            return

        width, height = self.content_size()

        # The width follows the text size too. A row of text at 28pt in a box
        # measured for 15 wraps or elides, which reads as the list being
        # wrong rather than the box being small.
        wide = int(max(self.MIN_W, min(self.MAX_W, self.BASE_W * max(
            0.5, float(self.font_size) / float(self.BASE_FONT or 20)))))
        wanted = max(self.MIN_H, min(self.MAX_H, self._natural_height()))

        if (wide, wanted) != (width, height):
            self.set_content_size(wide, wanted, chosen=False)
            self.setFixedSize(*self.rotated_bounds())
            self.updateGeometry()
        self.update()

    def minimumSizeHint(self):
        """Enough for the title, one row and Add - a floor, not a fit."""
        from PyQt6.QtCore import QSize
        return QSize(self.MIN_W, min(self.MAX_H, self._natural_height(1)))

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        width = self.content_size()[0] or 260
        return QSize(width, max(self.MIN_H,
                                min(self.MAX_H, self._natural_height())))

    ## -- state

    def layout_state(self) -> dict:
        state = super().layout_state()
        state["title"] = self.title
        state["items"] = self.items
        return state

    def fit_to_text(self) -> None:
        """
        The list's own fit, which is its contents and not a scale factor.

        `_refit` already knows how tall a list wants to be for the number of
        items it holds; the base class's version would overwrite that with a
        scaled guess.
        """
        self._refit()

    def apply_layout_state(self, state: dict) -> None:
        super().apply_layout_state(state)
        if not isinstance(state, dict):
            return
        self.title = str(state.get("title", self.title))
        saved = state.get("items")
        if isinstance(saved, list):
            # Rebuilt rather than trusted: this comes off disk, and a malformed
            # entry would otherwise crash the paint that draws it.
            self.items = [
                {"text": str(e.get("text", "")), "done": bool(e.get("done"))}
                for e in saved if isinstance(e, dict)
            ]
        # After the items, and after the base has restored any chosen size -
        # _refit() asks whether one was chosen, so it has to run last.
        self._refit()

    ## -- editing

    def add_item(self) -> None:
        """One new line, typed. This is the only place the keyboard opens."""
        def chosen(value: str) -> None:
            text = str(value or "").strip()
            if not text:
                return
            self.items.append({"text": text[:120], "done": False})
            self._refit()
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

    @staticmethod
    def serialise(items: list) -> str:
        """
        The list as text, in the form _parse reads back.

        The exact inverse, and paired with it here for that reason. Written
        out at the call site instead, the tick marker was left off - so every
        edit made from a phone came back with the whole list unticked, and the
        two halves of the round trip disagreed with nothing to compare.
        """
        lines = []
        for entry in items or []:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text", "")).strip()
            if not text:
                continue
            lines.append(f"[x] {text}" if entry.get("done") else text)
        return "\n".join(lines)

    def open_style(self) -> None:
        # Titled with the list's own name rather than the class's: a wall of
        # them is told apart by what each one is called.
        self.LOOK_TITLE = self.title
        super().open_style()

    def on_look_changed(self) -> None:
        # _list_top() is derived from the font size, so every row below it
        # moves - the widget is re-measured rather than only repainted.
        self._refit()
        self._save()

    def rename(self) -> None:
        def chosen(value: str) -> None:
            self.title = str(value or "").strip() or "Checklist"
            self.update()
            self._save()

        self.client.prompt("Name this list", on_submit=chosen,
                           default=self.title)

    def clear_done(self) -> None:
        self.items = [e for e in self.items if not e["done"]]
        self._refit()
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

        if point.x() >= self.content_size()[0] - 46:
            del self.items[index]
        else:
            self.items[index]["done"] = not self.items[index]["done"]
        self._refit()
        self._save()

    def _add_rect(self) -> QRect:
        width = self.content_size()[0]
        y = (self._list_top() + len(self._shown()) * self.ROW + self.ADD_GAP)
        return QRect(10, y, width - 20, self.ADD_H)

    def _row_at(self, y: int):
        top = self._list_top()
        if y < top:
            return None
        index = (y - top) // self.ROW
        # Bounded by what is DRAWN, not by what is on the list. A tap below
        # the last visible row belongs to nothing.
        if 0 <= index < len(self._shown()):
            return int(index)
        return None

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # content_size(), not width()/height(). The two agree for this widget
        # because it does not rotate, but the hit tests are written against
        # content_size and a paint measured differently is a paint that
        # disagrees with where taps land.
        width, height = self.content_size()
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
        shown = self._shown()

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
            painter.drawText(15, y, width - 30, self.MORE_H,
                             Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter,
                             f"and {left} more")
        painter.end()
