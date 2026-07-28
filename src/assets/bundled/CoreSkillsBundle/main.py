from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt

from src.plugin.template import Plugin
from src.assistant.skill import Skill
from src.styling import make_font, SIZES, set_style

from .voice_bar import VoiceBar


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
        self.client.subscribe_to_event("on_woke_assistant", self.on_woke)
        self.client.subscribe_to_event("on_assistant_cancelled", self.on_cancelled)

    def built(self):
        self.load_voice_bar()

    def unload(self, carryover=None):
        self.client.unsubscribe_from_event("on_update", self.update_assistant)
        self.client.unsubscribe_from_event("on_assistant_transcribed", self.on_transcribed)
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

    def on_woke(self, event):
        if self.voice_bar is None:
            return
        wake = ""
        if isinstance(event, (tuple, list)) and event:
            skill = event[0]
            wake = getattr(skill, "wake", "") or ""
        elif isinstance(event, str):
            wake = event
        wake = wake or self.wake_word()
        self.client.call_on_ui(lambda: self.voice_bar.show_woke(wake))

    def update_assistant(self, event):
        # on_update runs on the update thread; widgets must be touched on the
        # Qt thread, and only when something actually changed.
        if self.voice_bar is None:
            return

        state = (self.client.ASSIST_STATUS, round(self.client.ASSIST_VOICE_ACTIVITY_LEVEL, 2))
        if state == self._last_state:
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
                    ],
                },
                func=self.start_timer,
            ),
            # The calendar skills live in the Calendar plugin, against its own
            # registry. Two skills claiming "what is my next event" is the
            # intent matcher choosing between them on wording, which is not a
            # decision anybody wants it making.
            Skill(
                wake_word=wake, skill_key="nevermind", plugin_key=key,
                examples=[
                    "nevermind", "never mind", "cancel", "cancel that",
                    "forget it", "forget that", "stop listening",
                    "stop", "abort", "nothing", "leave it",
                    "disregard that", "scratch that", "dont worry about it",
                ],
                func=self.nevermind,
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

    def nevermind(self):
        # STTProcessing.cancel() already short-circuits cancel phrases before
        # intent matching, so this mostly exists so the skill is discoverable
        # in Settings and so the bar acknowledges it.
        self.client.cancel_assistant("nevermind")

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
        if self.client.public.has("notification_history"):
            self.client.public.notification_history.clear()

    def open_notifications(self):
        if self.client.public.has("notification_history"):
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
        label = f" called {name}" if name else ""
        self._respond(f"Timers aren't implemented yet. You asked for {time}{label}.")

    ## HELPERS

    def _respond(self, text: str):
        # Speak when possible, otherwise show it - a skill that silently does
        # nothing because TTS is unconfigured is indistinguishable from a
        # broken one.
        if not self.client.say(text, thread=False):
            self.client.simple_notify("assistant", "Assistant", text)
