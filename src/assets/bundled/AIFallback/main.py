from __future__ import annotations

import json
from datetime import datetime
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
    #What ends a conversation when it is said.
    #
    #Checked inside the follow-up loop as well as registered with the cancel
    #engine, because a session takes precedence: with one open every phrase
    #goes into its queue rather than to the intent engine, so "stop" arrived
    #here as a question and was sent to the model to answer.
    DISMISS_WORDS = (
        "nevermind", "never mind", "no nevermind", "no never mind",
        "cancel", "cancel that", "forget it", "forget that", "nothing",
        "nothing nevermind", "leave it", "disregard", "disregard that",
        "scratch that", "dont worry", "don't worry", "dont worry about it",
        "don't worry about it", "as you were", "stop", "stop it", "abort",
        "quit that", "that's all", "thats all", "we're done", "were done",
        "goodbye", "bye",
    )

    #How far the conversation card sits in from every edge. Enough that it
    #reads as laid on top of the panel rather than as a new page, and enough
    #that there is somewhere beside it to press to put it away.
    PANEL_INSET = 28


    ## CORE

    def __init__(self):
        self.panel = None
        self.chat = None
        self.history = []
        self.busy = False
        # Only ever set for the first request of a conversation, and read
        # by `_system_message()`, which can run outside one.
        self._opening_context = None
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
            keywords=list(self.DISMISS_WORDS),
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

    def on_fallback(self, event, context=None):
        """
        Nothing understood the phrase, so answer it with the AI.

        Runs off the event thread: this fires from inside the intent engine,
        and an HTTP round trip there would stall the whole STT pipeline.

        `context` is the turn before this one, handed over by the client - a
        `ContextEntry`, or None when there is nothing recent. The plugin does
        not keep it and does not build it; it reads what it was given. Where
        the history lives is the client's business, and a plugin keeping its
        own copy is a second history to disagree with the first.
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

        Thread(target=self._converse, args=[phrase, context],
               name="__ai_fallback", daemon=True).start()

    ## CONVERSATION

    def _converse(self, first_phrase: str, context=None):
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
        # Spent on the FIRST question only. The turn before this conversation
        # is context for the thing that started it; by the third follow-up the
        # conversation is its own context, and repeating a stale one every
        # request pays for it in tokens and invites the model to keep
        # answering the wrong question.
        self._opening_context = context
        phrase = first_phrase
        dismissed = False

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
                    # Spent. From here the conversation carries itself.
                    self._opening_context = None

                    phrase = session.wait_for_phrase()
                    if phrase is None:
                        # Cancelled, timed out, or closed.
                        #
                        # A cancelled session is somebody saying they were
                        # finished: wait_for_phrase() recognises the backing
                        # out itself and answers None, so "stop" never reached
                        # the check below and the panel stayed up with nothing
                        # listening to it.
                        dismissed = bool(getattr(session, "cancelled", False))
                        break
                    phrase = phrase.strip()
                    if not phrase:
                        break
                    if self.is_dismissal(phrase):
                        # Answered here rather than by the cancel engine: an
                        # open session queues every phrase, so this one never
                        # reached it and was asked of the model instead.
                        dismissed = True
                        break
        finally:
            if self.session is session:
                self.session = None
            with self._lock:
                self.busy = False
            self.client.call_on_ui(lambda: self._set_status(""))
            if dismissed:
                # Somebody said they were finished. The session ending is half
                # of that; the panel going is the other half, and stopping at
                # the first left a conversation on screen that nothing was
                # listening to.
                self.close_panel()

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
            # Not "Thinking…": the pill reads the assistant's own state now,
            # and says more than this did - whether it is speaking, and that
            # the wake word will interrupt it. Overriding it here replaced all
            # of that with one word.

        # The pill says so for the whole round trip. The assistant's own
        # THINKING is set while the phrase is routed and cleared once routing
        # returns, which is before this request has even been sent - so a slow
        # model left the panel looking idle while it waited.
        with self.client.thinking("asking the model"):
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
                # Kept so the panel can silence its OWN reply and nothing
                # else. A conversation that ends after something new has
                # started talking would otherwise cut that off instead.
                try:
                    self._speech_owner = self.client.speech_owner()
                except Exception:
                    self._speech_owner = 0

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

    def _system_message(self) -> str:
        """
        The configured prompt, plus what the model cannot work out for itself.

        Appended at request time and NOT written into the setting. The setting
        is somebody's prompt: editing it from code means their words and the
        panel's grow into each other, and a date baked into a saved string is
        wrong by the following morning.

        The time goes on every request rather than only the first. A
        conversation can run for several minutes, and a system message that
        still claims it is Tuesday evening at half past midnight is worse
        than none - the model has no way to notice.
        """
        parts = [str(self.option("conversation.system_prompt", "")).strip()]

        try:
            now = datetime.now()
            parts.append(
                "For reference, it is currently "
                f"{now.strftime('%A, %B')} {now.day}, {now.year}, "
                f"{now.strftime('%I:%M %p').lstrip('0')}.")
        except Exception:
            pass

        # Offered, not asserted. The turn before is often unrelated - somebody
        # asks the time and then asks something else entirely - and a model
        # told "this is the context" will find a connection whether or not
        # one is there.
        entry = self._opening_context
        if entry is not None:
            try:
                summary = entry.summary()
            except Exception:
                summary = ""
            if summary:
                parts.append(
                    "Just before this, the panel handled another question. "
                    + summary
                    + " If the question you are being asked now follows on "
                      "from that, use it. If it does not, ignore it entirely "
                      "and do not mention it.")

        return "\n\n".join(part for part in parts if part)

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

        messages = [{"role": "system", "content": self._system_message()}]
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

        # The room is about to be a conversation. Music over the top of it is
        # both hard to talk through and hard for the microphone to hear past.
        self._held_music = False
        try:
            music = self.client.public.music
            self._held_music = bool(music["hold"]("the assistant"))
        except Exception:
            self._held_music = False

        def created(panel):
            self.panel = panel
            # Long on purpose: a conversation should not be cut off mid-thought
            # by the ordinary interaction timeout.
            try:
                self.client.TIMEOUTS.add(timeout, self.close_panel, "__ai_fallback_panel")
                self.client.TIMEOUTS.start("__ai_fallback_panel")
            except Exception:
                pass

        # A card over the whole screen, not a drawer down one side.
        #
        # A conversation is the thing being looked at while it is open, and a
        # column six hundred pixels wide turns every reply into a narrow
        # ribbon. The inset keeps it reading as something laid on top rather
        # than as a new page.
        #
        # Sized here rather than after the panel exists: open_panel() works out
        # where it slides TO before on_created runs, so a size applied
        # afterwards animates to the old position and lands cut off.
        inset = self.PANEL_INSET
        try:
            host = self.client.OVERLAYS
            width = max(360, host.width() - inset * 2)
        except Exception:
            width, inset = Panel.DEFAULT_WIDTH, 0

        self.client.create_panel(
            content=self.chat, width=width, edge="right",
            # None fills the cross axis, less the margin.
            height=None, margin=inset,
            key="__ai_fallback", destroy_on_close=False, on_created=created,
            # However it goes away - the button, a press beside it, the
            # timeout - the conversation goes with it. Without this the
            # session outlived the panel it belonged to: every phrase after
            # that landed in a queue for a conversation nobody could see, and
            # nothing else answered until it timed out.
            on_closed=self.close_panel,
            # A conversation covering the screen has to be dismissable by
            # pressing beside it; there is nowhere else to press.
            dismiss_on_outside_click=True,
            # A conversation is read at a person's own pace and produces no
            # interaction while it is. The panel has its own, much longer
            # timeout - see `conversation.panel_timeout` - which is what
            # eventually puts it away.
            blocks_idle=True,
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

    def is_dismissal(self, phrase: str) -> bool:
        """Whether a phrase means the conversation is over."""
        cleaned = " ".join(str(phrase or "").lower().split())
        cleaned = cleaned.strip(" .,!?")
        return cleaned in self.DISMISS_WORDS

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

        # Re-entrant: the panel's own close hook calls this, and this closes
        # the panel. Second time through there is nothing left to do.
        if getattr(self, "_closing_panel", False):
            return
        self._closing_panel = True

        self._dismissed = True

        # Stop mid-sentence. An answer being read aloud to a panel that is no
        # longer there is the same mistake as listening for a reply to it.
        #
        # Only this conversation's own reply, though. By the time a panel
        # closes, the voice may belong to whatever was asked next, and
        # silencing that on the way out is the bug this token exists for.
        try:
            if self.client.TTS is not None:
                self.client.TTS.stop(
                    owner=getattr(self, "_speech_owner", None) or None)
        except Exception as e:
            self.client.log("debug", f"[AIFallback] Could not stop speech: {e}")

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

        # Whatever was playing gets to carry on. Only if this is what stopped
        # it: music paused by hand during a conversation was not asking to be
        # started again afterwards.
        if getattr(self, "_held_music", False):
            self._held_music = False
            try:
                self.client.public.music["release"]("the assistant")
            except Exception as e:
                self.client.log("debug",
                                f"[AIFallback] Could not resume music: {e}")

        self._closing_panel = False
