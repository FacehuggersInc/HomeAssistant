"""
The shared half of the widgets that look like paper.

A sticky note and a checklist are the same object with different contents: a
coloured rectangle on the home screen, with a palette, a text size and a
handle to the framework that saves it. What differs is what is written on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.ui.widget import Widget

if TYPE_CHECKING:
    from src.main import Client

#One set for both, so a wall of notes and lists reads as one thing.
COLOURS = ["#f2d675", "#f29e7b", "#a8d8a0", "#9cc4f0", "#e0a8d8"]

#Point sizes, not a scale factor. A note with two words on it wants big text
#and a list of six wants small, and "medium" means nothing without knowing how
#big the widget is.
FONT_SIZES = (13, 17, 22, 28)


class PaperWidget(Widget):
    """
    A coloured card that saves itself.

    A subclass sets `COLOURS` and `FONT_SIZES` if it wants its own, and paints
    itself. Everything below is the same either way.
    """

    COLOURS = COLOURS
    FONT_SIZES = FONT_SIZES
    #Which of COLOURS a new one starts as.
    DEFAULT_COLOUR = 0
    #Which of FONT_SIZES a new one starts as.
    DEFAULT_FONT = 1
    #What the look dialog calls itself. The widget's NAME when empty.
    LOOK_TITLE = ""
    #Whether the look dialog offers a rename. A list has a title to change;
    #a note is all body text and has nothing to name.
    RENAMEABLE = False

    def __init__(self, client: "Client", key: str = "", **kwargs):
        super().__init__(client=client, key=key or self.KEY, **kwargs)
        self.colour = self.COLOURS[self.DEFAULT_COLOUR]
        self.font_size = self.FONT_SIZES[self.DEFAULT_FONT]

    ## -- saving

    def framework(self):
        """
        The WidgetFramework this sits in, or None.

        Walked rather than taken from parent() directly: an anchored widget
        sits inside a zone's row, so its parent is that row and not the
        framework.
        """
        node = self.parent()
        while node is not None and not hasattr(node, "save_layout"):
            node = node.parent()
        return node

    def _save(self) -> None:
        framework = self.framework()
        if framework is None:
            return
        try:
            framework.save_layout()
        except Exception as e:
            self.client.log("warning",
                            f"[{self.KEY}] Could not save the layout: {e}")

    ## -- look

    def chrome_button(self):
        return ("mdi.palette-outline", "Look", self.open_style)

    def open_style(self) -> None:
        """Swatches and a stepper, not a list of rows to read."""
        from src.ui.dialogs_look import LookDialog

        self.client.dialog(LookDialog(
            self.client, self.LOOK_TITLE or self.NAME or self.KEY,
            self.COLOURS, self.colour, self.font_size, self.FONT_SIZES,
            on_colour=self.set_colour, on_size=self.set_font_size,
            **({"on_rename": self.rename} if self.RENAMEABLE else {})))

    def set_colour(self, colour: str) -> None:
        self.colour = str(colour)
        self.on_look_changed()

    def set_font_size(self, size: int) -> None:
        self.font_size = int(size)
        self.on_look_changed()

    def cycle_colour(self) -> None:
        index = (self.COLOURS.index(self.colour) + 1) % len(self.COLOURS) \
            if self.colour in self.COLOURS else 0
        self.colour = self.COLOURS[index]
        self.on_look_changed()

    def on_look_changed(self) -> None:
        """
        Repaint and save after the palette or the text size changes.

        A subclass whose geometry depends on the text size overrides this to
        re-measure as well - see ChecklistWidget.
        """
        self.update()
        self._save()

    def rename(self) -> None:
        """Only reached when RENAMEABLE. A widget without a title has none."""
        pass

    ## -- state

    def layout_state(self) -> dict:
        state = super().layout_state()
        state["colour"] = self.colour
        state["font_size"] = self.font_size
        return state

    def apply_layout_state(self, state: dict) -> None:
        super().apply_layout_state(state)
        if not isinstance(state, dict):
            return
        self.colour = str(state.get("colour", self.colour))
        try:
            self.font_size = int(state.get("font_size", self.font_size))
        except (TypeError, ValueError):
            pass
