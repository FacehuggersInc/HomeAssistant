"""
Alarms: a time of day rather than a countdown.

Close enough to `timers.py` to read side by side, and different in three ways
that matter.

An alarm is set for a WALL CLOCK time, so it survives a restart. A timer is a
thing happening in the room over the next few minutes and a panel that rebooted
has already failed to count it; an alarm for seven tomorrow morning set at ten
at night has to still be there in the morning.

It has no widget. A countdown is worth watching and a time of day is not - what
somebody wants from a scheduled alarm is for it to go off, and to be able to
ask what is set.

And it repeats daily if asked, which a timer never does.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.main import Client


#How long a ringing alarm keeps making noise unless something stops it.
#Longer than a timer's: a timer is answered by somebody standing over it, and
#an alarm is answered by somebody who has to wake up first.
RING_SECONDS = 45.0

#How close two times have to be to count as the same alarm. A minute, because
#that is the resolution anybody speaks in - "the eight o'clock alarm" should
#find one set for 08:00:30.
SAME_ALARM = 60.0


def clock_text(when: float) -> str:
    """A time as somebody would say it: `4:40 PM`, `8 AM`."""
    moment = datetime.fromtimestamp(when)
    minutes = moment.strftime(":%M") if moment.minute else ""
    hour = moment.strftime("%I").lstrip("0") or "12"
    return f"{hour}{minutes} {moment.strftime('%p')}"


def day_text(when: float, now: float = None) -> str:
    """`today`, `tomorrow`, or the weekday - for saying which one."""
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now).date()
    that = datetime.fromtimestamp(when).date()
    days = (that - today).days
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days < 7:
        return datetime.fromtimestamp(when).strftime("%A")
    return datetime.fromtimestamp(when).strftime("%A %d %B")


def describe_alarm(when: float, now: float = None) -> str:
    """`4:40 PM today`, `8 AM tomorrow`."""
    return f"{clock_text(when)} {day_text(when, now)}"


class Alarm:
    """One alarm, at a wall clock time."""

    def __init__(self, when: float, name: str = "", key: str = "",
                 colour: str = "#c0603f", repeats: bool = False):
        self.when = float(when)
        self.name = str(name or "").strip()
        self.key = key or f"alarm_{uuid.uuid4().hex[:8]}"
        self.colour = colour
        self.repeats = bool(repeats)
        self.fired_at: Optional[float] = None

    def remaining(self, now: float = None) -> float:
        return max(0.0, self.when - (time.time() if now is None else now))

    @property
    def ringing(self) -> bool:
        return self.fired_at is not None

    def label(self) -> str:
        return self.name or clock_text(self.when)

    def as_dict(self) -> dict:
        return {"key": self.key, "when": self.when, "name": self.name,
                "colour": self.colour, "repeats": self.repeats}

    @classmethod
    def from_dict(cls, data: dict) -> Optional["Alarm"]:
        try:
            return cls(float(data["when"]), name=data.get("name", ""),
                       key=data.get("key", ""),
                       colour=data.get("colour", "#c0603f"),
                       repeats=bool(data.get("repeats")))
        except (KeyError, TypeError, ValueError):
            return None


class AlarmService:
    """
    Owns what is scheduled, and what is currently ringing.

    On the plugin rather than on a page, for the reason timers are: a page is
    destroyed whenever it is rebuilt, and an alarm that is cancelled by
    somebody visiting Settings is not an alarm.
    """

    #How long a ringing alarm stays up before it gives up on being answered.
    GIVE_UP_AFTER = 300

    #How long the ringing panel stays up. Longer than an ordinary answer,
    #because it is asking to be answered rather than reporting something.
    RINGING_TIMEOUT = 120

    #And how long the "stopped" one does. It reports; nobody has to act on it.
    CANCELLED_TIMEOUT = 8

    def __init__(self, plugin):
        self.plugin = plugin
        self.client: "Client" = plugin.client
        self.alarms: dict = {}          # key -> Alarm
        #The answer panel a ringing alarm put up, so it can be taken down by
        #something other than a tap on it.
        self._panels: dict = {}         # key -> AnswerPanel
        self._colour_index = 0
        self._subscribed = False

    ## -- lifecycle

    def start_watching(self) -> None:
        if not self._subscribed:
            self._load()
            self.client.subscribe_to_event("on_update", self._tick)
            self._subscribed = True

    def stop_watching(self) -> None:
        if self._subscribed:
            try:
                self.client.unsubscribe_from_event("on_update", self._tick)
            except Exception:
                pass
            self._subscribed = False
        self.silence()

    ## -- storing

    def _path(self):
        return self.client.DATAPATH / "alarms.json"

    def _load(self) -> None:
        """
        What was scheduled before the panel restarted.

        Anything already past is dropped rather than fired: a panel that was
        off overnight should not wake somebody at lunchtime with six alarms it
        owes them. A repeating one is moved to its next occurrence instead.
        """
        path = self._path()
        if not path.is_file():
            # Nothing set yet, or nothing ever set. Not a problem and not
            # worth a line in the log.
            return
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            # Said out loud, and kept. A file that cannot be read is a set of
            # alarms that will not go off, and somebody is relying on one -
            # so it is moved aside rather than overwritten by the first save
            # after this, which is what would destroy the evidence.
            self.client.log("warning",
                            f"[Alarms] Could not read {path.name} ({e}). "
                            f"Kept as {path.name}.bad; starting empty.")
            try:
                path.replace(path.with_suffix(".json.bad"))
            except Exception:
                pass
            return
        now = time.time()
        kept = 0
        for entry in data if isinstance(data, list) else []:
            alarm = Alarm.from_dict(entry)
            if alarm is None:
                continue
            if alarm.repeats:
                alarm.when = self._next_daily(alarm.when, now)
            elif alarm.when <= now:
                continue
            self.alarms[alarm.key] = alarm
            kept += 1
        if kept:
            self.client.log("info", f"[Alarms] {kept} alarm(s) restored.")

    def _save(self) -> None:
        """
        Write the file, or leave the old one alone.

        Written beside and renamed over, because the point of this file is
        surviving a crash - and a crash during `write_text` leaves it
        truncated, which is worse than not having written at all. `os.replace`
        is atomic on both platforms this runs on.
        """
        path = self._path()
        temp = path.with_suffix(".json.tmp")
        payload = [a.as_dict() for a in self.alarms.values()]
        try:
            temp.write_text(json.dumps(payload, indent=2))
            os.replace(temp, path)
        except Exception as e:
            self.client.log("warning", f"[Alarms] Could not save: {e}")
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _next_daily(when: float, now: float) -> float:
        """The same clock time, on the next day it has not happened yet."""
        moment = datetime.fromtimestamp(when)
        target = datetime.fromtimestamp(now).replace(
            hour=moment.hour, minute=moment.minute, second=moment.second,
            microsecond=0)
        if target.timestamp() <= now:
            target += timedelta(days=1)
        return target.timestamp()

    ## -- the api

    def schedule(self, when: float, name: str = "",
                 repeats: bool = False) -> Optional[Alarm]:
        """
        An alarm at an epoch time. `None` if it is not in the future.

        The caller does the language; this does the clock. Everything about
        "quarter past eight tomorrow" belongs where the phrasing is known -
        see CoreSkillsBundle.
        """
        try:
            when = float(when)
        except (TypeError, ValueError):
            return None
        if when <= time.time():
            return None

        alarm = Alarm(when, name=name, repeats=repeats,
                      colour=self._next_colour())
        self.alarms[alarm.key] = alarm
        self._save()
        self.client.log("info",
                        f"[Alarms] Set for {describe_alarm(alarm.when)}"
                        f"{f' ({alarm.name})' if alarm.name else ''}.")
        return alarm

    def _next_colour(self) -> str:
        """
        The next palette entry not already in use.

        The same walk timers do, and for the same reason: the dot beside a row
        is how two of them are told apart, and two alike defeats it. Shared
        with timers rather than a second palette, so a panel does not grow
        two.
        """
        from .timers import PALETTE

        in_use = {a.colour for a in self.alarms.values()}
        for _ in range(len(PALETTE)):
            colour = PALETTE[self._colour_index % len(PALETTE)]
            self._colour_index += 1
            if colour not in in_use:
                return colour
        colour = PALETTE[self._colour_index % len(PALETTE)]
        self._colour_index += 1
        return colour

    def cancel(self, key: str, announce: bool = True) -> bool:
        """
        Stop an alarm, whether it is scheduled or currently ringing.

        A ringing one is three things on screen at once: a noise, a panel
        offering to silence it, and the alarm itself. Cancelling has to deal
        with all three - a panel left up is still offering to silence
        something that has already gone.
        """
        alarm = self.alarms.pop(key, None)
        if alarm is None:
            return False

        was_ringing = alarm.ringing
        if was_ringing:
            # Silence first, for the reason a timer does: cancelling something
            # that is making a noise and having it carry on is the one thing
            # nobody expects.
            self._silence_sound()
        self._close_panel(key)
        self._save()

        if was_ringing and announce:
            self._say_cancelled(alarm)
        return True

    def _close_panel(self, key: str) -> None:
        panel = self._panels.pop(key, None)
        if panel is None:
            return
        # Its on_closed reaches _dismissed, which looks the alarm up and finds
        # nothing - the alarm has already been popped. Cleared anyway rather
        # than relied on.
        try:
            panel.on_closed = None
        except Exception:
            pass
        try:
            self.client.call_on_ui(panel.close_panel)
        except Exception:
            pass

    def _say_cancelled(self, alarm: Alarm) -> None:
        """A short panel in place of the one just taken away."""
        try:
            self.client.answer(
                "mdi.alarm-off", f"{alarm.label()} stopped",
                [f"Was set for {describe_alarm(alarm.when)}."],
                tint="#8a8a8a", timeout=self.CANCELLED_TIMEOUT)
        except Exception as e:
            self.client.log("debug", f"[Alarms] Could not confirm: {e}")

    def cancel_all(self) -> int:
        keys = list(self.alarms)
        for key in keys:
            self.cancel(key)
        return len(keys)

    def scheduled(self) -> list:
        """Everything set, soonest first."""
        return sorted((a for a in self.alarms.values() if not a.ringing),
                      key=lambda a: a.when)

    def ringing(self) -> list:
        return [a for a in self.alarms.values() if a.ringing]

    def get(self, key: str) -> Optional[Alarm]:
        return self.alarms.get(key)

    def find(self, when: float = 0, name: str = "",
             within: float = SAME_ALARM, repeats: bool = None) -> list:
        """
        Alarms matching a time, a name, or both.

        Time matching is against the time it is SET FOR, to the nearest
        minute, because that is the resolution anybody speaks in. Name
        matching is the same loose ladder timers use - a name comes from a
        transcript, so an exact compare fails on the input it has to survive.
        """
        import difflib

        candidates = list(self.alarms.values())
        if repeats is not None:
            # "cancel the daily alarm" narrows to the repeating ones, and on
            # its own is enough to identify them - there are rarely two.
            candidates = [a for a in candidates if a.repeats == bool(repeats)]
        if not candidates:
            return []

        if when:
            candidates = [a for a in candidates
                          if abs(a.when - float(when)) <= within]
            if not name:
                return sorted(candidates, key=lambda a: a.when)

        wanted = str(name or "").strip().lower()
        if not wanted:
            # Nothing to narrow by beyond the repeat filter. Everything, when
            # that filter was asked for; nothing, when it was not - or
            # "cancel the alarm" would take them all.
            if repeats is None:
                return []
            return sorted(candidates, key=lambda a: a.when)

        named = [a for a in candidates if a.name]
        for test in (lambda n: n == wanted,
                     lambda n: n.startswith(wanted),
                     lambda n: wanted in n,
                     lambda n: difflib.SequenceMatcher(
                         None, n, wanted).ratio() >= 0.75):
            hit = [a for a in named if test(a.name.lower())]
            if hit:
                return sorted(hit, key=lambda a: a.when)
        return []

    def cancel_matching(self, when: float = 0, name: str = "",
                        repeats: bool = None) -> list:
        """Cancel every alarm matching, and return the ones that went."""
        matched = self.find(when=when, name=name, repeats=repeats)
        for alarm in matched:
            self.cancel(alarm.key)
        return matched

    ## -- ringing

    def _tick(self, event=None) -> None:
        """
        On the client tick, which is a background thread.

        Only the crossing edge does anything.
        """
        now = time.time()
        for alarm in list(self.alarms.values()):
            if alarm.ringing or alarm.when > now:
                continue
            alarm.fired_at = now
            self._announce(alarm)

    def _announce(self, alarm: Alarm) -> None:
        spoken = (f"{alarm.name}." if alarm.name
                  else f"It's {clock_text(alarm.when)}.")

        # Before the panel. An alarm going off is the panel asking for
        # attention, so the idle clock is measuring the wrong thing - and this
        # wakes the screen, so a night clock is dismissed rather than left
        # sitting over the answer.
        try:
            self.client.reset_interaction_timeout()
        except Exception as e:
            self.client.log("warning", f"[Alarms] Could not reset idle: {e}")

        # AUDIO.play answers to `sounds_muted()` itself, which do not disturb
        # implies - so a silent panel stays silent and still shows the panel.
        try:
            seconds = float(self.plugin.setting_value(
                "alerts.alarm_ring_seconds", RING_SECONDS) or RING_SECONDS)
            if seconds > 0:
                self.client.AUDIO.play("timer_alarm", for_seconds=seconds)
        except Exception as e:
            self.client.log("debug", f"[Alarms] Could not sound it: {e}")

        lines = [f"Set for {describe_alarm(alarm.when)}.",
                 "Tap anywhere to silence it."]
        try:
            self.client.answer("mdi.alarm", alarm.label(), lines,
                               tint=alarm.colour, speak=spoken,
                               timeout=self.RINGING_TIMEOUT,
                               # Dismissing the panel deals with the alarm.
                               # One that keeps sounding after somebody has
                               # acknowledged it is the panel arguing.
                               on_closed=lambda k=alarm.key: self._dismissed(k),
                               # Kept, so cancelling from anywhere else can
                               # take this panel down with it.
                               on_built=lambda panel, k=alarm.key:
                                   self._panels.__setitem__(k, panel))
        except Exception as e:
            self.client.log("warning", f"[Alarms] Could not announce: {e}")
            try:
                self.client.simple_notify("mdi.alarm", alarm.label(), lines[0])
            except Exception:
                pass

        self.client.trigger_on_call_event_iteration("on_alarm_fired", alarm)

        # Answered or not, it stops eventually. An alarm nobody is near should
        # not be ringing when they get home.
        key = f"alarm_giveup:{alarm.key}"
        try:
            self.client.TIMEOUTS.add(int(self.plugin.setting_value(
                                         "alerts.alarm_give_up_after",
                                         self.GIVE_UP_AFTER)),
                                     lambda k=alarm.key: self._dismissed(k),
                                     key, transient=True)
            self.client.TIMEOUTS.start(key)
        except Exception:
            pass

    def _dismissed(self, key: str) -> None:
        """
        Somebody answered it - by tapping the panel, by saying stop, or by
        nobody answering for long enough.

        A repeating alarm is rescheduled rather than removed; a one-off goes.
        """
        alarm = self.alarms.get(key)
        if alarm is None:
            return
        self._silence_sound()
        self._close_panel(key)
        if alarm.repeats:
            alarm.fired_at = None
            alarm.when = self._next_daily(alarm.when, time.time())
            self._save()
            self.client.log("info",
                            f"[Alarms] {alarm.label()} again at "
                            f"{describe_alarm(alarm.when)}.")
            return
        # Answered rather than cancelled, so no "stopped" panel: the one that
        # was just tapped said everything there is to say.
        self.cancel(key, announce=False)

    def silence(self) -> int:
        """
        Stop everything currently ringing. Returns how many there were.

        What the cancel registry calls, and what a tap on the panel reaches
        through `_dismissed`.
        """
        ringing = self.ringing()
        for alarm in ringing:
            self._dismissed(alarm.key)
        if not ringing:
            # Nothing was ringing, but the sound may still be running from an
            # alarm that has already been cleared.
            self._silence_sound()
        return len(ringing)

    def _silence_sound(self) -> None:
        try:
            self.client.AUDIO.stop("timer_alarm")
        except Exception:
            pass
