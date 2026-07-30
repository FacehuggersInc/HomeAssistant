from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from threading import Thread, Lock

from PyQt6.QtCore import QEvent, QPoint, QRect

from src.plugin.template import Plugin
from src.ui.overlays import Panel

from .chat_panel import ChatPanel
from .markdown import to_speech

API_URL = "https://api.openai.com/v1/chat/completions"
REQUEST_TIMEOUT = 60


@dataclass
class Usage:
    """Token counts for one exchange, or a running total across a session."""

    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def add(self, other: "Usage") -> None:
        self.prompt += other.prompt
        self.completion += other.completion

    @classmethod
    def from_response(cls, body: dict) -> "Usage":
        """
        Read the usage block OpenAI returns beside the reply.

        Taken from the response rather than counted here: a local count would
        need the exact tokeniser for whichever model is configured, and would
        still be wrong, because the billed prompt includes the system message,
        the entire history, and per-message framing this code never sees
        assembled.
        """
        usage = (body or {}).get("usage") or {}
        try:
            return cls(int(usage.get("prompt_tokens", 0) or 0),
                       int(usage.get("completion_tokens", 0) or 0))
        except (TypeError, ValueError):
            return cls()


class AIFallback(Plugin):

    ## CORE

    def __init__(self):
        self.panel = None
        self.chat = None
        self.history = []
        self.busy = False
        self.usage = Usage()
        self.session = None
        self._dismissed = False
        self._lock = Lock()

    def _panel_is_open(self) -> bool:
        """Whether there is a conversation on screen to back out of."""
        return bool(self.session is not None or
                    (getattr(self, "_panel", None) is not None
                     and not getattr(self, "_dismissed", True)))

    def load(self, carryover=None):
        self.client.subscribe_to_event("on_assistant_fallback", self.on_fallback)
        self.client.subscribe_to_event("on_interaction", self.on_interaction)

        # "Nevermind" belongs to a question somebody has thought better of
        # asking, which is exactly this. "Stop" too, since a panel reading an
        # answer out is something to stop.
        #
        # A high priority because a panel is in front of anything else: with
        # one open over music, "stop" means close this.
        self.client.CANCEL.register(
            "aifallback", "answer_panel",
            keywords=["nevermind", "never mind", "no nevermind",
                      "no never mind", "cancel", "cancel that", "forget it",
                      "forget that", "nothing", "nothing nevermind",
                      "leave it", "disregard", "disregard that",
                      "scratch that", "dont worry", "don't worry",
                      "dont worry about it", "don't worry about it",
                      "as you were", "stop", "stop it", "abort", "quit that"],
            handler=self.close_panel,
            is_active=self._panel_is_open,
            priority=50,
            description="close the answer panel and its session",
        )

    def unload(self, carryover=None):
        self.client.unsubscribe_from_event("on_assistant_fallback", self.on_fallback)
        self.client.unsubscribe_from_event("on_interaction", self.on_interaction)
        try:
            self.client.CANCEL.unregister("aifallback")
        except Exception:
            pass
        self.close_panel()

    ## SETTINGS

    def enabled(self) -> bool:
        try:
            return bool(self.settings.general.enabled.value)
        except Exception:
            return False

    def option(self, path: str, default):
        node = self.settings
        try:
            for part in path.split("."):
                node = getattr(node, part)
            return node.value
        except Exception:
            return default

    ## EVENT

    def on_fallback(self, event):
        """
        Nothing understood the phrase, so answer it with the AI.

        Runs off the event thread: this fires from inside the intent engine,
        and an HTTP round trip there would stall the whole STT pipeline.
        """
        phrase = event if isinstance(event, str) else str(event or "")
        phrase = phrase.strip()
        if not phrase or not self.enabled():
            return

        if not self.secret("OPENAI_API_KEY"):
            self.client.log("info", "[AIFallback] No OpenAI key set - ignoring unmatched phrase.")
            self.client.simple_notify(
                "robot", "AI Fallback",
                "No OpenAI key set. Add one in Settings to answer unmatched questions.")
            return

        with self._lock:
            if self.busy:
                # A conversation is already running; its session picks this up.
                return
            self.busy = True

        Thread(target=self._converse, args=[phrase],
               name="__ai_fallback", daemon=True).start()

    ## CONVERSATION

    def _converse(self, first_phrase: str):
        """
        One conversation, start to finish, on one thread.

        A Session is opened BEFORE the first API call. That is what serialises
        the exchange: while a request is in flight, anything else the user says
        lands in the session queue rather than being treated as a fresh command,
        and is only picked up once a reply has come back. Without it a second
        question fired mid-request would race the first.
        """
        session = self.client.STT.new_session() if self.client.STT else None
        self.session = session
        self._dismissed = False
        phrase = first_phrase

        try:
            if session is None:
                self._exchange(phrase)
                return

            with session:
                while True:
                    if not self._exchange(phrase):
                        # Nothing a follow-up can fix; do not hold the session
                        # open just to fail on the next question too.
                        break

                    phrase = session.wait_for_phrase()
                    if phrase is None:
                        # Cancelled, timed out, or closed.
                        break
                    phrase = phrase.strip()
                    if not phrase:
                        break
        finally:
            if self.session is session:
                self.session = None
            with self._lock:
                self.busy = False
            self.client.call_on_ui(lambda: self._set_status(""))

    def _exchange(self, phrase: str) -> bool:
        """
        One question and answer. Returns False if the conversation should end.

        Nothing is shown until a reply actually arrives. Opening the panel
        first and filling it in afterwards meant a failed request left an
        empty panel with an unanswered message in it, which reads as a hang
        rather than an error. The voice bar already shows what was heard, so
        there is no feedback gap.
        """
        panel_open = self.chat is not None
        if panel_open:
            self.client.call_on_ui(lambda: self._show(phrase, from_user=True))
            self.client.call_on_ui(lambda: self._set_status("Thinking…"))

        reply, error, fatal, usage = self._ask(phrase)

        if error:
            self._report_error(error, panel_open)
            return not fatal

        if not panel_open:
            self.client.call_on_ui(lambda: self._show(phrase, from_user=True))

        self.history.append({"role": "user", "content": phrase})
        self.history.append({"role": "assistant", "content": reply})
        turns = int(self.option("general.history_turns", 8))
        self.history = self.history[-turns * 2:]

        self.usage.add(usage)
        self.client.call_on_ui(
            lambda: self._show(reply, from_user=False, usage=usage))

        if self.option("conversation.speak_replies", True) and not self._dismissed:
            spoken = to_speech(reply)
            if spoken:
                self.client.say(spoken, thread=False)

        return True

    def _report_error(self, error: str, panel_open: bool):
        """
        Errors go to a dialog, never to a freshly opened panel.

        A chat panel containing nothing but a failure is worse than no panel:
        it implies a conversation started. If one is already open the note is
        added there too, so the transcript does not end on an unanswered
        question.
        """
        self.client.log("error", f"[AIFallback] {error}")

        summary, _, detail = error.partition("\n\n")

        if panel_open:
            self.client.call_on_ui(lambda: self._show(f"*{summary}*", from_user=False))
            self.client.call_on_ui(lambda: self._set_status(""))

        self.client.alert(
            "The assistant could not answer",
            summary,
            detail=detail.strip() or None,
        )

    ## API

    def _ask(self, phrase: str) -> tuple[str, str, bool, Usage]:
        """
        (reply, error, fatal, usage). Never raises - a failed call becomes a
        message.

        `fatal` means retrying will not help until something is changed: a bad
        key, no credit, a model this account cannot use. Those end the
        conversation rather than leaving a session open that will fail again
        on every follow-up. Rate limits and network errors are not fatal.
        """
        key = self.secret("OPENAI_API_KEY")
        if not key:
            return "", "No OpenAI key is set for this plugin.", True, Usage()

        messages = [{"role": "system",
                     "content": str(self.option("conversation.system_prompt", ""))}]
        messages.extend(self.history)
        messages.append({"role": "user", "content": phrase})

        payload = json.dumps({
            "model": str(self.option("general.model", "gpt-4o-mini")),
            "messages": messages,
            # max_completion_tokens, not max_tokens: the GPT-5 family rejects
            # the old name outright with a 400.
            "max_completion_tokens": int(self.option("general.max_tokens", 600)),
        }).encode("utf-8")

        request = urllib.request.Request(
            API_URL, data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
        )

        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # The body carries the reason; the status code alone is ambiguous.
            # 429 in particular means EITHER real rate limiting OR an account
            # with no credit, and reporting the wrong one sends you looking in
            # the wrong place. An earlier version discarded this entirely and
            # always said "rate limited".
            detail, code = "", ""
            try:
                error = json.loads(e.read().decode("utf-8")).get("error", {})
                detail = error.get("message", "") or ""
                code = (error.get("code") or error.get("type") or "").lower()
            except Exception:
                detail = str(e.reason)

            if e.code == 401:
                return "", (f"OpenAI rejected the key. Check it under this plugin's "
                            f"settings.\n\n{detail}" if detail
                            else "OpenAI rejected the key. Check it under this "
                                 "plugin's settings."), True, Usage()
            if e.code == 429:
                if "quota" in code or "quota" in detail.lower() or "billing" in detail.lower():
                    return "", ("Your OpenAI account has no available credit, so the "
                                "request was refused. This is a billing limit, not a "
                                f"rate limit.\n\n{detail}"), True, Usage()
                return "", f"Rate limited by OpenAI - too many requests.\n\n{detail}", False, Usage()
            if e.code == 404 and "model" in detail.lower():
                model = self.option("general.model", "")
                return "", (f"The model `{model}` is not available on this account. "
                            f"Pick another under this plugin's settings.\n\n{detail}"), True, Usage()
            return "", f"OpenAI returned {e.code}.\n\n{detail}", e.code < 500, Usage()
        except urllib.error.URLError as e:
            return "", f"Could not reach OpenAI.\n\n{e.reason}", False, Usage()
        except (ValueError, OSError) as e:
            return "", f"Could not read the reply.\n\n{e}", False, Usage()

        try:
            return (body["choices"][0]["message"]["content"].strip(), "", False,
                    Usage.from_response(body))
        except (KeyError, IndexError, AttributeError):
            return "", "OpenAI returned an unexpected response shape.", False, Usage()

    ## PANEL

    def _ensure_panel(self):
        # Guarded on `chat` alone, which is set synchronously. `panel` only
        # arrives via the on_created callback, so requiring both meant a
        # delayed or missed callback spawned a fresh panel per message.
        if self.chat is not None:
            return

        self.chat = ChatPanel(self.client)
        timeout = int(self.option("conversation.panel_timeout", 300))

        def created(panel):
            self.panel = panel
            # Long on purpose: a conversation should not be cut off mid-thought
            # by the ordinary interaction timeout.
            try:
                self.client.TIMEOUTS.add(timeout, self.close_panel, "__ai_fallback_panel")
                self.client.TIMEOUTS.start("__ai_fallback_panel")
            except Exception:
                pass

        self.client.create_panel(
            content=self.chat, width=Panel.DEFAULT_WIDTH, edge="right",
            key="__ai_fallback", destroy_on_close=False, on_created=created,
        )

    def _show(self, text: str, from_user: bool, usage: Usage = None):
        # A request already in flight when the panel was dismissed still comes
        # back. Without this it calls _ensure_panel() and the panel the user
        # just tapped away reappears with the answer in it.
        if self._dismissed:
            return
        self._ensure_panel()
        if self.chat is not None:
            self.chat.add_message(text, from_user, usage=usage)
            self.chat.set_totals(self.usage.prompt, self.usage.completion)
        self._restart_timeout()

    def _set_status(self, text: str):
        if self.chat is not None:
            self.chat.set_status(text)

    def _restart_timeout(self):
        try:
            self.client.TIMEOUTS.start("__ai_fallback_panel")
        except Exception:
            pass

    ## DISMISSAL

    # Presses only. on_interaction also fires on every mouse move, and a panel
    # that closed because the pointer crossed the page would be unreadable.
    DISMISS_EVENTS = (QEvent.Type.MouseButtonPress, QEvent.Type.TouchBegin)

    def on_interaction(self, event):
        """
        Tapping anywhere outside the panel ends the conversation.

        Runs on the UI thread, synchronously, for every interaction in the
        app - so it stays cheap and never raises. A handler that throws is
        unsubscribed by iterate_event_callables(), which would silently
        disable this for the rest of the session.
        """
        try:
            panel = self.panel
            if panel is None or not getattr(panel, "open", False):
                return
            if event is None or event.type() not in self.DISMISS_EVENTS:
                return

            # A dialog on top owns the tap. The plugin's own error alert is
            # the common case, and tearing the transcript down behind it while
            # the user is reading the error is not what "tapped outside" means.
            if self.client.DIALOG.get() is not None:
                return

            point = self._global_point(event)
            if point is None or self._inside(panel, point):
                return

            self.client.log("info", "[AIFallback] Tapped outside - closing panel.")
            self.client.call_on_ui(self.close_panel)
        except Exception as e:
            self.client.log("warning", f"[AIFallback] Interaction check failed: {e}")

    @staticmethod
    def _global_point(event):
        """Screen coordinates of an interaction, mouse or touch."""
        try:
            return event.globalPosition().toPoint()
        except Exception:
            pass
        try:
            points = event.points()
            if points:
                return points[0].globalPosition().toPoint()
        except Exception:
            pass
        return None

    @staticmethod
    def _inside(widget, global_point) -> bool:
        try:
            return QRect(widget.mapToGlobal(QPoint(0, 0)),
                         widget.size()).contains(global_point)
        except RuntimeError:
            # Panel deleted between the check above and here.
            return False

    def close_panel(self):
        """
        Tear the whole conversation down: timeout, session, history, panel.

        The session is the part that matters. Left open after the panel is
        gone, the assistant is still listening for a follow-up to a
        conversation that is no longer on screen - every phrase goes into the
        session queue instead of being treated as a fresh command, so nothing
        else responds until it times out.
        """
        try:
            self.client.TIMEOUTS.cancel("__ai_fallback_panel")
        except Exception:
            pass

        self._dismissed = True

        session, self.session = self.session, None
        if session is not None:
            try:
                # Releases the conversation thread blocked in
                # wait_for_phrase() and puts the STT back into wake mode.
                session.cancel()
            except Exception as e:
                self.client.log("warning", f"[AIFallback] Could not cancel session: {e}")

        panel, self.panel, self.chat = self.panel, None, None
        self.history = []
        self.usage = Usage()
        if panel is not None:
            self.client.call_on_ui(panel.close_panel)
