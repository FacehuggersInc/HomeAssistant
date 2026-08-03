"""
The panel's own switches, as tiles.

Everything here is a Quick Setting that somebody might rather have on the
home screen. Deliberately only Core Widgets' own: a plugin that wants a tile
for its own switch registers one itself, which keeps this from becoming the
place every plugin's UI lives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.ui.widgets.state_tile import StateTile, SliderTile, TileState

if TYPE_CHECKING:
    from src.main import Client


ON_BORDER = "#4f9de0"
ON_BACK   = "#1d3b57"
OFF_BACK  = "#22242a"


class DoNotDisturbTile(StateTile):
    KEY  = "do_not_disturb_tile"
    NAME = "Do not disturb"
    ICON = "mdi.bell-off-outline"

    STATES = [
        TileState("off", "Notifications", "mdi.bell-outline",
                  background=OFF_BACK, ink="#c9cfdb"),
        TileState("on", "Do not disturb", "mdi.bell-off",
                  background="#4a2130", border="#e0483f", ink="#ffd9d4"),
    ]

    def read_state(self) -> str:
        return "on" if self.client.do_not_disturb() else "off"

    def apply_state(self, key: str) -> None:
        self.client.set_do_not_disturb(key == "on")


class SilenceTile(StateTile):
    KEY  = "silence_tile"
    NAME = "Silence"
    ICON = "mdi.volume-off"

    STATES = [
        TileState("off", "Sounds on", "mdi.volume-high",
                  background=OFF_BACK, ink="#c9cfdb"),
        TileState("on", "Silenced", "mdi.volume-off",
                  background="#3a2159", border="#8a5fc0", ink="#e6dcff"),
    ]

    def read_state(self) -> str:
        return "on" if self.client.sounds_muted() else "off"

    def apply_state(self, key: str) -> None:
        self.client.set_sounds_muted(key == "on")


class _OpensSomething(StateTile):
    """
    A tile with one state that opens something when pressed.

    A `StateTile` with a single state is not a toggle - it is a button that
    happens to share the look. Worth having here rather than a second base
    class: what these do differs, how they look does not.
    """

    def next_state(self, key: str) -> str:
        return key

    def apply_state(self, key: str) -> None:
        self.open()

    def open(self) -> None:
        """What the press does."""


class TimersTile(_OpensSomething):
    KEY  = "timers_tile"
    NAME = "Timers"
    ICON = "mdi.timer-outline"

    STATES = [TileState("idle", "Timers", "mdi.timer-outline",
                        background=ON_BACK, border=ON_BORDER, ink="#dbe8f7")]

    def open(self) -> None:
        from src.assets.bundled.CoreWidgetsBundle.widgets.schedule_lists import (
            TimersDialog)
        if self.client.public.has("timers"):
            self.client.dialog(TimersDialog(self.client))


class AlarmsTile(_OpensSomething):
    KEY  = "alarms_tile"
    NAME = "Alarms"
    ICON = "mdi.alarm"

    STATES = [TileState("idle", "Alarms", "mdi.alarm",
                        background="#4a2a1f", border="#c0603f", ink="#f7dbcf")]

    def open(self) -> None:
        from src.assets.bundled.CoreWidgetsBundle.widgets.schedule_lists import (
            AlarmsDialog)
        if self.client.public.has("alarms"):
            self.client.dialog(AlarmsDialog(self.client))


class WebTile(_OpensSomething):
    KEY  = "web_tile"
    NAME = "Web"
    ICON = "mdi.earth"

    STATES = [TileState("idle", "Web", "mdi.earth",
                        background="#1f3b33", border="#4f9d6a", ink="#d4f0e2")]

    def open(self) -> None:
        self.client.goto("#webpage",
                         data={"url": self.client.BOOKMARKS_HOME},
                         override=True)


class VolumeTile(SliderTile):
    """Slide for volume, tap to mute."""

    KEY  = "volume_tile"
    NAME = "Volume"
    ICON = "mdi.volume-high"

    def __init__(self, client: "Client", **kwargs):
        self._known_volume = 0.0
        super().__init__(client, **kwargs)

    STATES = [
        TileState("on", "Volume", "mdi.volume-high",
                  background=OFF_BACK, border=ON_BORDER, ink="#dbe8f7"),
        TileState("muted", "Muted", "mdi.volume-off",
                  background="#3a2159", border="#8a5fc0", ink="#e6dcff"),
    ]

    def read_state(self) -> str:
        return "muted" if self.client.sounds_muted() else "on"

    def apply_state(self, key: str) -> None:
        self.client.set_sounds_muted(key == "muted")

    #The system mixer, not this panel's own player - the same one Quick
    #Settings drives, so the two never disagree about what the volume is.
    def _mixer(self):
        from src.system import volume as system_volume
        return system_volume

    def read_value(self) -> float:
        # Cached by the tick, never read here: `get_volume` shells out, and a
        # subprocess in a paint path is a tile that stutters under the finger.
        return self._known_volume

    def apply_value(self, value: float) -> None:
        level = int(round(value * 100))
        self._known_volume = value
        # On a worker, for the same reason Quick Settings does it: a drag
        # emits continuously, and one subprocess per emission judders.
        from threading import Thread
        Thread(target=lambda: self._mixer().set_volume(level),
               name="__tile_set_volume", daemon=True).start()

    def tick(self) -> None:
        super().tick()
        if self._sliding:
            return
        try:
            from threading import Thread

            def read():
                try:
                    level = float(self._mixer().get_volume())
                except Exception:
                    return
                self.client.call_on_ui(lambda: self._took_volume(level / 100.0))

            Thread(target=read, name="__tile_read_volume", daemon=True).start()
        except Exception:
            pass

    def _took_volume(self, share: float) -> None:
        if abs(share - self._known_volume) > 0.01 and not self._sliding:
            self._known_volume = self._value = max(0.0, min(1.0, share))
            self.update()


class BrightnessTile(SliderTile):
    """
    Slide for brightness; tap cycles full, low, and back to where it was.

    Three states rather than two because the useful third answer is "put it
    back". A tile that only has full and low loses whatever was set the first
    time it is pressed.
    """

    KEY  = "brightness_tile"
    NAME = "Brightness"
    ICON = "mdi.brightness-6"

    FULL, LOW = 100, 15

    STATES = [
        TileState("set", "Brightness", "mdi.brightness-6",
                  background=OFF_BACK, border="#e8c06a", ink="#f7ecd4"),
        TileState("full", "Full", "mdi.brightness-7",
                  background="#4a4021", border="#e8c06a", ink="#fff6dd"),
        TileState("low", "Dim", "mdi.brightness-4",
                  background="#1c1c22", border="#8a8a8a", ink="#c9cfdb"),
    ]

    def __init__(self, client: "Client", **kwargs):
        #What it was before the cycle started, so the third tap can undo it.
        self._remembered = 0
        super().__init__(client, **kwargs)

    def _level(self) -> int:
        try:
            return int(self.client.DIMMER.brightness())
        except Exception:
            return 0

    def read_state(self) -> str:
        level = self._level()
        if level >= self.FULL - 2:
            return "full"
        if level <= self.LOW + 2:
            return "low"
        return "set"

    def next_state(self, key: str) -> str:
        # Not a plain cycle: from anywhere else, the first tap goes full, and
        # the tap after low returns to what was there before rather than to
        # full - which is the point of remembering it.
        return {"set": "full", "full": "low", "low": "set"}.get(key, "full")

    def apply_state(self, key: str) -> None:
        if key == "full":
            self._remembered = self._level()
            self._set(self.FULL)
        elif key == "low":
            self._set(self.LOW)
        else:
            self._set(self._remembered or self.FULL)

    def read_value(self) -> float:
        return self._level() / 100.0

    def apply_value(self, value: float) -> None:
        self._set(int(round(value * 100)))

    def _set(self, level: int) -> None:
        self.client.DIMMER.set_brightness(max(1, min(100, int(level))))


DEFAULT_TILES = (DoNotDisturbTile, SilenceTile, TimersTile, AlarmsTile,
                 WebTile, VolumeTile, BrightnessTile)
