"""
A judge on another machine, or beside the panel, reached over TCP.

The same object serves both: `subprocess` is this pointed at 127.0.0.1 with
the panel having started the server, and `socket` is this pointed somewhere
else. One implementation rather than two, because a local mode with its own
code path is a second thing to keep working and only one of them gets used
enough to notice when it breaks.

**A judgement is one connection.** Ask, read a key, close. There is nothing to
keep open between utterances - no session, no cache the panel owns - and a
long-lived connection would be a thing to reconnect, heartbeat and reason
about for no gain.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.assistant import judge_protocol
from src.assistant.judge_protocol import DEFAULT_PORT, ProtocolError

if TYPE_CHECKING:
    from src.main import Client


class SocketJudge:
    """A judge somewhere else. `available` is whether it answered recently."""

    #Long enough to reach a machine on the same network and no longer. This
    #is checked while the panel starts, where waiting is cheap.
    HELLO_TIMEOUT = 3.0

    #Read by JudgeFacade.start(). A "no" here can become a "yes" without
    #anything being rebuilt: a server beside the panel is still loading its
    #model when the panel asks, and one on another machine may be started
    #long after the panel was.
    RECOVERS = True

    #How long to wait before asking again after a failure. An error stamped
    #once and kept for ever would mean restarting the panel every time the
    #far end was restarted, which is the wrong way round.
    RETRY_EVERY = 8.0

    def __init__(self, client: "Client", host: str = "", port: int = 0):
        self.client = client
        self.error = ""
        self._retry_at = 0.0
        self._ready = False

        self.host = str(host or self._setting(
            "assistant.wake.judge_host.value", "") or "").strip()
        self.port = int(port or self._setting(
            "assistant.wake.judge_port.value", DEFAULT_PORT) or DEFAULT_PORT)
        self.timeout = float(self._setting(
            "assistant.wake.judge_timeout.value", 1.0) or 1.0)

        if not self.host:
            # Said at startup rather than the first time somebody speaks.
            # An empty address is a setting nobody filled in, and it will not
            # fix itself, so there is no point retrying it.
            self.error = "no address to ask - set assistant.wake.judge_host"
            return

        self._hello()

    def _setting(self, path, default=None):
        try:
            return self.client.setting(path, default)
        except Exception:
            return default

    def _log(self, level, message):
        try:
            self.client.log(level, f"[Judge] {message}")
        except Exception:
            pass

    ## -- readiness

    @property
    def available(self) -> bool:
        """
        Whether the far end answered, re-asking after a while if it did not.

        A property rather than a stored flag, because the facade reads it
        before every judgement: a server that came up after the panel gets
        used without anything having to notice and rebuild.
        """
        if self._ready:
            return True
        if not self.host:
            return False
        if time.time() < self._retry_at:
            return False
        return self._hello()

    def _hello(self) -> bool:
        """Ask whether it is there, and remember for a while if it is not."""
        try:
            reply = judge_protocol.ask(self.host, self.port, {"cmd": "ping"},
                                       self.HELLO_TIMEOUT)
        except Exception as exc:
            self._ready = False
            self.error = f"{self.host}:{self.port} did not answer ({exc})"
            self._retry_at = time.time() + self.RETRY_EVERY
            return False

        if not reply.get("ok"):
            self._ready = False
            self.error = (f"{self.host}:{self.port} is there but not ready: "
                          f"{reply.get('reason') or 'no reason given'}")
            self._retry_at = time.time() + self.RETRY_EVERY
            return False

        if not self._ready:
            self._log("info", f"{self.host}:{self.port} answered"
                              f"{' - ' + str(reply.get('model')) if reply.get('model') else ''}.")
        self._ready = True
        self.error = ""
        return True

    ## -- asking

    def judge(self, payload: dict) -> str:
        """
        The key the far end gave. Raises for anything else.

        Raising rather than returning "" on purpose: the facade turns any
        exception into the rules deciding, and it logs the reason. Returning
        an empty string here would lose the reason.
        """
        if not self.host:
            raise RuntimeError(self.error or "no address to ask")

        try:
            reply = judge_protocol.ask(self.host, self.port, payload,
                                       self.timeout)
        except Exception as exc:
            # Marked down and retried later. One slow or refused connection
            # is not proof the machine has gone, but continuing to wait a
            # timeout per utterance while it is down would be.
            self._ready = False
            self.error = f"{self.host}:{self.port} did not answer ({exc})"
            self._retry_at = time.time() + self.RETRY_EVERY
            raise RuntimeError(self.error) from exc

        key = judge_protocol.key_from(reply)
        if not key:
            raise ProtocolError(
                f"{self.host}:{self.port} answered "
                f"{str(reply)[:80]!r}, which is not a key")
        return key

    def status(self) -> dict:
        """What the far end says about itself, for a diagnostics page."""
        try:
            return judge_protocol.ask(self.host, self.port, {"cmd": "status"},
                                      self.HELLO_TIMEOUT)
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    def stop(self) -> None:
        # Nothing is held open. The server is somebody else's process, and
        # one started beside the panel is stopped by the service registry
        # that started it.
        self._ready = False
