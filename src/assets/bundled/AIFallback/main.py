from __future__ import annotations

import json
import urllib.error
import urllib.request
from threading import Thread, Lock

from src.plugin.template import Plugin

from .chat_panel import ChatPanel
from .markdown import to_speech

API_URL = "https://api.openai.com/v1/chat/completions"
REQUEST_TIMEOUT = 60


class AIFallback(Plugin):

    ## CORE

    def __init__(self):
        self.panel = None
        self.chat = None
        self.history = []
        self.busy = False
        self._lock = Lock()

    def load(self, carryover=None):
        self.client.subscribe_to_event("on_assistant_fallback", self.on_fallback)

    def unload(self, carryover=None):
        self.client.unsubscribe_from_event("on_assistant_fallback", self.on_fallback)
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

        reply, error, fatal = self._ask(phrase)

        if error:
            self._report_error(error, panel_open)
            return not fatal

        if not panel_open:
            self.client.call_on_ui(lambda: self._show(phrase, from_user=True))

        self.history.append({"role": "user", "content": phrase})
        self.history.append({"role": "assistant", "content": reply})
        turns = int(self.option("general.history_turns", 8))
        self.history = self.history[-turns * 2:]

        self.client.call_on_ui(lambda: self._show(reply, from_user=False))

        if self.option("conversation.speak_replies", True):
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

    def _ask(self, phrase: str) -> tuple[str, str, bool]:
        """
        (reply, error, fatal). Never raises - a failed call becomes a message.

        `fatal` means retrying will not help until something is changed: a bad
        key, no credit, a model this account cannot use. Those end the
        conversation rather than leaving a session open that will fail again
        on every follow-up. Rate limits and network errors are not fatal.
        """
        key = self.secret("OPENAI_API_KEY")
        if not key:
            return "", "No OpenAI key is set for this plugin.", True

        messages = [{"role": "system",
                     "content": str(self.option("conversation.system_prompt", ""))}]
        messages.extend(self.history)
        messages.append({"role": "user", "content": phrase})

        payload = json.dumps({
            "model": str(self.option("general.model", "gpt-4o-mini")),
            "messages": messages,
            "max_tokens": int(self.option("general.max_tokens", 600)),
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
                                 "plugin's settings."), True
            if e.code == 429:
                if "quota" in code or "quota" in detail.lower() or "billing" in detail.lower():
                    return "", ("Your OpenAI account has no available credit, so the "
                                "request was refused. This is a billing limit, not a "
                                f"rate limit.\n\n{detail}"), True
                return "", f"Rate limited by OpenAI - too many requests.\n\n{detail}", False
            if e.code == 404 and "model" in detail.lower():
                model = self.option("general.model", "")
                return "", (f"The model `{model}` is not available on this account. "
                            f"Pick another under this plugin's settings.\n\n{detail}"), True
            return "", f"OpenAI returned {e.code}.\n\n{detail}", e.code < 500
        except urllib.error.URLError as e:
            return "", f"Could not reach OpenAI.\n\n{e.reason}", False
        except (ValueError, OSError) as e:
            return "", f"Could not read the reply.\n\n{e}", False

        try:
            return body["choices"][0]["message"]["content"].strip(), "", False
        except (KeyError, IndexError, AttributeError):
            return "", "OpenAI returned an unexpected response shape.", False

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
            content=self.chat, width=520, edge="right",
            key="__ai_fallback", destroy_on_close=False, on_created=created,
        )

    def _show(self, text: str, from_user: bool):
        self._ensure_panel()
        if self.chat is not None:
            self.chat.add_message(text, from_user)
        self._restart_timeout()

    def _set_status(self, text: str):
        if self.chat is not None:
            self.chat.set_status(text)

    def _restart_timeout(self):
        try:
            self.client.TIMEOUTS.start("__ai_fallback_panel")
        except Exception:
            pass

    def close_panel(self):
        try:
            self.client.TIMEOUTS.cancel("__ai_fallback_panel")
        except Exception:
            pass
        panel, self.panel, self.chat = self.panel, None, None
        self.history = []
        if panel is not None:
            self.client.call_on_ui(panel.close_panel)
