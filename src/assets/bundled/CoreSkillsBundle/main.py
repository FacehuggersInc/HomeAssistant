from __future__ import annotations

import random
import time as _time
from datetime import date, datetime, timedelta

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt

from src.plugin.template import Plugin
from src.assistant.skill import Skill, SkillDeclined
from .skills import build_all
from .skills import units
from .skills.helpers import (
    FILLER,
    PAGE_VERBS,
    _alarm_epoch,
    _clean_label,
    _clean_words,
    _clock,
    _overlap,
    _spoken_wait,
    _spoken_duration,
    title_matches,
    wikipedia_subject,
)

from src.styling import make_font, SIZES, set_style

from .voice_bar import VoiceBar


class CoreSkills(Plugin):

    def _describe(self, seconds) -> str:
        """
        A duration as a noun phrase, from whoever owns timers.

        Through the public registry rather than by importing the module it
        lives in. `plugin.toml` makes `corewidgetsbundle` a dependency, so an
        import would load - but a dependency is what makes an import legal,
        not what makes it right: everything else about timers already comes
        through `client.public.timers`, and one function arriving by a
        different route is one more thing to keep in step.
        """
        try:
            return self.client.public.timers["describe"](seconds)
        except Exception:
            return f"{int(seconds)} seconds"


    ## CORE

    def __init__(self):
        self.skills = []
        self.voice_bar = None
        self._last_state = (None, None)

    def load(self, carryover=None):
        # Registered before the skills, so a skill built during load_skills
        # that wants it finds it there rather than on the second try.
        # Declared before anything can subscribe - subscribe_to_event indexes
        # straight into the event table, so a name that has not been created
        # is a KeyError rather than a quiet no-op.
        for name in ("on_dictionary_lookup_failed",
                     "on_wikipedia_lookup_failed"):
            if name not in self.client.EVENTS["on_call"]:
                self.client.create_on_call_event(name)

        # A word the dictionary does not have is very often a proper noun, a
        # place, or a species - the things an encyclopedia has and a
        # dictionary does not. Wired through the event rather than by calling
        # one handler from the other, so anything else can answer a missed
        # word too and this plugin has no special claim on it.
        self.client.subscribe_to_event("on_dictionary_lookup_failed",
                                       self.on_dictionary_missed)

        from .api.dictionary import DictionaryAPI
        from .api.wikipedia import WikipediaAPI
        self.client.API.register_api("coreskillsbundle", "dictionary",
                                     DictionaryAPI(self, self.client))
        self.client.API.register_api("coreskillsbundle", "wikipedia",
                                     WikipediaAPI(self, self.client))
        self.load_skills()
        self.client.subscribe_to_event("on_update", self.update_assistant)
        self.client.subscribe_to_event("on_assistant_transcribed", self.on_routed)
        self.client.subscribe_to_event("on_transcribing_assistant",
                                       self.on_transcribing)
        self.client.subscribe_to_event("on_transcribed_assistant",
                                       self.on_transcribed)
        self.client.subscribe_to_event("on_heard_assistant", self.on_heard)
        self.client.subscribe_to_event("on_woke_assistant", self.on_woke)
        self.client.subscribe_to_event("on_assistant_cancelled", self.on_cancelled)
        # Whether anything took the phrase, so the transcript on the bar can
        # say which it was rather than looking refused either way.
        self.client.subscribe_to_event("on_skill_called", self.on_matched)
        self.client.subscribe_to_event("on_assistant_fallback", self.on_fallback)

    def built(self):
        self.load_voice_bar()

    def unload(self, carryover=None):
        self.client.unsubscribe_from_event("on_dictionary_lookup_failed",
                                           self.on_dictionary_missed)
        self.client.API.unregister_api("coreskillsbundle", "dictionary")
        self.client.API.unregister_api("coreskillsbundle", "wikipedia")
        self.client.unsubscribe_from_event("on_update", self.update_assistant)
        self.client.unsubscribe_from_event("on_assistant_transcribed", self.on_routed)
        self.client.unsubscribe_from_event("on_transcribing_assistant",
                                           self.on_transcribing)
        self.client.unsubscribe_from_event("on_transcribed_assistant",
                                           self.on_transcribed)
        self.client.unsubscribe_from_event("on_heard_assistant", self.on_heard)
        self.client.unsubscribe_from_event("on_woke_assistant", self.on_woke)
        self.client.unsubscribe_from_event("on_skill_called", self.on_matched)
        self.client.unsubscribe_from_event("on_assistant_fallback", self.on_fallback)
        self.client.unsubscribe_from_event("on_assistant_cancelled", self.on_cancelled)
        if self.voice_bar is not None:
            try:
                self.client.OVERLAYS.remove("TOPMOST", self.voice_bar)
                self.voice_bar.setParent(None)
                self.voice_bar.deleteLater()
            except Exception:
                pass
            self.voice_bar = None

    ## WAKE WORD

    def wake_word(self) -> str:
        # Plugin setting wins if set, otherwise the app-wide one so a user
        # changing it in Assistant settings does not have to change it twice.
        try:
            own = str(self.settings.general.wake_word.value).strip()
        except Exception:
            own = ""
        return own.lower() or self.client.wake_word

    ## VOICE BAR

    def load_voice_bar(self):
        try:
            if not self.client.SETTINGS.assistant.feedback.voice_bar.value:
                return
        except Exception:
            pass

        self.voice_bar = VoiceBar(self.client)
        self.client.OVERLAYS.add("TOPMOST", self.voice_bar)
        self.voice_bar.anchor(self.client.OVERLAYS)
        self.client.subscribe_to_event("on_state_change", self._reanchor)

    def _reanchor(self, event=None):
        if self.voice_bar is not None:
            self.client.call_on_ui(lambda: self.voice_bar.anchor(self.client.OVERLAYS))

    def on_routed(self, event):
        """
        The transcript, after normalising and before it is acted on.

        Named apart from `on_transcribed` deliberately: both were called
        that, in this class, subscribed to two DIFFERENT events - so the
        second definition replaced the first and `on_assistant_transcribed`
        ran the wrong body. The bar cleared its waiting message where it was
        meant to show the words.

        The transcriber only emits finished transcripts, so there is no
        partial stream to show while somebody is talking. The meter covers
        "hearing something"; this is "heard this".
        """
        if self.voice_bar is None:
            return
        text = event if isinstance(event, str) else str(event or "")
        self.client.call_on_ui(lambda: self.voice_bar.show_heard(text))

    def on_matched(self, event=None):
        """A skill took the phrase. Say so on the bar."""
        if self.voice_bar is None:
            return
        self.client.call_on_ui(lambda: self.voice_bar.mark_heard(True))

    def on_fallback(self, event=None, context=None):
        """
        Nothing took it. The grey stays, and now it means something.

        `context` is the turn before this one, which this handler has no use
        for - it is here so the signature matches what the event now sends.
        """
        if self.voice_bar is None:
            return
        self.client.call_on_ui(lambda: self.voice_bar.mark_heard(False))

    def on_cancelled(self, event=None):
        if self.voice_bar is None:
            return
        self.client.call_on_ui(lambda: self.voice_bar.show_cancelled())

    def on_transcribing(self, event=None):
        """
        Audio captured, the model is working on it.

        The stage that covers the wait. On a big model this is seconds, and
        nothing used to be shown during them - the pill from the wake faded
        and the answer arrived later out of nowhere, which reads as the panel
        having missed you and then changed its mind.
        """
        if self.voice_bar is None:
            return
        self.client.call_on_ui(self.voice_bar.show_transcribing)

    def on_transcribed(self, event=None):
        """
        The model finished, whatever it decided.

        Only clears the WAITING message. If a phrase came back, `on_heard`
        has already replaced this with the words - and taking that down here
        would remove the one thing worth reading a moment after it appeared.
        """
        if self.voice_bar is None:
            return
        self.client.call_on_ui(self.voice_bar.done_transcribing)

    def on_heard(self, event):
        """
        A phrase arrived and is being looked up. Show it.

        The middle of the three stages. It fires before the skill search, so
        a phrase that matches nothing still leaves the person knowing what
        the panel heard - which is the difference between "it misheard me"
        and "there is no skill for that".
        """
        if self.voice_bar is None:
            return
        said = str(event or "").strip()
        self.client.call_on_ui(lambda: self.voice_bar.show_understood(said))

    def on_woke(self, event):
        """
        A skill matched. Say what was understood.

        The event carries `(skill, phrase)` - it fires from `process_phrase`
        AFTER parsing, so by the time this runs the panel is acting rather
        than listening. It used to show "<wake word> - listening…", which was
        wrong twice: it had stopped listening, and the word it named was the
        matched SKILL's own wake word rather than the one the person says.

        The phrase is what is worth showing. It is the panel repeating back
        what it heard, which is the one thing that says whether it got it
        right.
        """
        if self.voice_bar is None:
            return

        said = ""
        if isinstance(event, (tuple, list)) and len(event) >= 2:
            said = str(event[1] or "").strip()
        elif isinstance(event, str):
            said = event.strip()

        # Without a phrase there is still something worth saying: it
        # understood, even if this does not know what.
        shown = said or "Got it\u2026"
        self.client.call_on_ui(lambda: self.voice_bar.show_matched(shown))

    def update_assistant(self, event):
        # on_update runs on the update thread; widgets must be touched on the
        # Qt thread, and only when something actually changed.
        if self.voice_bar is None:
            return

        state = (self.client.ASSIST_STATUS, round(self.client.ASSIST_VOICE_ACTIVITY_LEVEL, 2))
        if state == self._last_state:
            # Nothing moved. The bar is still asked to check itself, because
            # what it is SHOWING can be wrong while the status is right: a
            # phrase that arrives already answered goes LIVE -> THINKING ->
            # LIVE between two polls, so this sees no change at all, and the
            # pill that was put up in the middle of it is never taken down.
            self.client.call_on_ui(self.voice_bar.check_still_wanted)
            return
        self._last_state = state

        status, level = state
        self.client.call_on_ui(lambda: self.voice_bar.apply_state(status, level))

    ## SKILLS

    def load_skills(self):
        wake = self.wake_word()
        key = self.config["plugin"]["key"]

        # One module per group, in skills/. The declarations are the
        # bulk of a skill and none of them need this file; the
        # handlers they point at are what stays.
        self.skills = build_all(self, wake, key)
        self.client.SKILLS.register(key, self.skills)

    ## SETTINGS PAGE

    def settings_blocks(self) -> list:
        """
        Cards for this plugin's settings page, between the registry summary
        and the settings themselves. Rendered by SettingsPage via the
        settings_blocks() hook - any plugin can do this.
        """
        if not self.skills:
            return []

        container = QWidget()
        set_style(container, "common", "transparent")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.setSpacing(10)

        heading = QLabel(
            f"Skills  ·  {len(self.skills)} registered to \u201c{self.wake_word()}\u201d"
        )
        heading.setFont(make_font(SIZES.S2, bold=True))
        set_style(heading, "common", "text-muted")
        outer.addWidget(heading)

        for skill in sorted(self.skills, key=lambda s: getattr(s, "key", "")):
            outer.addWidget(self._skill_card(skill))

        return [container]

    def _skill_card(self, skill) -> QFrame:
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(card, "settings", "registry-card")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        title = QLabel(getattr(skill, "key", "unnamed"))
        title.setFont(make_font(SIZES.S3, bold=True))
        set_style(title, "common", "text-strong")
        top.addWidget(title)

        examples = list(getattr(skill, "examples", []) or [])
        badge = QLabel(f"{len(examples)} phrase" + ("s" if len(examples) != 1 else ""))
        badge.setFont(make_font(SIZES.S1, bold=True))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(badge, "settings", "registry-count")
        top.addWidget(badge)

        arguments = list((getattr(skill, "arguments", None) or {}).keys())
        if arguments:
            args_badge = QLabel(", ".join(arguments))
            args_badge.setFont(make_font(SIZES.S1, bold=True))
            args_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_style(args_badge, "settings", "skill-args")
            top.addWidget(args_badge)

        top.addStretch()
        layout.addLayout(top)

        wake = getattr(skill, "wake", "") or self.wake_word()
        word_min = getattr(skill, "word_min", None)
        word_max = getattr(skill, "word_max", None)
        meta = f"say \u201c{wake} …\u201d"
        if word_min is not None and word_max is not None:
            meta += f"   ·   {word_min}-{word_max} words"
        sub = QLabel(meta)
        sub.setFont(make_font(SIZES.S1))
        set_style(sub, "common", "text-muted")
        layout.addWidget(sub)

        # A couple of real phrases are worth more than the whole list, which
        # runs to sixteen entries on some skills.
        for phrase in examples[:3]:
            row = QLabel(f"\u201c{phrase}\u201d")
            row.setFont(make_font(SIZES.S1))
            row.setWordWrap(True)
            row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            set_style(row, "settings", "registry-entry")
            layout.addWidget(row)

        if len(examples) > 3:
            more = QLabel(f"+{len(examples) - 3} more")
            more.setFont(make_font(SIZES.S1))
            set_style(more, "common", "text-muted")
            layout.addWidget(more)

        return card

    ## SKILL BODIES

    def nevermind(self, phrase: str = "", **_ignored):
        """
        Back out of whatever is in front.

        What that means depends on what is going on: an answer panel should
        close, music should stop, and with neither of those the assistant
        should simply stand down. And the words are not interchangeable -
        "stop" fits music where "nevermind" does not - so each thing that can
        be cancelled registers its own words and its own condition on
        `client.CANCEL`, and this asks rather than holding a list of cases that
        would grow every time something new appeared.
        """
        action = self.client.CANCEL.run(phrase)

        if action is None:
            # Nothing was in front. Standing down is the whole instruction.
            self.client.cancel_assistant("nevermind")
            return

        if action.stops_listening:
            self.client.cancel_assistant(f"nevermind: {action.key}")
    def quit_application(self):
        try:
            confirm = bool(self.settings.general.confirm_quit.value)
        except Exception:
            confirm = True

        # Spoken confirmation needs both ends of the voice loop. Without TTS
        # the user would be answering a question they never heard, so fall
        # back to an on-screen confirm instead.
        if not confirm or self.client.STT is None or not self.client.say(
                "Are you sure you want to quit the application? Please say Yes or No.",
                thread=False):
            self.client.confirm(
                "Quit application?", "Asked by voice command.",
                confirm_text="Quit", destructive=True,
                on_confirm=lambda: self.client.call_on_ui(self.client.stop),
            )
            return

        session = self.client.STT.new_session()
        reprompt = None
        with session:
            while True:
                phrase = session.wait_for_phrase()
                if phrase is None:
                    break
                phrase = phrase.lower().strip()
                if phrase in ("yes", "yeah", "yup", "sure", "absolutely", "correct"):
                    self.client.call_on_ui(self.client.stop)
                    break
                if phrase in ("no", "nope", "nah", "negative", "incorrect"):
                    break
                if reprompt is None:
                    reprompt = True
                    self.client.say("I didn't quite get that. Please say Yes or No.",
                                    thread=False)

    def tell_time(self):
        """"what time is it", "what's the time"."""
        now = datetime.now()

        # The clock widget's format, not a second opinion. A panel set to 24
        # hour that answers out loud in 12 is two clocks disagreeing in the
        # same room.
        try:
            fmt = str(self.client.setting("home.clock.time_format.value",
                                          "%I:%M %p"))
        except Exception:
            fmt = "%I:%M %p"
        shown = now.strftime(fmt)
        # Only for twelve hour. "09:00" is how a 24 hour clock is written and
        # stripping it to "9:00" is a different convention, not a tidier one.
        if "%I" in fmt:
            shown = shown.lstrip("0") or now.strftime("%I:%M %p")

        # Spoken separately, and always in twelve hour. "Seventeen forty" is
        # a correct reading of the clock and not how anybody says it.
        minute = now.minute
        hour = now.hour % 12 or 12
        if minute == 0:
            said = f"It's {hour} o'clock"
        elif minute < 10:
            said = f"It's {hour} oh {minute}"
        else:
            said = f"It's {hour} {minute}"
        said += (" in the morning" if now.hour < 12 else
                 " in the afternoon" if now.hour < 18 else
                 " in the evening" if now.hour < 21 else " at night")

        self.client.answer("mdi.clock-outline", shown,
                           [now.strftime("%A, %B ") + str(now.day)],
                           tint="#4f9de0", speak=said + ".")

    def tell_relative_date(self, given_date: str = ""):
        def ordinal(n: int) -> str:
            if 10 <= n % 100 <= 20:
                suffix = "th"
            else:
                suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            return f"{n}{suffix}"

        def readable(d: date) -> str:
            """For speech. The strftime already ends in a space."""
            return f"{d.strftime('%B, %A the')} {ordinal(d.day)}"

        def heading(d: date) -> str:
            """For the panel. A heading, not a sentence read aloud."""
            return f"{d.strftime('%A')} {ordinal(d.day)} {d.strftime('%B')}"

        today = date.today()
        given_date = (given_date or "").lower()

        if "yesterday" in given_date or "before today" in given_date:
            when, spoken = today - timedelta(days=1), "Yesterday was,"
        elif "tomorrow" in given_date or "after today" in given_date:
            when, spoken = today + timedelta(days=1), "Tomorrow is,"
        elif ("today" in given_date or "date" in given_date
              or "day" in given_date or not given_date):
            # Nothing captured means the phrasing got past the patterns but
            # not into them. Every example of this skill asks about a day, and
            # of those today is the one somebody asks for without saying which
            # - so it is a better answer than a refusal.
            #
            # "date" and "day" for the same reason one step further along.
            # "what's the date" captures the words "the date", which named no
            # day at all and so refused - a question this skill exists to
            # answer, answered with "I don't know how to answer that".
            when, spoken = today, "Today is,"
        else:
            self._respond("I don't know how to answer that.")
            return

        answer = f"{spoken} {readable(when)}"
        # Shown as well as said. A date is a thing to read off a wall rather
        # than catch as it goes past, and the day somebody just asked about
        # is the day they are about to ask what is on.
        self.client.answer("mdi.calendar", heading(when),
                           self._day_lines(when), tint="#4f9de0", speak=answer)

    def _day_lines(self, when: date) -> list:
        """
        What is on that day, one line each.

        Empty when there is no calendar, or nothing on. The panel is worth
        showing either way - the date is the answer, and the events are what
        the question was usually leading to.
        """
        try:
            if not self.client.public.has("calendar"):
                return []
            api = self.client.public.calendar
            events = api["on_day"](when)
        except Exception:
            return []

        lines = []
        for event in events or []:
            if getattr(event, "all_day", False):
                lines.append(f"All day  -  {event.title}")
                continue
            starts = getattr(event, "starts_at", None)
            clock = ""
            if starts is not None:
                pattern = "%I:%M %p" if starts.minute else "%I %p"
                clock = starts.strftime(pattern).lstrip("0")
            lines.append(f"{clock}  -  {event.title}" if clock else event.title)
        return lines or ["Nothing on."]

    def empty_notifications(self):
        if not self.client.public.has("notification_history"):
            self._respond("There is no notification history to clear.")
            return

        history = self.client.public.notification_history
        try:
            count = len(history.items)
        except Exception:
            count = -1
        history.clear()

        # Answered, like every other skill here.
        #
        # This cleared the list and said nothing - spoken or shown - so from
        # the outside a working clear and a skill that failed to match looked
        # identical.
        if count < 0:
            self._respond("Notifications cleared.")
        elif count == 0:
            self._respond("There was nothing to clear.")
        else:
            self._respond(f"Cleared {count} "
                          f"notification{'s' if count != 1 else ''}.")

    def open_notifications(self):
        if not self.client.public.has("notification_history"):
            self._respond("There is no notification history.")
            return
        # Through what the history exposes, rather than reaching for the
        # widget behind it. The manager lives on the home page, so it can be
        # absent - which is what asking for this from another page does.
        if not self.client.public.notification_history.open():
            self._respond("The notification list is only on the home screen.")
            return
        # Not spoken on success: the panel opening IS the answer, and reading
        # it out over the list somebody is now looking at helps nobody.
    def weather_update(self):
        api = self.client.API.get("weather")
        if api is None:
            self._respond("The weather plugin is not loaded.")
            return
        try:
            data = api.get_current_weather()
            # Nested quotes inside an f-string only became legal in 3.12, and
            # this plugin should stay readable regardless.
            temperature = int(data["temperature_2m"])
        except Exception as e:
            self.client.log("warning", f"[CoreSkills] Weather lookup failed: {e}")
            self._respond("I couldn't get the weather right now.")
            return

        # A panel, not a notification. The weather has more to say than one
        # number, and a toast is the wrong shape for four lines - it goes
        # past before anybody has read the second one.
        lines = []
        for label, key, unit in (("Feels like", "apparent_temperature", "\u00b0"),
                                 ("Cloud cover", "cloud_cover", "%"),
                                 ("Wind", "wind_speed_10m", " mph"),
                                 ("Gusts", "wind_gusts_10m", " mph"),
                                 ("Humidity", "relative_humidity_2m", "%")):
            value = data.get(key)
            if value is None:
                continue
            lines.append(f"{label}: {int(value)}{unit}")

        rain = data.get("precipitation")
        if rain:
            lines.append(f"Precipitation: {float(rain):.2f} in")

        is_day = bool(data.get("is_day", 1))
        glyph = "mdi.weather-sunny" if is_day else "mdi.weather-night"
        try:
            glyph = api.get_icon(data) or glyph
        except Exception:
            pass

        self.client.answer(glyph, f"{temperature} degrees", lines,
                           tint="#3f7fbf" if is_day else "#3a2159",
                           speak=self._weather_spoken(temperature, data))

    def _weather_api(self):
        """The weather plugin's API, or None with the answer already given."""
        api = self.client.API.get("weather")
        if api is None:
            self._respond("The weather plugin is not loaded.")
        return api

    def humidity_update(self):
        """"how humid is it", "what's the humidity"."""
        api = self._weather_api()
        if api is None:
            return
        data = api.get_current_weather() or {}
        try:
            humidity = int(float(data["relative_humidity_2m"]))
        except (KeyError, TypeError, ValueError):
            self._respond("I couldn't get the humidity right now.")
            return

        # The number, then what it means. Nobody standing in a room knows
        # whether 64% is a lot, and the whole point of asking is to find out
        # whether to expect a sticky afternoon.
        if humidity < 30:
            feel = "Dry."
        elif humidity < 60:
            feel = "Comfortable."
        elif humidity < 75:
            feel = "Getting muggy."
        else:
            feel = "Humid."

        lines = [feel]
        try:
            dew = float(data["temperature_2m"])
            feels = float(data["apparent_temperature"])
            if abs(feels - dew) >= 3:
                lines.append(f"{int(dew)}\u00b0 out, feels like {int(feels)}\u00b0.")
        except (KeyError, TypeError, ValueError):
            pass

        self.client.answer("mdi.water-percent", f"{humidity}% humidity", lines,
                           tint="#3f8fa8",
                           speak=f"It's {humidity} percent humidity. {feel}")

    #How close a chance has to get before it is worth mentioning. Below this
    #the forecast is saying no, and "a 4% chance at 3pm" is a way of saying no
    #that sounds like a yes.
    RAIN_LIKELY = 30

    def precipitation_update(self, phrase: str = ""):
        """
        "is it going to rain", "is it snowing", "will it snow tonight".

        Takes the whole phrase because rain and snow are the same skill asked
        about different weather. Splitting them into two skills would put
        "will it rain or snow" in a competition between them, and the words
        that separate them are the only difference between the utterances.
        """
        api = self._weather_api()
        if api is None:
            return

        asked_snow = any(word in (phrase or "").lower()
                         for word in ("snow", "snowing", "flurr", "sleet"))

        current = api.get_current_weather() or {}
        outlook = api.get_precipitation_outlook(12)
        if not outlook:
            self._respond("I couldn't get the forecast right now.")
            return

        # Falling now beats going to fall. Somebody who can hear it on the
        # window is asking how long it lasts, not whether it started.
        falling = ""
        for key, word in (("snowfall", "snowing"), ("showers", "showering"),
                          ("rain", "raining")):
            try:
                if float(current.get(key) or 0) > 0:
                    falling = word
                    break
            except (TypeError, ValueError):
                continue

        # Separated from "0%" on purpose. A probability the model did not
        # return is not a probability of nothing, and reporting the absence
        # as a confident zero is the panel making the number up.
        known = [(when, chance) for when, chance, _, _ in outlook
                 if chance is not None]
        peak_at, peak_chance = (max(known, key=lambda row: row[1])
                                if known else (None, None))
        first = next((when for when, chance, _, _ in outlook
                      if chance is not None and chance >= self.RAIN_LIKELY),
                     None)

        total = sum(amount or 0.0 for _, _, amount, _ in outlook)
        snow  = sum(depth or 0.0 for _, _, _, depth in outlook)
        hours = len(outlook)
        word  = "Snow" if asked_snow else "Rain"

        if falling and (asked_snow == (falling == "snowing")):
            # Only when it answers what was asked. "Is it snowing" while rain
            # is falling is a no, and "It's raining" as the headline reads as
            # a yes to somebody who asked about snow.
            headline = f"It's {falling}"
            glyph, tint = "mdi.weather-pouring", "#3a6ea8"
        elif asked_snow and snow < 0.01 and not falling:
            headline = "No snow expected"
            glyph, tint = "mdi.weather-partly-cloudy", "#3f7fbf"
        elif first is not None:
            headline = f"{word} likely by {_clock(first)}"
            glyph = "mdi.weather-snowy" if asked_snow else "mdi.weather-rainy"
            tint = "#3a6ea8"
        elif peak_chance is None:
            headline = f"No {word.lower()} forecast"
            glyph, tint = "mdi.weather-cloudy-alert", "#6a6a7a"
        else:
            headline = f"No {word.lower()} expected"
            glyph, tint = "mdi.weather-partly-cloudy", "#3f7fbf"

        lines = []
        if falling and not (asked_snow == (falling == "snowing")):
            lines.append(f"It's {falling}, though.")
        if peak_chance is not None:
            lines.append(f"Highest chance {int(peak_chance)}% at {_clock(peak_at)}.")
        else:
            lines.append("No hourly chances came back for here.")
        if asked_snow:
            lines.append(f"About {snow:.1f} in of snow over the next {hours} hours."
                         if snow >= 0.1 else
                         f"No snow in the next {hours} hours.")
        elif total >= 0.01:
            lines.append(f"About {total:.2f} in over the next {hours} hours.")
        else:
            lines.append(f"Nothing measurable in the next {hours} hours.")

        if falling and (asked_snow == (falling == "snowing")):
            spoken = f"It's {falling} right now"
            if first is None and peak_chance is not None:
                spoken += ", and it should ease off within the hour"
        elif first is not None:
            spoken = (f"{word} looks likely by {_clock(first)}, "
                      f"peaking around {int(peak_chance)} percent")
        elif peak_chance is None:
            spoken = f"I don't have an hourly {word.lower()} forecast for here"
        else:
            spoken = (f"No {word.lower()} expected today. The highest chance "
                      f"is {int(peak_chance)} percent")

        self.client.answer(glyph, headline, lines, tint=tint,
                           speak=spoken + ".")

    def wind_update(self):
        """"how windy is it", "which way is the wind blowing"."""
        api = self._weather_api()
        if api is None:
            return
        data = api.get_current_weather() or {}
        try:
            speed = float(data["wind_speed_10m"])
        except (KeyError, TypeError, ValueError):
            self._respond("I couldn't get the wind right now.")
            return

        described = api.beaufort_word(speed)
        heading = api.compass(data.get("wind_direction_10m"))
        # Beaufort 0 and 1 are "calm" and "light air", and nobody standing
        # outside can tell them apart. Both are the answer "there's no wind",
        # so both get said that way rather than reported as a reading.
        still = api.get_beaufort_scale(speed) <= 1

        lines = []
        # Where it is coming FROM, which is the meteorological convention and
        # also the one somebody means when they ask which way the wind is
        # blowing on a doorstep.
        if heading and not still:
            lines.append(f"Out of the {heading}.")
        try:
            gusts = float(data["wind_gusts_10m"])
            # Only when they are actually gusting. A gust figure a mile an
            # hour above the average is the same wind reported twice.
            if gusts - speed >= 5:
                lines.append(f"Gusting to {int(gusts)} mph.")
        except (KeyError, TypeError, ValueError):
            pass
        if not lines:
            lines.append("Barely a breath out there.")

        if still:
            said = "It's calm out"
        else:
            unit = "mile an hour" if int(speed) == 1 else "miles an hour"
            said = f"It's {int(speed)} {unit}"
            if described:
                said += f", {described}"
            if heading:
                said += f", out of the {heading}"

        self.client.answer("mdi.weather-windy", f"{int(speed)} mph wind", lines,
                           tint="#4f8f9d", speak=said + ".")

    def uv_update(self):
        """"what's the UV index", "do I need sunscreen"."""
        api = self._weather_api()
        if api is None:
            return
        data = api.get_air_quality()
        uv = (data or {}).get("uv_index")
        if uv is None:
            self._respond("I couldn't get the UV index right now.")
            return

        uv = float(uv)
        band = api.uv_band(uv)
        advice = {
            "low":       "Nothing to worry about.",
            "moderate":  "Cover up around midday.",
            "high":      "Sunscreen and shade at midday.",
            "very high": "Sunscreen, shade, and keep it short.",
            "extreme":   "Avoid the sun through the middle of the day.",
        }[band]

        tint = ("#3f8f5a" if band == "low" else
                "#a08a3a" if band == "moderate" else
                "#a86a3a" if band == "high" else
                "#a84a4a" if band == "very high" else "#7a3a7a")

        self.client.answer("mdi.weather-sunny-alert",
                           f"UV index {uv:.0f}",
                           [band.capitalize() + ".", advice], tint=tint,
                           speak=f"The UV index is {uv:.0f}, which is {band}. {advice}")

    def forecast_week(self):
        """"what's the forecast for the week", "what's the rest of the week look like"."""
        api = self._weather_api()
        if api is None:
            return
        days = api.get_daily_forecast(7)
        if not days:
            self._respond("I couldn't get the forecast right now.")
            return

        lines = []
        for index, day in enumerate(days):
            when = day.get("day")
            label = "Today" if index == 0 else \
                    "Tomorrow" if index == 1 else when.strftime("%A")
            sky, _ = api.sky_from_code(day.get("code"))
            high, low = day.get("high"), day.get("low")
            parts = []
            if high is not None and low is not None:
                parts.append(f"{int(high)}\u00b0 / {int(low)}\u00b0")
            if sky:
                parts.append(sky)
            chance = day.get("chance")
            if chance is not None and chance >= self.RAIN_LIKELY:
                # `precipitation_probability_max` covers everything that
                # falls, so calling it rain on a day the code says is snow
                # puts "80% rain" against a snow forecast.
                falls = "snow" if "snow" in sky.lower() else "rain"
                # Not repeated where the sky already said it. "Snow, 80%
                # snow" is the same word twice in six characters.
                parts.append(f"{int(chance)}%" if falls in sky.lower()
                             else f"{int(chance)}% {falls}")
            if parts:
                lines.append(f"{label}: " + ", ".join(parts))

        if not lines:
            self._respond("The forecast came back empty.")
            return

        # The first day drives the icon. A week has several skies in it and
        # the card can only wear one, so it wears the one nearest.
        _, glyph = api.sky_from_code(days[0].get("code"))

        # Spoken short. Seven days read aloud is forty seconds of numbers
        # nobody is still listening to by Thursday - the panel is showing
        # them, and that is the part worth having.
        highs = [d["high"] for d in days if d.get("high") is not None]
        said = "Here's the week"
        if highs:
            warmest = days[highs.index(max(highs))] if len(highs) == len(days) else None
            said += f". Highs from {int(min(highs))} to {int(max(highs))}"
            if warmest is not None:
                said += f", warmest on {warmest['day'].strftime('%A')}"
        wet = [d for d in days
               if (d.get("chance") or 0) >= self.RAIN_LIKELY]
        if wet:
            # "Rain" would be wrong on a week with snow in it, and the count
            # is over every day something falls.
            said += (f". Rain or snow likely on {len(wet)} "
                     + ("day" if len(wet) == 1 else "days"))

        self.client.answer(glyph, "This week", lines, tint="#3f7fbf",
                           speak=said + ".")

    def air_quality_update(self):
        """"how's the air quality", "is the air clean"."""
        api = self._weather_api()
        if api is None:
            return

        data = api.get_air_quality()
        if not data:
            self._respond("I couldn't get the air quality right now.")
            return

        aqi = data.get("us_aqi")
        if aqi is None:
            # Modelled almost everywhere, but not everywhere, and a panel
            # somewhere it is not should be told that rather than shown a
            # blank card.
            self._respond("There's no air quality reading for here.")
            return

        aqi = int(aqi)
        band, glyph = api.aqi_band(aqi)

        lines = [band.capitalize() + "."]
        for label, key, unit in (("PM2.5", "pm2_5", " \u00b5g/m\u00b3"),
                                 ("PM10", "pm10", " \u00b5g/m\u00b3"),
                                 ("Ozone", "ozone", " \u00b5g/m\u00b3")):
            value = data.get(key)
            if value is None:
                continue
            lines.append(f"{label}: {value:.0f}{unit}")

        # Green through red, so the card says it before the words do.
        tint = ("#3f8f5a" if aqi <= 50 else
                "#a08a3a" if aqi <= 100 else
                "#a86a3a" if aqi <= 150 else
                "#a84a4a" if aqi <= 200 else "#7a3a7a")

        self.client.answer(glyph, f"Air quality {aqi}", lines, tint=tint,
                           speak=f"The air quality index is {aqi}, which is {band}.")

    #What the sky is doing, in the order somebody would mention it. First hit
    #wins, so snow beats rain beats cloud.
    WEATHER_WORDS = (
        ("snowfall",     0,  "snowing"),
        ("showers",      0,  "showery"),
        ("rain",         0,  "raining"),
        ("cloud_cover", 80,  "overcast"),
        ("cloud_cover", 40,  "cloudy"),
    )

    def _weather_spoken(self, temperature, data: dict) -> str:
        """
        The weather as a sentence rather than a reading.

        "72 degrees." is two words: a speech model is finished with it before
        a room has noticed anybody is talking. The answer is not padded with
        silence - the words somebody misses are the ones at the front, so the
        front is given something worth missing.

        What is added is real: the sky, and how it feels if that differs from
        the number. A sentence made of filler is the same problem in more
        syllables.
        """
        sky = ""
        for key, above, word in self.WEATHER_WORDS:
            try:
                if float(data.get(key) or 0) > above:
                    sky = word
                    break
            except (TypeError, ValueError):
                continue
        if not sky:
            sky = "clear" if data.get("is_day", 1) else "clear out"

        said = f"It's {temperature} degrees and {sky}"

        # Only when it disagrees with the thermometer by enough to be worth
        # saying. "72 degrees, feels like 72" is noise.
        try:
            feels = int(float(data.get("apparent_temperature")))
            if abs(feels - int(float(temperature))) >= 3:
                said += f", though it feels more like {feels}"
        except (TypeError, ValueError):
            pass
        return said + "."

    ## WIKIPEDIA

    def _wikipedia(self):
        api = self.client.API.get("wikipedia")
        if api is None:
            self._respond("The encyclopedia isn't available.")
        return api

    def _why(self, api_key: str) -> str:
        """Why the last lookup on `api_key` came back empty."""
        api = self.client.API.get(api_key)
        if api is None:
            return "unavailable"
        return getattr(api, "last_failure", "") or "missing"

    @staticmethod
    def _not_found(subject: str, dictionary: str, encyclopedia: str) -> str:
        """
        One sentence naming what was asked and what came back from each.

        Spelled out because the two failures need different things done about
        them. "Not a word" means try a different word; "couldn't reach it"
        means check the network, and answering both with "I couldn't find it"
        sends somebody hunting for a spelling mistake that is not there.
        """
        offline = [name for name, why in (("the dictionary", dictionary),
                                          ("Wikipedia", encyclopedia))
                   if why in ("offline", "unavailable")]
        if len(offline) == 2:
            return (f"I couldn't reach the dictionary or Wikipedia, so I "
                    f"can't look up {subject} right now.")
        if offline:
            other = "Wikipedia" if offline[0] == "the dictionary" else "the dictionary"
            return (f"{subject} isn't in {other}, and I couldn't reach "
                    f"{offline[0]} to check there.")
        return (f"{subject} isn't in the dictionary, and Wikipedia doesn't "
                f"have an article on it either.")

    def _while_speaking(self):
        """
        A `hold_open` that keeps an answer up until the panel stops reading it.

        No check for quiet mode, and none needed: with replies turned off the
        panel never speaks, so this is never true and the answer times out
        the ordinary way. A second condition asking the same question through
        a setting would be one more thing to keep in step with the first.
        """
        def speaking():
            try:
                tts = getattr(self.client, "TTS", None)
                return bool(tts is not None and tts.is_speaking())
            except Exception:
                return False
        return speaking

    def _miss(self, subject: str) -> None:
        """Say that the encyclopedia had nothing, for anything listening."""
        try:
            self.client.trigger_on_call_event_iteration(
                "on_wikipedia_lookup_failed", subject)
        except Exception as e:
            self.client.log("debug", f"[CoreSkills] Miss event failed: {e}")

    def _open_page(self, url: str):
        """A callback that puts a URL on the built-in browser page."""
        def go():
            self.client.goto("#webpage", data={"url": url}, override=True)
        return go

    def looks_like(self, subject: str = "", phrase: str = ""):
        """"what does an axolotl look like", "show me the eiffel tower"."""
        api = self._wikipedia()
        if api is None:
            return

        subject = (subject or "").strip()
        if not subject and phrase:
            subject = wikipedia_subject(phrase)
        if not subject:
            raise SkillDeclined("no subject in the phrase")

        found = api.look_up(subject)
        if not found:
            self._miss(subject)
            raise SkillDeclined(
                f"Wikipedia had nothing for {subject!r} "
                f"({self._why('wikipedia')})")

        if not title_matches(subject, found["title"]):
            self._miss(subject)
            raise SkillDeclined(
                f"asked for {subject!r}, Wikipedia offered "
                f"{found['title']!r}")

        image = api.picture(found)

        # The caption if the article wrote one, its short description if not,
        # and nothing at all rather than a made-up sentence about a picture
        # nobody has described.
        caption = found.get("caption") or found.get("description") or ""

        try:
            self.client.CONTEXT.note(subject=found["title"], url=found["url"])
        except Exception:
            pass

        if image:
            lines, spoken = [], f"Here's what {found['title']} looks like."
        else:
            # No picture is not no answer. The article was found, and what it
            # says about the thing is worth more than an apology about a
            # photograph - so the question gets answered in words and the
            # missing picture is mentioned rather than being the whole reply.
            lines = [api.first_blob(found["extract"], sentences=2)
                     or caption or "No description available."]
            spoken = (f"I don't have a picture of {found['title']}, "
                      f"but here's what it is.")

        self.client.answer(
            "mdi.image-search-outline" if image else "mdi.image-off-outline",
            found["title"], lines,
            tint="#6a8ab0", image=image, caption=caption if image else "",
            action=("Read more", self._open_page(found["url"]))
                   if found["url"] else None,
            # Held while it is being read. A Wikipedia summary takes longer
            # to speak than the panel's 30 seconds, so the card used to
            # vanish mid-sentence.
            hold_open=self._while_speaking(),
            speak=spoken)

    def wiki_search(self, subject: str = "", phrase: str = ""):
        """"search for the eiffel tower", "look up mount fuji on wikipedia"."""
        api = self._wikipedia()
        if api is None:
            return

        subject = (subject or "").strip()
        if not subject and phrase:
            subject = wikipedia_subject(phrase)
        if not subject:
            # Declined, not asked. Having nothing to look up means the words
            # matched and the question did not - which is a decline, and the
            # phrase still has somewhere to go. Asking "what should I look
            # up?" ends the turn on behalf of every skill that was never
            # tried, and it does it while holding a phrase that names the
            # thing perfectly well.
            raise SkillDeclined("no subject in the phrase")

        found = api.look_up(subject)
        if not found or not found["extract"]:
            # Declined rather than answered. Nothing came back, and the
            # phrase still has somewhere to go: another skill, or the
            # fallback, either of which may know what this was. Saying
            # "Wikipedia doesn't have an article on that" ends the turn on
            # behalf of everything else that was never asked.
            self._miss(subject)
            raise SkillDeclined(
                f"Wikipedia had nothing for {subject!r} "
                f"({self._why('wikipedia')})")

        if not title_matches(subject, found["title"]):
            # The article is about something else that shares a word. This
            # is the case the whole check exists for: Wikipedia answers
            # every query with its nearest article, and the nearest article
            # to a question that was not an encyclopedia question is usually
            # a person with the right surname.
            self._miss(subject)
            raise SkillDeclined(
                f"asked for {subject!r}, Wikipedia offered "
                f"{found['title']!r}")

        if found["type"] == "disambiguation":
            # The extract of a disambiguation page reads like an answer and is
            # not one - "Mercury may refer to:" followed by nothing. Saying so
            # is better than reading the preamble of a list out loud.
            self._respond(f"There's more than one {found['title']}. "
                          f"Can you be more specific?")
            return

        # Two paragraphs, from the fuller intro rather than the summary's
        # lead. The first paragraph says what kind of thing something is and
        # the second says what is worth knowing about it - stopping at the
        # first is the half that reads like a dictionary entry.
        blob = api.first_paragraphs(found.get("intro") or found["extract"])
        image = api.picture(found)

        try:
            self.client.CONTEXT.note(subject=found["title"], url=found["url"])
        except Exception:
            pass

        # One line per paragraph. Passed as a single string the panel draws
        # it as one block with a blank line inside a label, which wraps as a
        # wall rather than as two paragraphs.
        lines = [part.strip() for part in blob.split("\n\n") if part.strip()]

        self.client.answer(
            "mdi.book-search-outline", found["title"], lines,
            tint="#6a8ab0", image=image,
            caption=found.get("description") or "",
            action=("Read on Wikipedia", self._open_page(found["url"]))
                   if found["url"] else None,
            # Held while it is being read. A Wikipedia summary takes longer
            # to speak than the panel's 30 seconds, so the card used to
            # vanish mid-sentence.
            hold_open=self._while_speaking(),
            # All of it, not the first sentence. Somebody who asked to be
            # told about something wants to be told about it, and cutting the
            # reply short to save them time answers a question they did not
            # ask. Saying "stop" ends it, and so does pressing the button or
            # tapping the card - the way out is cheap, so the default can be
            # generous.
            speak=blob)

    def on_dictionary_missed(self, word=None):
        """
        A word the dictionary did not have. Try the encyclopedia.

        The two cover different ground: a dictionary has words and an
        encyclopedia has things, so "petrichor" is in one and "Xochimilco" is
        in the other, and being told neither exists is wrong about half of
        them.

        This is where the apology ends up, because this is the last place
        that looks - and it says WHICH of the two came up empty. Both used to
        answer with the same sentence, so a word that is simply not a word
        and a panel that cannot reach the internet were indistinguishable
        from the room.
        """
        word = str(word or "").strip()
        if not word:
            return

        api = self.client.API.get("wikipedia")
        why_dictionary = self._why("dictionary")
        if api is None:
            self._respond(self._not_found(word, why_dictionary, "unavailable"))
            return

        found = api.look_up(word)
        if found and found["type"] == "disambiguation":
            self._respond(f"There's more than one {found['title']}. "
                          f"Can you be more specific?")
            return
        if not found or not found["extract"]:
            self._respond(self._not_found(word, why_dictionary,
                                          self._why("wikipedia")))
            return

        blob = api.first_paragraphs(found.get("intro") or found["extract"],
                                    count=1)
        lines = [part.strip() for part in blob.split("\n\n") if part.strip()]

        try:
            self.client.CONTEXT.note(subject=found["title"], url=found["url"])
        except Exception:
            pass

        self.client.answer(
            "mdi.book-search-outline", found["title"], lines,
            tint="#6a8ab0", image=api.picture(found),
            caption=found.get("description") or "",
            action=("Read on Wikipedia", self._open_page(found["url"]))
                   if found["url"] else None,
            # Held while it is being read. A Wikipedia summary takes longer
            # to speak than the panel's 30 seconds, so the card used to
            # vanish mid-sentence.
            hold_open=self._while_speaking(),
            # Said, because it is not the answer that was asked for. Somebody
            # who asked what a word means and gets an encyclopedia entry
            # should know which of the two they are being read.
            speak=f"That's not in the dictionary, but Wikipedia has "
                  f"{found['title']}. " + (lines[0] if lines else ""))

    ## SUN AND MOON

    def _astronomy(self):
        """
        The astronomy library, and where the panel is.

        Through `client.public` rather than by importing it. `plugin.toml`
        makes `astronomy` a dependency, so an import would work - but the
        library is a library on purpose, and a skill reaching past the
        registry into its module is one more thing to keep in step when it
        moves.
        """
        try:
            if not self.client.public.has("astronomy"):
                return None, 0.0, 0.0
            api = self.client.public.astronomy
        except Exception:
            return None, 0.0, 0.0

        # The coordinates belong to the weather plugin, which is where they
        # are configured. Asking it beats keeping a second copy that can
        # disagree with the one somebody actually edits.
        latitude = longitude = 0.0
        try:
            weather = self.client.API.get("weather")
            if weather is not None:
                latitude, longitude = weather.coordinates()
        except Exception:
            pass
        return api, latitude, longitude

    def sun_times(self, phrase: str = ""):
        """"when is sunset", "what time does the sun come up"."""
        api, latitude, longitude = self._astronomy()
        if api is None:
            self._respond("The astronomy plugin isn't loaded.")
            return
        if not latitude and not longitude:
            # Sunrise at 0,0 is a real time in the Gulf of Guinea, which is
            # a worse answer than none: it looks right and is hours out.
            self._respond("I don't know where this panel is. Set the "
                          "location in weather settings.")
            return

        rise, setting = api["sun_times"](latitude, longitude)
        if rise is None:
            # Inside a polar circle the sun may not rise or set at all that
            # day, and the library says so by returning nothing.
            self._respond("The sun doesn't rise or set here today.")
            return

        asked_rise = any(word in (phrase or "").lower() for word in
                         ("sunrise", "sun rise", "come up", "comes up",
                          "get light", "gets light", "dawn", "sunup"))
        asked_set = any(word in (phrase or "").lower() for word in
                        ("sunset", "sun set", "go down", "goes down",
                         "get dark", "gets dark", "dusk", "sundown"))

        now = datetime.now().astimezone()
        # Neither named, or both: whichever is next. "How long is daylight"
        # and "when does the sun move" are the same want - the next thing it
        # does.
        if asked_rise == asked_set:
            name, moment, seconds = api["next_sun_event"](latitude, longitude)
            if name is None:
                self._respond("I couldn't work out the sun times.")
                return
            heading = f"{name.capitalize()} at {_clock(moment)}"
            # Words out loud, the compact form on the card. "3h 33m" is a
            # label; read aloud it is "three em thirty three em".
            said = (f"{name.capitalize()} is at {_clock(moment)}, "
                    f"{_spoken_wait(seconds)} from now")
        else:
            moment = rise if asked_rise else setting
            name = "Sunrise" if asked_rise else "Sunset"
            heading = f"{name} at {_clock(moment)}"
            if moment > now:
                said = (f"{name} is at {_clock(moment)}, "
                        f"{_spoken_wait((moment - now).total_seconds())} from now")
            else:
                # Already happened. Saying "in -3h" is what a naive
                # subtraction does, and it is worse than saying nothing.
                said = f"{name} was at {_clock(moment)} today"

        lines = [f"Sunrise {_clock(rise)}", f"Sunset {_clock(setting)}"]
        length = int((setting - rise).total_seconds())
        hours, minutes = divmod(length // 60, 60)
        lines.append(f"{hours}h {minutes}m of daylight")

        glyph = ("mdi.weather-sunset-up" if heading.lower().startswith("sunrise")
                 else "mdi.weather-sunset-down")
        self.client.answer(glyph, heading, lines, tint="#c8873a",
                           speak=said + ".")

    def moon_phase(self):
        """"what phase is the moon", "is it a full moon"."""
        api, _, _ = self._astronomy()
        if api is None:
            self._respond("The astronomy plugin isn't loaded.")
            return

        name = api["moon_name"]()
        lit = api["moon_illumination"]()
        waxing = api["moon_waxing"]()
        age = api["moon_age"]()

        lines = [f"{lit * 100:.0f}% lit",
                 "Waxing" if waxing else "Waning",
                 f"{age:.1f} days into the cycle"]

        glyph = {
            "New moon":        "mdi.moon-new",
            "Waxing crescent": "mdi.moon-waxing-crescent",
            "First quarter":   "mdi.moon-first-quarter",
            "Waxing gibbous":  "mdi.moon-waxing-gibbous",
            "Full moon":       "mdi.moon-full",
            "Waning gibbous":  "mdi.moon-waning-gibbous",
            "Last quarter":    "mdi.moon-last-quarter",
            "Waning crescent": "mdi.moon-waning-crescent",
        }.get(name, "mdi.moon-waning-crescent")

        self.client.answer(glyph, name, lines, tint="#5a5a8a",
                           speak=f"It's a {name.lower()}, {lit * 100:.0f} percent lit.")

    ## UNITS

    def convert_units(self, phrase: str = ""):
        """"how many cups in a litre", "convert 5 miles to km", "350 F in C"."""
        parsed = units.parse(phrase or "")
        if not parsed:
            self._respond("I didn't catch what to convert. Try 'how many "
                          "cups in a litre'.")
            return

        amount, source_word, target_word = parsed
        pair = units.resolve(source_word, target_word)
        if pair is None:
            # Both words are units and neither is nonsense - they just do not
            # measure the same thing. Saying which is the difference between
            # an answer and a shrug.
            self._respond(f"I can't convert {source_word} into "
                          f"{target_word} - they measure different things.")
            return

        source, target = pair
        try:
            self.client.CONTEXT.note(amount=amount, source=source[2],
                                     target=target[2])
        except Exception:
            pass
        try:
            result = units.convert(amount, source, target)
        except Exception as e:
            self.client.log("warning", f"[CoreSkills] Conversion failed: {e}")
            self._respond("I couldn't work that out.")
            return

        left = f"{units.pretty(amount)} {units.name(source, amount)}"
        right = f"{units.pretty(result)} {units.name(target, result)}"

        # The rate underneath, when the question was about a quantity. "5
        # miles is 8.05 km" is the answer; "1 mile is 1.61 km" is the thing
        # somebody can use again without asking.
        lines = [f"{left} = {right}"]
        if abs(amount - 1.0) > 1e-9:
            try:
                unit_rate = units.convert(1.0, source, target)
                # Not for temperature. One degree Fahrenheit is -17 Celsius,
                # which is true and is not a conversion rate - the scales
                # have different zeros, so there is no rate to give.
                if source[0] != "temperature":
                    lines.append(f"1 {units.name(source, 1.0)} = "
                                 f"{units.pretty(unit_rate)} "
                                 f"{units.name(target, unit_rate)}")
            except Exception:
                pass

        # First letter only. `capitalize()` lowercases everything after it,
        # which turns "degrees Celsius" into "degrees celsius" - a proper
        # noun quietly demoted by a formatting call.
        heading = right[0].upper() + right[1:] if right else right
        self.client.answer("mdi.swap-horizontal", heading, lines,
                           tint="#4f9d8a", speak=f"{left} is {right}.")

    ## DICTIONARY

    #How many senses a panel shows. Past this it stops being an answer and
    #starts being a page of a dictionary, and the card grows to hold it.
    MAX_SENSES = 3
    MAX_SYNONYMS = 8

    def _look_up(self, word: str):
        """(cleaned word, entry) with the apology already given on failure."""
        api = self.client.API.get("dictionary")
        if api is None:
            self._respond("The dictionary isn't available.")
            return "", None

        cleaned = api.clean(word)
        if not cleaned:
            # The skill matched but caught no word - "what does that mean"
            # with nothing before it. Asking again is the answer; guessing is
            # not.
            self._respond("Which word?")
            return "", None

        entry = api.look_up(cleaned)
        if entry is None:
            # Announced rather than apologised for. Something else may know
            # the word - the encyclopedia usually does - and this plugin
            # subscribes to its own event to try exactly that. The apology
            # belongs to whoever runs out of places to look, which is not
            # here.
            try:
                self.client.trigger_on_call_event_iteration(
                    "on_dictionary_lookup_failed", cleaned)
            except Exception as e:
                self.client.log("warning",
                                f"[CoreSkills] Lookup event failed: {e}")
                self._respond(self._not_found(cleaned, self._why("dictionary"),
                                              "missing"))
            return cleaned, None

        # The word itself, kept beside the turn. The answer says what it
        # means and never repeats it, so "where does that come from" has
        # nothing to work from in the prose alone.
        try:
            self.client.CONTEXT.note(word=entry.get("word") or cleaned)
        except Exception:
            pass
        return cleaned, entry

    def define_word(self, word: str = "", phrase: str = ""):
        """"what does serendipity mean", "define petrichor"."""
        api = self.client.API.get("dictionary")
        if api is not None and not word and phrase:
            # No leader in the phrase - "what does X mean" - so the word is
            # in the middle and the payload anchors never fired.
            word = api.word_from_phrase(phrase)
        cleaned, entry = self._look_up(word)
        if entry is None:
            return

        lines = []
        for sense in entry["senses"][:self.MAX_SENSES]:
            part = sense["part"]
            lines.append(f"({part}) {sense['text']}" if part else sense["text"])

        # The first sense only, spoken. A word with four meanings read aloud
        # is a paragraph, and whoever asked wanted to know roughly what it
        # means - the panel is holding the rest of it.
        first = entry["senses"][0] if entry["senses"] else None
        if first:
            # Lowered, because the definition is a sentence in its own right
            # and it is being spoken as a clause in somebody else's. "X means
            # A combination of events" is read with the capital audible.
            text = first["text"]
            text = text[0].lower() + text[1:] if text else text
            said = f"{entry['word'] or cleaned} means {text}"
        else:
            said = f"I have {entry['word'] or cleaned}, but no definition for it"

        # Shown but not said. A phonetic spelling read by a speech model is
        # noise, and out loud the pronunciation is already being demonstrated.
        heading = entry["word"] or cleaned
        if entry["phonetic"]:
            lines.append(entry["phonetic"])

        self.client.answer("mdi.book-open-variant", heading.capitalize(),
                           lines, tint="#7a6ab0", speak=said)

    def word_synonyms(self, word: str = ""):
        """"what are other words for happy", "synonyms for tired"."""
        cleaned, entry = self._look_up(word)
        if entry is None:
            return

        found = entry["synonyms"][:self.MAX_SYNONYMS]
        if not found:
            # A real answer rather than a failure. Plenty of words have an
            # entry and no synonyms in it, and "I couldn't find happy" would
            # be false.
            self._respond(f"I don't have any other words for {cleaned}.")
            return

        heading = entry["word"] or cleaned
        lines = [", ".join(found)]
        if entry["antonyms"]:
            lines.append("Opposites: "
                         + ", ".join(entry["antonyms"][:self.MAX_SYNONYMS]))

        # Four out loud, the rest on the panel. A spoken list stops being a
        # list somewhere around five and becomes a noise that ends.
        spoken = found[:4]
        if len(spoken) == 1:
            said = f"Another word for {heading} is {spoken[0]}"
        else:
            said = (f"Other words for {heading}: "
                    + ", ".join(spoken[:-1]) + f", and {spoken[-1]}")

        self.client.answer("mdi.text-search", f"Other words for {heading}",
                           lines, tint="#7a6ab0", speak=said + ".")

    ## TIMERS

    def start_timer(self, time: str = None, name: str = None,
                    phrase: str = ""):
        """
        "set a timer for 10 minutes", "make a timer called Eggs for 5 minutes".

        `time` arrives as spoken text - "10 minutes", "1 hour" - because a
        transcript is untrusted and normalisation converts most spoken numbers
        but is not a guarantee. Parsed here rather than trusted.

        The whole phrase is taken as well, and only used to catch a qualifier
        the capture threw away. `extract_args` trims leading stopwords off a
        span, so "half an hour" comes back as "hour" - a thirty minute timer
        set for an hour, which is worse than not understanding at all because
        it looks like it worked.
        """
        seconds = _spoken_duration(time)
        if phrase:
            whole = _spoken_duration(phrase)
            # Only where a qualifier is present and the two disagree. The
            # captured span is the more precise reading everywhere else -
            # "call it 10 minute wash" is a name, not a duration - so the
            # phrase is a correction rather than the default.
            if whole and abs(whole - seconds) > 1 and any(
                    word in phrase.lower() for word in ("half", "quarter")):
                self.client.log("debug",
                                f"[CoreSkills] '{time}' lost a qualifier; "
                                f"reading {whole:.0f}s from the phrase.")
                seconds = whole
        if not seconds:
            self._respond("I did not catch how long for. Try 'set a timer for "
                          "five minutes'.")
            return

        if not self.client.public.has("timers"):
            # Provided by corewidgetsbundle. Disable that and timers go with
            # it, so this says so rather than failing silently.
            self._respond("Timers need the Core Widgets plugin, which is not "
                          "loaded.")
            return

        timer = self.client.public.timers["start"](
            seconds, name=_clean_label(name))
        if timer is None:
            self._respond("I could not start that timer.")
            return

        label = f" for {name}" if name else ""
        self._respond(f"{self._describe(seconds)}{label}, starting now.")

    def cancel_timers(self, time: str = None, name: str = None):
        """
        "cancel my timers", "cancel the eggs timer", "cancel the 5 minute timer".

        With neither argument this means all of them. With either, it means the
        ones that match - and saying so when nothing does, rather than silently
        cancelling everything, which is the failure that would actually cost
        somebody their dinner.
        """
        if not self.client.public.has("timers"):
            self._respond("There are no timers running.")
            return

        api = self.client.public.timers
        running = api["running"]()
        if not running:
            self._respond("There are no timers running.")
            return

        seconds = _spoken_duration(time) if time else 0
        wanted = _clean_label(name)

        # Nothing to narrow by: all of them.
        if not seconds and not wanted:
            stopped = api["cancel_all"]()
            self._respond(f"Stopped {stopped} timer" + ("s." if stopped != 1 else "."))
            return

        matched = api["cancel_matching"](name=wanted, seconds=seconds)

        if not matched:
            self._respond(f"I could not find that timer. {self._running_summary(running)}")
            return

        if len(matched) == 1:
            timer = matched[0]
            if timer.name:
                self._respond(f"Stopped the {timer.name} timer.")
            else:
                # "the 30 minutes timer" reads wrong - describe() gives a
                # noun phrase, not an adjective, so it goes after the noun.
                self._respond(
                    f"Stopped the timer set for "
                    f"{self._describe(timer.duration)}.")
            return

        self._respond(f"Stopped {len(matched)} timers.")

    def _running_summary(self, running: list) -> str:
        """What is actually on, for when a request matched nothing."""
        if not running:
            return "Nothing is running."
        names = []
        for timer in running:
            names.append(timer.name if timer.name
                         else self._describe(timer.duration))
        if len(names) == 1:
            return f"The only one running is {names[0]}."
        return "Running: " + ", ".join(names) + "."

    def check_timers(self):
        if not self.client.public.has("timers"):
            self._respond("There are no timers running.")
            return
        running = self.client.public.timers["running"]()
        if not running:
            self._respond("There are no timers running.")
            return

        lines = [f"{t.label()}: {self._describe(t.remaining())} left"
                 for t in running]
        spoken = "; ".join(lines)
        self.client.answer("mdi.timer-outline",
                           f"{len(running)} timer" + ("s" if len(running) != 1 else ""),
                           lines, tint="#3f7fbf", speak=spoken)

    ## ALARMS

    def _alarms(self):
        """The alarm service, or None if whoever owns it is not loaded."""
        if not self.client.public.has("alarms"):
            return None
        return self.client.public.alarms

    def _alarm_when(self, time_text, after_text, day, part) -> float:
        """
        The epoch an alarm was asked for, however it was asked for.

        Relative beats absolute, always. "10 minutes from now" contains a
        number that also reads as a clock time, so both arguments match - and
        the one somebody said is the one with a unit on it.
        """
        seconds = _spoken_duration(after_text) if after_text else 0
        if seconds:
            return _time.time() + seconds
        return _alarm_epoch(time_text or "", day or "", part or "")

    def set_alarm(self, time: str = None, after: str = None, day: str = None,
                  part: str = None, repeat: str = None, name: str = None):
        """
        "set an alarm at 4:40 PM", "set an alarm 10 minutes from now".

        Everything ambiguous is resolved in `_alarm_epoch` - which 8 o'clock,
        which day, and what a bare hour means with a day named on it.
        """
        api = self._alarms()
        if api is None:
            self._respond("Alarms are not available right now.")
            return

        when = self._alarm_when(time, after, day, part)
        if not when:
            self._respond("I did not catch what time. Try 'set an alarm for "
                          "seven in the morning'.")
            return

        alarm = api["schedule"](when, name=_clean_label(name),
                                repeats=bool(repeat))
        if alarm is None:
            # The only way schedule() refuses: a time already gone. Everything
            # else has had a day rolled onto it by now.
            self._respond("That time has already passed.")
            return

        said = api["describe"](alarm.when)
        every = " every day" if alarm.repeats else ""
        called = f", called {alarm.name}" if alarm.name else ""
        lines = [f"Set for {said}."]
        if alarm.repeats:
            lines.append("Repeats daily.")
        self.client.answer("mdi.alarm",
                           f"Alarm {api['clock_text'](alarm.when)}",
                           lines, tint="#c0603f",
                           speak=f"Alarm set for {said}{every}{called}.")

    def cancel_alarms(self, time: str = None, after: str = None,
                      day: str = None, part: str = None, repeat: str = None,
                      name: str = None):
        """
        "cancel my alarms", "cancel the alarm at 4:40 PM",
        "cancel the alarm 10 minutes from now".

        The relative form is how it was ASKED for rather than what it reads
        as now, and both find the same one: "10 minutes from now" resolves to
        a clock time, and the match is against that to the nearest minute.
        """
        api = self._alarms()
        if api is None:
            self._respond("Alarms are not available right now.")
            return

        scheduled = api["scheduled"]()
        if not scheduled:
            self._respond("There are no alarms set.")
            return

        when = self._alarm_when(time, after, day, part)
        wanted = _clean_label(name)
        # "the daily alarm" narrows to the repeating ones. On its own that is
        # usually enough to say which; with a time as well it is both.
        only_repeating = True if repeat else None

        if not when and not wanted and only_repeating is None:
            stopped = api["cancel_all"]()
            self._respond(f"Cleared {stopped} alarm"
                          + ("s." if stopped != 1 else "."))
            return

        matched = api["cancel_matching"](when=when, name=wanted,
                                         repeats=only_repeating)
        if not matched:
            self._respond("I could not find that alarm. "
                          + self._alarm_summary(scheduled))
            return
        if len(matched) == 1:
            alarm = matched[0]
            said = (alarm.name if alarm.name
                    else f"the {api['clock_text'](alarm.when)} alarm")
            if alarm.repeats:
                said = f"the daily {said}" if not alarm.name else said
            self._respond(f"Cancelled {said}.")
            return
        self._respond(f"Cancelled {len(matched)} alarms.")

    def check_alarms(self):
        """What is set, soonest first."""
        api = self._alarms()
        if api is None:
            self._respond("Alarms are not available right now.")
            return

        scheduled = api["scheduled"]()
        if not scheduled:
            self._respond("There are no alarms set.")
            return

        lines = []
        for alarm in scheduled:
            said = api["describe"](alarm.when)
            lines.append(f"{alarm.name}: {said}" if alarm.name else said)
        self.client.answer(
            "mdi.alarm",
            f"{len(scheduled)} alarm" + ("s" if len(scheduled) != 1 else ""),
            lines, tint="#c0603f", speak=self._alarm_summary(scheduled))

    def _alarm_summary(self, scheduled: list) -> str:
        """What is set, for when a request matched nothing."""
        api = self._alarms()
        if api is None or not scheduled:
            return "There are no alarms set."
        said = [(f"{a.name} at {api['describe'](a.when)}" if a.name
                 else api["describe"](a.when)) for a in scheduled]
        if len(said) == 1:
            return f"The only one set is {said[0]}."
        return "Set: " + ", ".join(said) + "."

    ## HELPERS

    ## -- quiet

    #Skill handlers run on the assistant's worker.
    #
    #Anything below that reaches Qt - a page rebuild, a settings apply that
    #redraws, a panel - has to be handed over. Doing it inline produced
    #"Timers cannot be stopped from another thread" and a page torn down
    #underneath its own widgets.
    def quiet_on(self):
        self.client.call_on_ui(lambda: self.client.set_do_not_disturb(True))
        # Said before it takes effect, or the confirmation is the first thing
        # the mode silences.
        self._respond("Do not disturb is on.")

    def quiet_off(self):
        # Spoken FIRST for the opposite reason: while it is still on, this
        # would not be heard.
        self.client.call_on_ui(lambda: self.client.set_do_not_disturb(False))
        self._respond("Do not disturb is off.")

    def mute_on(self):
        self._respond("Going quiet.")
        self.client.call_on_ui(lambda: self.client.set_sounds_muted(True))

    def mute_off(self):
        self.client.call_on_ui(lambda: self.client.set_sounds_muted(False))
        self._respond("Sounds are back on.")

    def mic_mute_on(self):
        """
        Mute the microphone itself.

        Said BEFORE it goes, because afterwards nothing is listening and the
        reply is the last thing this can do about it. The quick settings
        button and the tile are the way back - by voice there is not one, and
        saying so is the difference between muted and broken.
        """
        if not self.client.mic_mute_available():
            self._respond("I cannot reach the microphone controls here.")
            return
        self._respond("Muting the microphone. Use the panel to turn it back on.")
        self.client.set_mic_muted(True)

    def mic_mute_off(self):
        if not self.client.mic_mute_available():
            self._respond("I cannot reach the microphone controls here.")
            return
        self.client.set_mic_muted(False)
        self._respond("The microphone is back on.")

    ## -- getting about

    def go_to_page(self, page_name: str = ""):
        """
        Open a registered page by name.

        Matched against what the pages call themselves rather than a list kept
        here, so a plugin adding a page is reachable without this knowing it
        exists.
        """
        wanted = _clean_words(page_name, drop=PAGE_VERBS)
        if not wanted:
            self._respond("Which page?")
            return

        best, score = None, 0.0
        for label, target in self._page_candidates():
            hit = _overlap(wanted, label)
            if hit > score:
                best, score = target, hit

        # A page nobody meant is worse than admitting the miss: this navigates,
        # so a wrong guess takes the screen away from whatever was on it.
        if best is None or score < 0.5:
            self._respond(f"I do not have a page called {wanted}.")
            return

        entry_key, coord = best
        if coord is None:
            self.client.call_on_ui(
                lambda target=entry_key: self.client.goto(target, override=True))
            return

        def travel(target=entry_key, where=coord):
            self.client.goto(target, override=True)
            instance = getattr(self.client.PAGES.get_entry(target),
                               "instance", None)
            jump = getattr(instance, "jump_to_coord", None)
            if callable(jump):
                jump(tuple(where))
        self.client.call_on_ui(travel)

    def _page_candidates(self) -> list:
        """
        Every page somebody could ask for: the registered ones, and the
        sub-pages inside them.

        A sub-page is reached through its parent rather than by name, so it is
        not in `PAGES` and a search of that alone cannot find it - "go to
        calendar" matched the skill and then reported no such page, because
        the calendar is a sub-page of the home page.

        Found by shape rather than by naming a plugin: any page instance
        offering `sub_page_dict` and `jump_to_coord` joins in, so this does
        not have to know which plugin owns the framework.
        """
        found = []
        for entry_key in self.client.PAGES.keys():
            label = str(entry_key).lstrip("#").replace("_", " ")
            label = label.replace("cwb ", "").replace(" page", "")
            found.append((label, (entry_key, None)))

            instance = getattr(self.client.PAGES.get_entry(entry_key),
                               "instance", None)
            subs = getattr(instance, "sub_page_dict", None)
            if not isinstance(subs, dict) \
                    or not callable(getattr(instance, "jump_to_coord", None)):
                continue
            for name, sub in subs.items():
                coord = getattr(sub, "coord", None)
                if coord is None:
                    continue
                spoken = str(getattr(sub, "NAME", "") or name)
                spoken = spoken.replace("_", " ").lower()
                found.append((spoken, (entry_key, coord)))
        return found

    def open_bookmark(self, wanted: str = ""):
        """
        Open a saved page by roughly its name.

        Anchored on the verb and matched loosely, because a bookmark's title
        comes from the page rather than from whoever saved it - "Advanced
        Search - Scryfall" is a name somebody would say as "scryfall".
        """
        wanted = _clean_words(wanted, drop=("open", "go", "to", "goto",
                                            "launch", "bookmark", "my", "the",
                                            "for", "bring", "up"))
        if not wanted:
            self._respond("Which bookmark?")
            return

        marks = []
        try:
            marks = self.client.BOOKMARKS.all()
        except Exception:
            marks = []
        if not marks:
            self._respond("There are no bookmarks saved.")
            return

        best, score = None, 0.0
        for mark in marks:
            hit = max(_overlap(wanted, mark.label), _overlap(wanted, mark.host))
            if hit > score:
                best, score = mark, hit

        if best is None or score < 0.4:
            self._respond(f"I have no bookmark like {wanted}.")
            return

        from urllib.parse import urlparse
        base = ""
        try:
            parts = urlparse(best.url)
            if parts.scheme and parts.netloc:
                base = f"{parts.scheme}://{parts.netloc}"
        except Exception:
            base = ""
        self.client.call_on_ui(lambda: self.client.goto("#webpage", data={
            "url": best.url, "lock_base": base, "lock_address": True,
        }, override=True))

    def _respond(self, text: str):
        # Speak when possible, otherwise show it - a skill that silently does
        # nothing because TTS is unconfigured is indistinguishable from a
        # broken one.
        if not self.client.say(text, thread=False):
            self.client.simple_notify("assistant", "Assistant", text)
