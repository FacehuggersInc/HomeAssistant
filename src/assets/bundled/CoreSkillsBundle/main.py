from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt

from src.plugin.template import Plugin
from src.assistant.skill import Skill
from src.styling import make_font, SIZES, set_style

from .voice_bar import VoiceBar


# Words that may sit immediately before "timer" without being its name.
# Shared by set-timer and cancel-timer: two copies would drift, and the whole
# point is that "a 5 minute timer" is not a timer called "minute".
TIMER_NAME_STOPWORDS = [
    "timer", "timers",
    "second", "seconds", "minute", "minutes", "hour", "hours", "day", "days",
    "all", "every", "running", "remaining", "new", "another", "other",
]


class CoreSkills(Plugin):

    ## CORE

    def __init__(self):
        self.skills = []
        self.voice_bar = None
        self._last_state = (None, None)

    def load(self, carryover=None):
        self.load_skills()
        self.client.subscribe_to_event("on_update", self.update_assistant)
        self.client.subscribe_to_event("on_assistant_transcribed", self.on_transcribed)
        self.client.subscribe_to_event("on_heard_assistant", self.on_heard)
        self.client.subscribe_to_event("on_woke_assistant", self.on_woke)
        self.client.subscribe_to_event("on_assistant_cancelled", self.on_cancelled)

    def built(self):
        self.load_voice_bar()

    def unload(self, carryover=None):
        self.client.unsubscribe_from_event("on_update", self.update_assistant)
        self.client.unsubscribe_from_event("on_assistant_transcribed", self.on_transcribed)
        self.client.unsubscribe_from_event("on_heard_assistant", self.on_heard)
        self.client.unsubscribe_from_event("on_woke_assistant", self.on_woke)
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
            if not self.client.SETTINGS.assistant.voice_bar.value:
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

    def on_transcribed(self, event):
        # Whisper only emits finished transcripts - it transcribes a completed
        # speech window, so there is no partial stream to show while talking.
        # The meter covers "hearing something"; this is "heard this".
        if self.voice_bar is None:
            return
        text = event if isinstance(event, str) else str(event or "")
        self.client.call_on_ui(lambda: self.voice_bar.show_heard(text))

    def on_cancelled(self, event=None):
        if self.voice_bar is None:
            return
        self.client.call_on_ui(lambda: self.voice_bar.show_cancelled())

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

        self.skills = [
            Skill(
                wake_word=wake, skill_key="tell-relative-date", plugin_key=key,
                examples=[
                    "what is today", "what was yesterday",
                    "what is the day before today", "what is tomorrow",
                    "what is the day after today", "whats today", "what today",
                    "whats tomorrow", "what tomorrow", "what before today",
                    "whats before today", "what after today", "whats after today",
                ],
                arguments={
                    "given_date": [
                        [{"IS_ALPHA": True, "OP": "{2,3}"}],
                        [{"LOWER": {"IN": ["is", "was"]}}, {"IS_ALPHA": True, "OP": "{1,4}"}],
                    ]
                },
                func=self.tell_relative_date,
            ),
            Skill(
                wake_word=wake, skill_key="notifications-open", plugin_key=key,
                examples=[
                    "open my notifications", "show me my notifications",
                    "check notifications", "display notifications",
                    "read my notifications", "bring up notifications",
                    "what are my notifications", "open notifications",
                ],
                func=self.open_notifications,
            ),
            Skill(
                wake_word=wake, skill_key="notifications-empty", plugin_key=key,
                examples=[
                    "empty notifications", "empty my notifications",
                    "empty all of my notifications", "please empty my notifications",
                    "empty notifications all", "clear my notifications",
                    "clear notifications", "clear all notifications",
                    "delete my notifications", "delete all notifications",
                    "dismiss my notifications", "remove my notifications",
                ],
                func=self.empty_notifications,
            ),
            Skill(
                wake_word=wake, skill_key="weather-update", plugin_key=key,
                examples=[
                    "whats the weather", "weather outside",
                    "what is the weather today", "can you tell me the weather",
                    "can you tell me the weather today", "weather today", "the weather",
                    "hows the weather", "whats the weather like",
                    "what is it like outside", "is it cold outside",
                    "is it warm outside", "is it raining", "whats the temperature",
                ],
                func=self.weather_update,
            ),
            Skill(
                wake_word=wake, skill_key="set-timer", plugin_key=key,
                examples=[
                    "set a timer for 10 minutes", "can you create a timer for 5 minutes",
                    "make a timer for 11 minutes", "start a timer for 5 minutes",
                    "start a 5 minute timer", "make a new 2 minute timer",
                    "create a timer for 5 minute",
                    "can you make a timer called Cooking for 5 minutes",
                    "start a new timer for 10 minutes and call it Eggs",
                    "make a timer named Spaghetti for 5 minutes",
                    # The name in front of the word, which is how people
                    # actually say it - "an eggs timer", not "a timer called
                    # eggs".
                    "create an eggs timer for 5 minutes",
                    "set a spaghetti timer for 1 hour",
                    "start a laundry timer for 40 minutes",
                    "make a bread timer for 25 minutes",
                    "put a coffee timer on for 4 minutes",
                ],
                arguments={
                    # LEMMA, not LOWER: one entry covers singular and plural.
                    # Abbreviations ("mins", "secs") are expanded upstream by
                    # normalize.expand_units before this ever sees them.
                    "time": [
                        [{"LIKE_NUM": True},
                         {"LEMMA": {"IN": ["second", "minute", "hour", "day"]}}],
                    ],
                    "name": [
                        # "call it Eggs" is in the examples but was not in the
                        # pattern, so that phrasing never produced a name.
                        [{"LOWER": {"IN": ["call", "called", "naming", "named", "name"]}},
                         {"LOWER": "it", "OP": "?"},
                         {"POS": "DET", "OP": "?"},
                         {"IS_ALPHA": True, "IS_STOP": False}],
                        # "an eggs timer" - the word immediately before
                        # "timer". Units and quantifiers are excluded or
                        # "a 5 minute timer" would come back named "minute",
                        # and determiners are stop words so "a timer" is safe.
                        [{"IS_ALPHA": True, "IS_STOP": False,
                          "LOWER": {"NOT_IN": TIMER_NAME_STOPWORDS}},
                         {"LOWER": {"IN": ["timer", "timers"]}}],
                    ],
                },
                func=self.start_timer,
            ),
            Skill(
                wake_word=wake, skill_key="cancel-timer", plugin_key=key,
                examples=[
                    # All of them
                    "cancel my timers", "stop all timers", "clear my timers",
                    "cancel all of my timers", "turn off my timers",
                    "stop all my timers", "cancel the timer",
                    "stop the timer", "end the timer", "kill the timer",
                    "get rid of the timer", "remove the timer",
                    # One, by name
                    "cancel the eggs timer", "stop the pasta timer",
                    "cancel the timer called eggs",
                    "stop the timer named laundry",
                    "end the bread timer",
                    # One, by how long it was set for. Determiners vary, and a
                    # pattern compiles the one it was given - so the shapes
                    # differ rather than repeating "the" three times.
                    "cancel the 5 minute timer", "stop the 10 minute timer",
                    "cancel my 30 second timer", "stop the 30 second timer",
                    "cancel the 1 hour timer",
                ],
                arguments={
                    # LEMMA covers singular and plural in one entry, and
                    # normalize.expand_units has already turned "mins" into
                    # "minutes" by the time this runs.
                    "time": [
                        [{"LIKE_NUM": True},
                         {"LEMMA": {"IN": ["second", "minute", "hour"]}}],
                    ],
                    "name": [
                        # "called eggs" / "named laundry"
                        [{"LOWER": {"IN": ["call", "called", "name", "named"]}},
                         {"LOWER": "it", "OP": "?"},
                         {"POS": "DET", "OP": "?"},
                         {"IS_ALPHA": True, "IS_STOP": False}],
                        # "the eggs timer" - the word immediately before
                        # "timer", as long as it is not a unit or a quantifier.
                        # Without the exclusion "the five minute timer" would
                        # hand back a timer named "minute".
                        [{"IS_ALPHA": True, "IS_STOP": False,
                          "LOWER": {"NOT_IN": TIMER_NAME_STOPWORDS}},
                         {"LOWER": {"IN": ["timer", "timers"]}}],
                    ],
                },
                func=self.cancel_timers,
            ),
            Skill(
                wake_word=wake, skill_key="check-timers", plugin_key=key,
                examples=[
                    "how long is left on my timer", "how much time is left",
                    "check my timers", "what timers are running",
                    "how long until my timer is done", "how long on the timer",
                ],
                func=self.check_timers,
            ),
            # The calendar skills live in the Calendar plugin, against its own
            # registry. Two skills claiming "what is my next event" is the
            # intent matcher choosing between them on wording, which is not a
            # decision anybody wants it making.
            Skill(
                wake_word=wake, skill_key="quiet-on", plugin_key=key,
                examples=[
                    "do not disturb", "turn on do not disturb",
                    "enable do not disturb", "dont disturb me",
                    "leave me alone", "hold my notifications",
                ],
                func=self.quiet_on,
            ),
            Skill(
                wake_word=wake, skill_key="quiet-off", plugin_key=key,
                examples=[
                    "turn off do not disturb", "disable do not disturb",
                    "stop do not disturb", "you can disturb me",
                    "let my notifications through",
                ],
                func=self.quiet_off,
            ),
            Skill(
                wake_word=wake, skill_key="mute-on", plugin_key=key,
                examples=[
                    "be quiet", "mute yourself", "stop making noise",
                    "silence", "turn off your sounds", "mute the panel",
                    "no sounds",
                ],
                func=self.mute_on,
            ),
            Skill(
                wake_word=wake, skill_key="mute-off", plugin_key=key,
                examples=[
                    "unmute", "you can make noise", "turn your sounds back on",
                    "unmute yourself", "sounds on",
                ],
                func=self.mute_off,
            ),
            Skill(
                wake_word=wake, skill_key="go-to-page", plugin_key=key,
                examples=[
                    "show the calendar", "open the calendar", "go to settings",
                    "show me the home page", "open settings", "go home",
                    "take me home", "show the clock", "open the web page",
                ],
                arguments={
                    "page_name": [
                        [{"LOWER": {"IN": ["the", "me", "to"]}, "OP": "*"},
                         {"IS_ALPHA": True, "OP": "{1,3}"}],
                    ]
                },
                func=self.go_to_page,
            ),
            Skill(
                wake_word=wake, skill_key="open-bookmark", plugin_key=key,
                examples=[
                    "open scryfall", "open my scryfall bookmark",
                    "go to scryfall", "open the bookmark for scryfall",
                    "bring up scryfall", "open bookmark scryfall",
                ],
                arguments={
                    # Everything after the verb, however long.
                    #
                    # A bookmark is named by whoever saved it and the title
                    # comes from the page, so it can be anything - "Scryfall"
                    # or "Advanced Search - Scryfall". Anchoring on the verb
                    # and taking the rest is the only shape that works; the
                    # matching happens in the handler, against the list.
                    "wanted": [
                        [{"LOWER": {"IN": ["open", "goto", "launch"]}},
                         {"IS_ALPHA": True, "OP": "+"}],
                        [{"LOWER": "go"}, {"LOWER": "to"},
                         {"IS_ALPHA": True, "OP": "+"}],
                    ]
                },
                func=self.open_bookmark,
            ),
            Skill(
                wake_word=wake, skill_key="nevermind", plugin_key=key,
                examples=[
                    "nevermind", "never mind", "cancel", "cancel that",
                    "forget it", "forget that", "stop listening",
                    "stop", "abort", "nothing", "leave it",
                    "disregard that", "scratch that", "dont worry about it",
                ],
                func=self.nevermind,
                # Which word was said decides what it does, so the whole
                # utterance is needed rather than an argument out of it.
                wants_phrase=True,
            ),
            Skill(
                wake_word=wake, skill_key="quit-application", plugin_key=key,
                examples=[
                    "quit the application", "quit application",
                    "close the application", "close application",
                    "exit the application", "exit application",
                    "close down the application", "shut down the application",
                    "shut down application", "shut the application down",
                    "close the client", "exit the client", "quit the client",
                    "close client", "exit client", "quit client",
                    "close the app", "quit the app", "exit the app",
                    "close the program", "exit the program", "quit the program",
                ],
                func=self.quit_application,
            ),
        ]
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

    def tell_relative_date(self, given_date: str):
        def ordinal(n: int) -> str:
            if 10 <= n % 100 <= 20:
                suffix = "th"
            else:
                suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            return f"{n}{suffix}"

        def readable(d: date) -> str:
            return f"{d.strftime('%B, %A the ')} {ordinal(d.day)}"

        today = date.today()
        given_date = (given_date or "").lower()

        if "yesterday" in given_date or "before today" in given_date:
            answer = f"Yesterday was, {readable(today - timedelta(days=1))}"
        elif "tomorrow" in given_date or "after today" in given_date:
            answer = f"Tomorrow is, {readable(today + timedelta(days=1))}"
        elif "today" in given_date:
            answer = f"Today is, {readable(today)}"
        else:
            answer = ""

        self._respond(answer or "I don't know how to answer that.")

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
        # Not spoken on success: the panel opening IS the answer, and reading
        # it out over the list somebody is now looking at helps nobody.
        self.client.public.notification_history.manager.open_history()
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
                           speak=f"{temperature} degrees.")

    def start_timer(self, time: str = None, name: str = None):
        """
        "set a timer for 10 minutes", "make a timer called Eggs for 5 minutes".

        `time` arrives as spoken text - "10 minutes", "1 hour" - because a
        transcript is untrusted and normalisation converts most spoken numbers
        but is not a guarantee. Parsed here rather than trusted.
        """
        seconds = _spoken_duration(time)
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

        timer = self.client.public.timers["start"](seconds, name=name or "")
        if timer is None:
            self._respond("I could not start that timer.")
            return

        from src.assets.bundled.CoreWidgetsBundle.timers import describe
        label = f" for {name}" if name else ""
        self._respond(f"{describe(seconds)}{label}, starting now.")

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
        wanted = (name or "").strip()

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
                from src.assets.bundled.CoreWidgetsBundle.timers import describe
                self._respond(
                    f"Stopped the timer set for {describe(timer.duration)}.")
            return

        self._respond(f"Stopped {len(matched)} timers.")

    def _running_summary(self, running: list) -> str:
        """What is actually on, for when a request matched nothing."""
        from src.assets.bundled.CoreWidgetsBundle.timers import describe
        if not running:
            return "Nothing is running."
        names = []
        for timer in running:
            names.append(timer.name if timer.name else describe(timer.duration))
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

        from src.assets.bundled.CoreWidgetsBundle.timers import describe
        lines = [f"{t.label()}: {describe(t.remaining())} left" for t in running]
        spoken = "; ".join(lines)
        self.client.answer("mdi.timer-outline",
                           f"{len(running)} timer" + ("s" if len(running) != 1 else ""),
                           lines, tint="#3f7fbf", speak=spoken)

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
        for entry_key in self.client.PAGES.keys():
            label = str(entry_key).lstrip("#").replace("_", " ")
            label = label.replace("cwb ", "").replace(" page", "")
            hit = _overlap(wanted, label)
            if hit > score:
                best, score = entry_key, hit

        # A page nobody meant is worse than admitting the miss: this navigates,
        # so a wrong guess takes the screen away from whatever was on it.
        if best is None or score < 0.5:
            self._respond(f"I do not have a page called {wanted}.")
            return
        self.client.call_on_ui(
            lambda target=best: self.client.goto(target, override=True))

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


