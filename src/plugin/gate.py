"""
An upload of new code, waiting for somebody in the room to agree.

A device is let in once, when it asks. That is a decision about a *device* and
it is made once because devices do not change. A plugin is different: every
new one is a separate piece of code with the run of the house, arriving after
the device was trusted and doing something the device's owner may not have
looked at. Being allowed to upload and being allowed to install THIS are not
the same question.

So the first install of a plugin that is not already there waits here until
somebody presses a button on the panel. An **update** to a plugin already
installed does not: that plugin was accepted once, and asking again every time
it gains a bug fix trains people to press yes without reading.

Kept as an object with a plan attached rather than as a live dialog, because
the two ends are minutes apart and on different threads - the request arrives
on a Flask worker, the answer comes from the UI - and because a panel that
restarts between the two should forget the request rather than apply it.
"""

from __future__ import annotations

import time
from threading import RLock

from src.plugin.install import Plan, Refusal, apply as apply_plan


#How long an unanswered request stands. Long enough to walk to the panel,
#short enough that a "yes" tapped tomorrow is not agreeing to something
#somebody uploaded and forgot about.
EXPIRES_AFTER = 5 * 60.0


class PendingInstall:
    """One upload, its plan, and who sent it."""

    def __init__(self, plan: Plan, token: str, who: str):
        self.plan = plan
        self.token = token
        self.who = who or "a device"
        self.at = time.time()
        self.state = "waiting"        # waiting | approved | denied | expired
        self.detail = ""

    @property
    def expired(self) -> bool:
        return time.time() - self.at > EXPIRES_AFTER

    def summary(self) -> dict:
        found = dict(self.plan.summary())
        found.update({"state": self.state, "who": self.who,
                      "detail": self.detail,
                      "waited": int(time.time() - self.at)})
        return found


class InstallGate:
    """
    The queue of uploads waiting to be agreed to, and the asking.

    One at a time on purpose. Two confirmation dialogs stacked on a wall panel
    is a person pressing whichever is on top, and the thing being agreed to is
    exactly what must not be guessed at.
    """

    def __init__(self, client):
        self.client = client
        self._lock = RLock()
        self._pending: dict = {}       # id -> PendingInstall
        self._showing = ""             # the id whose dialog is up

    ## -- asking

    def key_owner(self, key: str, folder: str) -> str:
        """
        The folder already holding `key`, or "" - checked at the last moment.

        A plan is made when the upload arrives and applied when somebody at
        the panel agrees, which can be minutes apart. Another plugin can claim
        the key in between, and writing a folder that can never load is worse
        than refusing it.
        """
        try:
            manager = self.client.PLUGIN
            from pathlib import Path
            for _plugin, other in manager.get_plugins():
                if other != key:
                    continue
                path = manager.registered.get(other)
                owner = Path(str(path)).name if path else other
                return "" if owner == folder else owner
            for item in manager.pending_plugins():
                if getattr(item, "key", None) == key:
                    owner = Path(str(getattr(item, "path", ""))).name or key
                    return "" if owner == folder else owner
        except Exception as e:
            self.client.log("debug", f"[Plugins] Could not check the key: {e}")
        return ""

    def ask(self, plan: Plan, token: str, who: str) -> PendingInstall:
        """
        Park an install and put the question on the panel.

        Returns immediately. The caller is an HTTP request and must not sit
        on a socket waiting for somebody to walk into the room.
        """
        request = PendingInstall(plan, token, who)
        with self._lock:
            self._drop_expired()
            self._pending[plan.name] = request
        self.client.log("info",
                        f"[Plugins] '{who}' wants to install a new plugin "
                        f"'{plan.name}' - waiting for the panel.")
        self._show_next()
        return request

    def status(self, name: str) -> dict | None:
        with self._lock:
            self._drop_expired()
            request = self._pending.get(name)
            return request.summary() if request else None

    def waiting(self) -> list:
        with self._lock:
            self._drop_expired()
            return [r.summary() for r in self._pending.values()]

    def forget(self, name: str) -> None:
        with self._lock:
            self._pending.pop(name, None)
            if self._showing == name:
                self._showing = ""

    ## -- answering

    def _drop_expired(self) -> None:
        for name, request in list(self._pending.items()):
            if request.state == "waiting" and request.expired:
                request.state = "expired"
                self.client.log("info", f"[Plugins] The request to install "
                                        f"'{name}' expired unanswered.")

    def _show_next(self) -> None:
        with self._lock:
            if self._showing:
                return
            waiting = [r for r in self._pending.values()
                       if r.state == "waiting" and not r.expired]
            if not waiting:
                return
            request = waiting[0]
            self._showing = request.plan.name

        plan = request.plan
        files = plan.counted("create")
        clash = self.key_owner(plan.key, plan.name)
        self.client.confirm(
            "Install a new plugin?",
            # Short. The body is one line at dialog width, and the paragraph
            # that used to be here - with blank lines in it - ran off the
            # bottom of the card, so the sentence explaining what a plugin can
            # do was the part nobody read.
            f"{request.who} sent '{plan.name}'.",
            # The rest goes in `detail`, which is what the dialog wraps.
            detail=(f"This is code that has not run on this panel before. It "
                    f"will be able to do anything the panel can.\n\n"
                    f"Key: {plan.key}"
                    + (f"  ·  version {plan.version}" if plan.version else "")
                    + f"\n{files} file{'' if files == 1 else 's'} would be "
                      f"added."
                    + (f"\n\nWARNING: the key '{plan.key}' already belongs to "
                       f"'{clash}'. Installing this will not make it load."
                       if clash else "")),
            confirm_text="Install",
            cancel_text="No",
            # Not destructive. Nothing is being destroyed - a folder that did
            # not exist is being created - and red is the panel's word for
            # "this removes something". Using it here spends the colour that
            # marks Revoke and Shut down on the ordinary case.
            destructive=False,
            on_confirm=lambda: self._answer(plan.name, True),
            on_cancel=lambda: self._answer(plan.name, False),
        )

    def _answer(self, name: str, agreed: bool) -> None:
        with self._lock:
            request = self._pending.get(name)
            self._showing = ""
        if request is None:
            return

        if not agreed:
            request.state = "denied"
            request.detail = "Refused at the panel."
            self.client.log("info", f"[Plugins] Install of '{name}' refused "
                                    f"at the panel.")
            self._show_next()
            return

        if request.expired:
            # Answered, but the question had already gone stale. Applying it
            # now would install something somebody agreed to a different
            # version of.
            request.state = "expired"
            request.detail = "The request expired before it was answered."
            self._show_next()
            return

        owner = self.key_owner(request.plan.key, request.plan.name)
        if owner:
            request.state = "denied"
            request.detail = (f"The key '{request.plan.key}' now belongs to "
                              f"'{owner}', so this would never load.")
            self.client.log("warning", f"[Plugins] {request.detail}")
            self._show_next()
            return

        try:
            summary = apply_plan(request.plan)
            request.state = "approved"
            request.detail = f"Installed {summary['written']} files."
            self.client.log("info", f"[Plugins] '{name}' installed with "
                                    f"{summary['written']} files.")
        except Refusal as e:
            request.state = "denied"
            request.detail = str(e)
            self.client.log("error", f"[Plugins] Installing '{name}' failed: {e}")
        self._show_next()
