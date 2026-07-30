from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QHBoxLayout, QFrame
from PyQt6.QtCore import Qt

from src.ui.widget import Widget
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons
from src.styling import set_style

from .notification import NotificationCenterWidget

if TYPE_CHECKING:
    from src.main import Client


class ConfigurationBar(Widget):
    # Home for the controls that always need to be reachable: notifications
    # and the widgets panel. They used to be separate anchored widgets, which
    # meant they could each be dragged somewhere unhelpful or removed
    # entirely, leaving no way back into the panel.
    #
    # REMOVABLE is False so it cannot be dropped on the trash. It is still
    # floatable, so it can live wherever the user wants.

    KEY = "configuration-bar"
    NAME = "Configuration Bar"
    DESCRIPTION = "Notifications and the widgets panel. Always on the page."
    ICON = "tune"

    RESIZABLE = False
    ROTATABLE = False
    FLOATABLE = True
    REMOVABLE = False

    BUTTON = 46
    DEFAULT_ANCHOR = "top-right:0"

    def __init__(self, client: "Client"):
        super().__init__(client=client, key=self.KEY, anchor=self.DEFAULT_ANCHOR)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self, "widgets", "configuration-bar")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        # The real notification centre, embedded rather than reimplemented, so
        # history, the unread dot and the panel keep working exactly as before.
        self.notifications = NotificationCenterWidget(client)
        self.notifications.setParent(self)
        row.addWidget(self.notifications)

        self._widgets_btn = IconButton(
            Icons.WIDGETS,
            self._open_widgets_panel, size=self.BUTTON // 2,
        )
        self._widgets_btn.setFixedSize(self.BUTTON, self.BUTTON)
        row.addWidget(self._widgets_btn)

        # A timer without saying anything to the assistant. The voice route
        # exists, but a panel is a shared screen and not everyone wants to
        # talk to it.
        self._timer_btn = IconButton("mdi.timer-plus-outline",
                                     self._new_timer, size=self.BUTTON // 2)
        self._timer_btn.setFixedSize(self.BUTTON, self.BUTTON)
        row.addWidget(self._timer_btn)

        # An explicit size, not adjustSize(): the bar goes into a graphics
        # proxy before its layout has run, so leaving it to size itself left
        # the buttons clipped by a box smaller than its own contents.
        inner_h = max(self.notifications.height(), self.BUTTON)
        self.setFixedSize(
            10 + self.notifications.width() + 8 + self.BUTTON
            + 8 + self.BUTTON + 10,
            inner_h + 16,
        )

    ## -- timers

    def _new_timer(self) -> None:
        if not self.client.public.has("timers"):
            self.client.simple_notify("mdi.timer-off-outline", "Timers",
                                      "The timer service is not available.")
            return
        # A picker, not a preset list. A list can only offer what somebody
        # guessed in advance, and "seven minutes" is not unreasonable to want.
        from src.ui.dialogs import DurationPickerDialog
        self.client.dialog(DurationPickerDialog(
            self.client, title="New timer", seconds=300,
            on_chosen=self._start_timer, choose_text="Start"))

    def _start_timer(self, seconds) -> None:
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return
        if seconds <= 0:
            return
        try:
            self.client.public.timers["start"](seconds)
        except Exception as e:
            self.client.log("warning", f"[ConfigurationBar] Timer failed: {e}")
            self.client.simple_notify("mdi.timer-off-outline", "Timers",
                                      "Could not start that timer.")

    def _open_widgets_panel(self) -> None:
        framework = self._framework()
        if framework is not None:
            framework.toggle_panel()

    def _framework(self):
        # Walk up to whoever owns this widget rather than holding a reference,
        # so a reload or a re-place cannot leave a stale pointer behind.
        node = self.parent()
        while node is not None and not hasattr(node, "toggle_panel"):
            node = node.parent()
        return node
