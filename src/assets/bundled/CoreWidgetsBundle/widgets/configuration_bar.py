from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QHBoxLayout
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

        # An alarm, for the same reason: a time of day rather than a length,
        # and the same objection to having to say it out loud.
        self._alarm_btn = IconButton("mdi.alarm-plus",
                                     self._new_alarm, size=self.BUTTON // 2)
        self._alarm_btn.setFixedSize(self.BUTTON, self.BUTTON)
        row.addWidget(self._alarm_btn)

        self._draw_btn = IconButton("mdi.draw",
                                    self._whiteboard, size=self.BUTTON // 2)
        self._draw_btn.setFixedSize(self.BUTTON, self.BUTTON)
        row.addWidget(self._draw_btn)

        # The wallpaper, from the page the wallpaper is on.
        #
        # These were in the quick settings header, which is reachable from
        # every page - and the cycling background only exists while sub.home
        # is built, so they were hidden almost everywhere they could be
        # reached from. Here they are on the page they act on.
        self._wallpaper_btn = IconButton(Icons.IMAGE, self._cycle_wallpaper,
                                         size=self.BUTTON // 2)
        self._wallpaper_btn.setFixedSize(self.BUTTON, self.BUTTON)
        row.addWidget(self._wallpaper_btn)

        self._pin_btn = IconButton(Icons.PIN, self._pin_wallpaper,
                                   size=self.BUTTON // 2)
        self._pin_btn.setFixedSize(self.BUTTON, self.BUTTON)
        row.addWidget(self._pin_btn)

        # An explicit size, not adjustSize(): the bar goes into a graphics
        # proxy before its layout has run, so leaving it to size itself left
        # the buttons clipped by a box smaller than its own contents.
        inner_h = max(self.notifications.height(), self.BUTTON)
        # Counted rather than written out. Three buttons was two additions
        # ago, and a width that has to be edited every time one is added is a
        # width that will be wrong.
        buttons = 6
        self.setFixedSize(
            10 + self.notifications.width()
            + (8 + self.BUTTON) * buttons + 10,
            inner_h + 16,
        )

    ## -- wallpaper

    def _wallpaper(self, name: str):
        """
        One of the background's own functions, or nothing.

        Looked up on every press rather than held. This widget is built by the
        same mixin that publishes them and can run first, and the page can be
        torn down and rebuilt under it - a reference taken once is a reference
        to a background that may be gone.
        """
        try:
            if not self.client.public.has("cwb_wallpaper"):
                return None
            found = self.client.public.cwb_wallpaper.get(name)
        except Exception:
            return None
        return found if callable(found) else None

    def _cycle_wallpaper(self) -> None:
        action = self._wallpaper("cycle")
        if action is None:
            return
        try:
            action()
        except Exception as e:
            self.client.log("warning",
                            f"[ConfigurationBar] Wallpaper cycle failed: {e}")
        self.refresh_wallpaper()

    def _pin_wallpaper(self) -> None:
        action = self._wallpaper("toggle_pin")
        if action is None:
            return
        try:
            action()
        except Exception as e:
            self.client.log("warning",
                            f"[ConfigurationBar] Wallpaper pin failed: {e}")
        self.refresh_wallpaper()

    def refresh_wallpaper(self) -> None:
        """
        Put the two buttons in step with the background.

        Only the buttons themselves change either state, so this runs on a
        press rather than on a tick - a bar that polls the background once a
        second to redraw an icon that cannot have changed is a bar that costs
        something for nothing.
        """
        available = self._wallpaper("cycle") is not None
        self._wallpaper_btn.setVisible(available)
        self._pin_btn.setVisible(available)
        if not available:
            return

        pinned = False
        check = self._wallpaper("is_pinned")
        if check is not None:
            try:
                pinned = bool(check())
            except Exception:
                pinned = False
        self._pin_btn.update_icon(Icons.UNPIN if pinned else Icons.PIN)

        # Cycling a pinned wallpaper does nothing, so the button says so.
        can_cycle = self._wallpaper("can_cycle")
        try:
            self._wallpaper_btn.setEnabled(
                bool(can_cycle()) if can_cycle is not None else True)
        except Exception:
            self._wallpaper_btn.setEnabled(True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The first refresh. It cannot happen in __init__: this is built by the
        # mixin that publishes the background, and may be built first.
        try:
            self.refresh_wallpaper()
        except Exception:
            pass

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

    ## -- alarms

    def _new_alarm(self) -> None:
        if not self.client.public.has("alarms"):
            self.client.simple_notify("mdi.alarm-off", "Alarms",
                                      "The alarm service is not available.")
            return
        from .alarm_picker import AlarmPickerDialog
        self.client.dialog(AlarmPickerDialog(
            self.client, title="New alarm", on_chosen=self._set_alarm))

    def _set_alarm(self, when, repeats: bool = False) -> None:
        try:
            alarm = self.client.public.alarms["schedule"](
                when, repeats=bool(repeats))
        except Exception as e:
            self.client.log("warning", f"[ConfigurationBar] Alarm failed: {e}")
            alarm = None
        if alarm is None:
            self.client.simple_notify("mdi.alarm-off", "Alarms",
                                      "Could not set that alarm.")
            return
        said = self.client.public.alarms["describe"](alarm.when)
        self.client.simple_notify(
            "mdi.alarm", "Alarm set",
            f"{said}, every day" if alarm.repeats else said)

    ## -- the whiteboard

    def _whiteboard(self) -> None:
        from .whiteboard import WhiteboardDialog
        self.client.dialog(WhiteboardDialog(self.client, on_saved=self._stuck))

    def _stuck(self, sticker, longest: int = 0) -> None:
        """
        Drawn, saved, and put up.

        Placed as well as saved because the whole point was sticking it on
        the wall - a drawing that lands in a folder and waits to be chosen is
        two more steps than anybody wants while holding a pen.
        """
        placed, reason = False, ""
        try:
            placed, reason = self.client.public.stickers["place"](
                sticker.key, "center",
                # "custom" with the size it was drawn at, rather than the
                # default share of the panel width - which would shrink a
                # careful drawing or blow up a doodle.
                scale="custom", size=int(longest or 0))
        except Exception as e:
            reason = str(e)
        if not placed and reason:
            self.client.log("warning", f"[Whiteboard] Not placed: {reason}")
        self.client.simple_notify(
            "mdi.draw", "Whiteboard",
            "Saved to your stickers and put on the home screen." if placed
            else "Saved to your stickers.")

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