#Words that carry no meaning in a request and only dilute a match.
FILLER = ("the", "a", "an", "my", "me", "please", "to", "up", "for")

#Words that ask for a page without naming one. Dropped before matching, or
#"show me the home page" is measured as three words against one and scores a
#third of what it should.
PAGE_VERBS = ("show", "open", "go", "goto", "take", "bring", "display",
              "switch", "page", "screen")


def _clean_words(text: str, drop: tuple = ()) -> str:
    """The words worth matching on, in order."""
    words = str(text or "").lower().split()
    skip = set(FILLER) | set(drop)
    return " ".join(w for w in words if w and w not in skip).strip()


def _overlap(said: str, candidate: str) -> float:
    """
    How much of what was said appears in the candidate, 0 to 1.

    Words rather than characters, and only whether each word is present rather
    than where. "scryfall" against "Advanced Search - Scryfall" scores 1: the
    part somebody says is the part they remember, and it is rarely the whole
    title.
    """
    said_words = [w for w in _clean_words(said).split() if w]
    if not said_words:
        return 0.0
    hay = str(candidate or "").lower()
    hits = sum(1 for w in said_words if w in hay)
    return hits / len(said_words)


def _spoken_duration(text: str) -> float:
    """
    Seconds from a phrase like "10 minutes" or "1 hour 30 minutes".

    Transcript normalisation turns most spoken numbers into digits before this
    sees them, but not all of it - so a small word list covers what is left
    rather than trusting that every "five" arrived as a "5".
    """
    import re

    if not text:
        return 0.0

    # "a"/"an" are a soft one: they only count when nothing else is pending,
    # or "half an hour" reads as "half", then "an" overwriting it with 1, then
    # an hour - and a thirty minute timer becomes a sixty minute one.
    soft = {"a", "an", "the"}
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
        "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
        "forty-five": 45, "fortyfive": 45, "fifty": 50, "sixty": 60,
        "half": 0.5, "quarter": 0.25,
    }
    units = {
        "second": 1, "seconds": 1, "sec": 1, "secs": 1,
        "minute": 60, "minutes": 60, "min": 60, "mins": 60,
        "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    }

    tokens = re.findall(r"[a-z0-9\-\.]+", str(text).lower())
    total = 0.0
    pending = None

    for token in tokens:
        if token in units:
            # A unit with no number in front of it means one of them:
            # "set a timer for an hour" arrives here as just "hour".
            total += (1.0 if pending is None else pending) * units[token]
            pending = None
            continue
        try:
            pending = float(token)
            continue
        except ValueError:
            pass
        if token in soft:
            if pending is None:
                pending = 1
            continue
        if token in words:
            value = words[token]
            # "twenty five" is one number, not two. normalize.py usually joins
            # compounds before a skill sees them, but not always, and losing
            # the tens turns a 25 minute timer into a 5 minute one.
            if (pending is not None and pending >= 20
                    and pending % 10 == 0 and value < 10):
                pending += value
            else:
                pending = value

    # "set a timer for 10" with no unit at all - minutes is what people mean.
    if total == 0 and pending:
        total = pending * 60
    return float(total)
