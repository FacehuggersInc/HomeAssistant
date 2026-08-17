"""
Random Chance - flipping a coin and rolling dice.

Two halves kept apart on purpose:

  * `flip()` and `roll()` are the capabilities. Each decides an outcome,
    shows it, says it, and publishes what happened. Anything can call them -
    a skill, an endpoint, another plugin - and they behave the same way each
    time.
  * a skill is one caller among those, and does nothing except decide whether
    the phrase was really for it and then call one.

Kept apart because the endpoint and the outcome system are further callers of
the same functions, and a capability that grew out of a skill handler would
have the skill's assumptions baked into it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from src.plugin.template import Plugin
from src.assistant.skill import Skill, SkillDeclined

from src.constants import APP_NAME, get_data_dir

from . import coin as coin_module
from . import dice as dice_module
from . import wheels as wheels_module
from .coin import CoinWidget, HEADS
from .dice import DiceTray
from .spinner import WheelWidget
from .stage import Stage

if TYPE_CHECKING:
    from src.main import Client


class RandomChancePlugin(Plugin):

    KEY = "randomchance"

    # How far the coin travels upward, as a fraction of the coin rather than
    # of the screen, so the arc stays in proportion to it.
    ARC_FRACTION = 0.55
    # How much of the screen the dice land in.
    TRAY_FRACTION = 0.72

    def __init__(self):
        self.stage = None
        self.skills = []
        self.wheels = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def load(self, carryover=None):
        self.stage = Stage(self.client)

        # User data, not the plugin's own folder. settings.json ships with
        # the app and an update replaces it, which would take somebody's
        # wheels with it - the same reason widget layout lives out here.
        self.wheels = wheels_module.WheelStore(
            get_data_dir(APP_NAME) / "randomchance" / "wheels.json",
            log=self.client.log)

        # Declared before anything can subscribe, and only once:
        # create_on_call_event() resets the list, so calling it on a reload
        # would drop every subscriber another plugin had registered.
        for name in ("on_coin_flip", "on_dice_roll", "on_wheel_spin"):
            if name not in self.client.EVENTS["on_call"]:
                self.client.create_on_call_event(name)

        self.client.public.expose(self.KEY, "randomchance", {
            "flip": self.flip,
            "roll": self.roll,
            "spin": self.spin,
            "wheels": self.wheels,
        })

        self.client.API.register(
            self.KEY, "randomchance_page", self.api_page,
            requires_auth=True, gui="Random Chance", icon="mdi.dice-multiple",
            description="Flip a coin or roll dice on the panel, from a phone.")

        self.skills = self._skills()
        self.client.SKILLS.register(self.KEY, self.skills)

    def unload(self, carryover=None):
        self.client.SKILLS.un_register(self.KEY)
        self.wheels = None
        self.client.API.unregister(self.KEY)
        if self.stage is not None:
            # On the UI thread: an unload triggered from the settings page is
            # already there, one from a reload may not be.
            self.client.call_on_ui(self.stage.dismiss)
        self.stage = None
        self.skills = []

    # ── Settings ─────────────────────────────────────────────────────────────

    def _hold_settings(self, stage) -> None:
        """
        Hand the stage the reading-time rule, before every showing.

        Read here rather than at load, so changing it in Settings takes
        effect on the next flip instead of after a restart.
        """
        stage.configure_hold(
            base=self._setting("stage.result_ms", stage.HOLD_BASE_MS),
            per_item=self._setting("stage.result_per_item_ms",
                                   stage.HOLD_PER_ITEM_MS),
            maximum=self._setting("stage.result_max_ms", stage.HOLD_MAX_MS))

    def _setting(self, dotted: str, default=None):
        """One of this plugin's own settings, or the default if it is absent."""
        node = getattr(self, "settings", None)
        try:
            for part in dotted.split("."):
                node = getattr(node, part)
            return node.value
        except Exception:
            return default

    # ── The capability ───────────────────────────────────────────────────────

    def flip(self, title: str = "", heads: str = "", tails: str = "",
             announce: bool = True) -> str:
        """
        Flip a coin. Returns the outcome immediately.

        `heads` and `tails` name what the two sides stand for - "Colin" and
        "Chris" rather than heads and tails. The coin itself is unchanged
        either way: the label belongs on the banner, where there is room to
        read it, not on a disc that spends most of the flip edge-on.

        The return value is the raw face, always `heads` or `tails`, so a
        caller comparing outcomes does not have to know what they were called
        this time. `on_coin_flip` carries both.
        """
        result = coin_module.decide()
        heads_label = str(heads).strip() or "Heads"
        tails_label = str(tails).strip() or "Tails"
        spoken = heads_label if result == HEADS else tails_label

        say = announce and self._setting("speech.speak_result", True)
        self.client.call_on_ui(
            lambda: self._show(result, title, spoken, say))

        self.client.log("info", f"[RandomChance] Coin: {result} ({spoken}).")
        self._remember("mdi.circle-multiple",
                       str(title).strip() or "Coin flip", spoken)
        self.client.iterate_event_callables("on_coin_flip", {
            "result": result,
            "label": spoken,
            "heads": heads_label,
            "tails": tails_label,
            "title": str(title or ""),
        })
        return result

    def _show(self, result: str, title: str, label: str,
              say: bool = False) -> None:
        """The drawing half, on the UI thread."""
        stage = self.stage
        if stage is None:
            # Nothing to watch, so nothing to wait for.
            if say:
                self.client.say(label)
            return

        diameter = stage.content_size()
        widget = CoinWidget(diameter, int(diameter * self.ARC_FRACTION))

        self._hold_settings(stage)
        animate = bool(self._setting("animation.enabled", True))
        flip_ms = int(self._setting("animation.flip_ms", 2200))
        frame_ms = int(self._setting("animation.frame_ms", 33))

        stage.present(
            content=widget,
            start=lambda done: widget.start(result,
                                            duration_ms=flip_ms,
                                            frame_ms=frame_ms,
                                            animate=animate,
                                            on_settled=done),
            result=label,
            title=str(title or ""),
            title_ms=int(self._setting("stage.title_ms", 1400)),
            # One coin is one thing to read.
            items=1,
            # Said when the coin lands, not when the face is decided. The
            # result exists before the animation starts, so announcing it
            # there has the panel calling the flip mid-air.
            on_result=(lambda: self.client.say(label)) if say else None,
        )

    # ── Dice ─────────────────────────────────────────────────────────────────

    def roll(self, spec: str = "", title: str = "", groups: list = None,
             outcomes: list = None, announce: bool = True) -> dict:
        """
        Roll dice. Returns what they showed, immediately.

        `spec` is what somebody said - "2d20 and a d10", "three six sided
        dice", or nothing at all. `groups` takes the same thing already
        parsed, as `[(count, sides), ...]`, for a caller that has its own
        idea of what to roll and does not want it read out of English.

        `outcomes` reads the total: a list of `{"op", "value", "text"}` where
        `op` is `greater` or `less`. The first rule the total satisfies is
        the one shown, so the list is a sequence rather than a set - order is
        priority. A threshold these dice cannot reach is dropped, because a
        rule that holds on every roll kills every rule under it.

        Nothing here declines. A bare "roll the dice" is a complete request,
        so an unparseable one falls back to a random standard die rather than
        being handed to the fallback, which cannot roll anything.
        """
        wanted = dice_module.plan(spec, groups)
        rules = dice_module.clean_outcomes(
            outcomes, span=dice_module.totals_range(wanted))

        rolled = dice_module.roll_groups(wanted)
        total = sum(value for _sides, value in rolled)
        detail = dice_module.describe(rolled)
        achieved = dice_module.match_outcome(total, rules)

        say = announce and self._setting("speech.speak_result", True)
        self.client.call_on_ui(
            lambda: self._show_dice(rolled, str(total), detail, title,
                                    achieved["text"] if achieved else "", say))

        summary = ", ".join(f"{count}d{sides}" for count, sides in wanted)
        self.client.log("info",
                        f"[RandomChance] Rolled {summary}: {total}"
                        f"{' (' + detail + ')' if detail else ''}.")

        self._remember("mdi.dice-multiple",
                       str(title).strip() or f"Rolled {summary}",
                       f"{total}" + (f" - {detail}" if detail else "")
                       + (f" - {achieved['text']}" if achieved else ""))

        outcome = {
            "total": total,
            "rolls": [{"sides": sides, "value": value}
                      for sides, value in rolled],
            "groups": [{"count": count, "sides": sides}
                       for count, sides in wanted],
            "detail": detail,
            "title": str(title or ""),
            "outcome": achieved,
        }
        self.client.iterate_event_callables("on_dice_roll", outcome)
        return outcome

    def _show_dice(self, rolled: list, total: str, detail: str,
                   title: str, epilogue: str = "", say: bool = False) -> None:
        """The drawing half, on the UI thread."""
        stage = self.stage
        if stage is None:
            if say:
                self.client.say(total)
            return

        width, height = stage.surface()
        tray = DiceTray(rolled,
                        int(width * self.TRAY_FRACTION),
                        int(height * self.TRAY_FRACTION))

        self._hold_settings(stage)
        animate = bool(self._setting("animation.enabled", True))
        roll_ms = int(self._setting("animation.roll_ms", 1600))
        frame_ms = int(self._setting("animation.frame_ms", 33))
        tray.collide = bool(self._setting("animation.dice_collide", True))

        stage.present(
            content=tray,
            start=lambda done: tray.start(duration_ms=roll_ms,
                                          frame_ms=frame_ms,
                                          animate=animate,
                                          on_settled=done),
            result=total,
            detail=detail,
            title=str(title or ""),
            title_ms=int(self._setting("stage.title_ms", 1400)),
            # Every die is something somebody may want to find in the
            # breakdown, so the answer waits for as many as there are.
            items=len(rolled),
            epilogue=epilogue,
            epilogue_ms=int(self._setting("stage.outcome_ms", 2600)),
            # Said when the last die settles - see the coin above.
            on_result=(lambda: self.client.say(total)) if say else None,
        )

    # ── The wheel ────────────────────────────────────────────────────────────

    # How much of the screen the wheel takes. Larger than the coin, because a
    # slice has a name written along it and the coin has nothing on it.
    WHEEL_FRACTION = 0.62
    WHEEL_MIN = 200
    WHEEL_MAX = 560

    def spin(self, wheel_id: str = "", items: list = None, title: str = "",
             announce: bool = True) -> dict:
        """
        Spin a wheel. Returns who won, immediately.

        `wheel_id` names a saved wheel; `items` takes a list outright, for a
        caller with its own idea of what to spin over. Either way the winner
        is chosen from the shares before anything turns, and the rotation is
        worked out to land on it.

        Disabled items are gone before this point, and an item on 0% cannot
        be landed on - which is what setting it to zero means.
        """
        wheel = None
        if items is None and wheel_id and self.wheels is not None:
            wheel = self.wheels.get(wheel_id)
            items = (wheel or {}).get("items") or []
        heading = str(title or "").strip() or str((wheel or {}).get("name", ""))

        live = wheels_module.normalise(items or [])
        for item in live:
            item["hue"] = wheels_module.hue_for(item["label"])
            item["tone"] = wheels_module.tone_for(item["label"])

        winner = wheels_module.pick(items or [])
        if winner is None:
            self.client.log("warning",
                            "[RandomChance] Nothing to spin - every item is "
                            "off, or the wheel is empty.")
            return {"winner": None, "items": live, "title": heading}

        index = next((position for position, item in enumerate(live)
                      if item["id"] == winner["id"]), 0)
        label = winner["label"]

        say = announce and self._setting("speech.speak_result", True)
        self.client.call_on_ui(
            lambda: self._show_wheel(live, index, label, heading, say))

        self.client.log("info",
                        f"[RandomChance] Wheel"
                        f"{' ' + heading if heading else ''}: {label} "
                        f"at {winner['share']}%.")
        self._remember("mdi.chart-pie", heading or "Wheel",
                       f"{label} - {winner['share']}%")

        outcome = {
            "winner": {"label": label, "share": winner["share"],
                       "id": winner["id"]},
            "items": live,
            "title": heading,
        }
        self.client.iterate_event_callables("on_wheel_spin", outcome)
        return outcome

    def _show_wheel(self, live: list, index: int, label: str, title: str,
                    say: bool = False) -> None:
        """The drawing half, on the UI thread."""
        stage = self.stage
        if stage is None:
            if say:
                self.client.say(label)
            return

        width, height = stage.surface()
        size = int(max(self.WHEEL_MIN,
                       min(self.WHEEL_MAX,
                           min(width, height) * self.WHEEL_FRACTION)))
        widget = WheelWidget(live, size)

        self._hold_settings(stage)
        animate = bool(self._setting("animation.enabled", True))
        spin_ms = int(self._setting("animation.spin_ms", 3600))
        frame_ms = int(self._setting("animation.frame_ms", 33))

        stage.present(
            content=widget,
            start=lambda done: widget.start(index,
                                            duration_ms=spin_ms,
                                            frame_ms=frame_ms,
                                            animate=animate,
                                            on_settled=done),
            result=label,
            title=title,
            title_ms=int(self._setting("stage.title_ms", 1400)),
            # A wheel is one name to read however many slices it had, so it
            # does not scale - but it earns a longer look than a coin does,
            # because everybody watching wants a moment on the wheel itself
            # once it has stopped.
            items=1,
            hold_bonus=int(self._setting("stage.wheel_bonus_ms", 1600)),
            on_result=(lambda: self.client.say(label)) if say else None,
        )

    # ── Recording what happened ──────────────────────────────────────────────

    def _remember(self, icon: str, title: str, body: str) -> None:
        """
        Put a result in the notification history, without a toast.

        Written straight to the history rather than through `simple_notify`,
        which always queues a toast as well. The result is already on screen
        in a banner the size of a fist - a toast beside it is the same answer
        twice, and the documentation is explicit that a notification is the
        wrong shape for something somebody asked for and is watching.

        What is wanted is the record: a flip that settled an argument is worth
        finding again an hour later, and the banner is gone in three seconds.
        """
        if not self._setting("notify.remember_results", True):
            return
        public = getattr(self.client, "public", None)
        if public is None or not public.has("notification_history"):
            return
        try:
            public.notification_history.add(icon, title, body, datetime.now())
        except Exception as e:
            self.client.log("debug",
                            f"[RandomChance] Could not record '{title}': {e}")

    # ── The page ─────────────────────────────────────────────────────────────

    PATH = "/public/randomchance_page"

    def api_page(self, what: str = "", title: str = "", heads: str = "",
                 tails: str = "", groups: str = "", outcomes: str = "",
                 wheel: str = "", wheel_id: str = "", fmt: str = "",
                 **_ignored):
        """
        The page, and the thing it asked for.

        One endpoint for both: a GET renders the form and a POST does the
        work. Two endpoints would mean the form posting somewhere that has to
        know how to send you back, and there is nothing here worth that.

        `fmt=json` answers with the message alone, which is what the page
        posts. The dice and the outcome rules live in arrays in the browser
        rather than in form fields, so a reply that replaced the document
        would throw away everything picked - and on a phone that means
        re-tapping a handful of dice to roll the same thing twice.

        The page module is loaded late through `sibling()`, and is handed
        what it needs rather than importing it. A module loaded that way has
        no package to be relative to - see the note at the top of
        webpage.py - so it knows about HTML and nothing else.
        """
        web = self.sibling("webpage")

        token = ""
        try:
            from flask import request as _request
            token = (_request.args.get("token")
                     or _request.headers.get("X-Client-Token") or "")
        except Exception:
            pass

        message, bad = "", False
        wanted = str(what or "").strip().lower()

        if wanted == "coin":
            # The answer is deliberately not in here. The panel is where it
            # is shown, and a phone that already knows how it landed while
            # the coin is still turning has spoiled the only interesting
            # second of it.
            self.flip(title=title, heads=heads, tails=tails)
            message = "Flipping the coin."
        elif wanted == "dice":
            picked = dice_module.groups_from_json(groups)
            if not picked:
                message, bad = "Pick at least one die first.", True
            else:
                self.roll(title=title, groups=picked,
                          outcomes=dice_module.outcomes_from_json(outcomes))
                # No total here either - see the coin above.
                message = "Rolling."
        elif wanted.startswith("wheel_"):
            message, bad = self._wheel_action(wanted, wheel, wheel_id)

        if str(fmt).strip().lower() == "json":
            answer = {"message": message, "bad": bad}
            if wanted.startswith("wheel_"):
                # The page rebuilds its picker from this rather than asking
                # again - saving a wheel and then fetching the list back is
                # two chances for one of them to be the one that fails.
                answer["wheels"] = self._saved_wheels()
            return answer

        html = web.render(token, self.PATH,
                          die_types=dice_module.STANDARD,
                          max_dice=dice_module.MAX_DICE,
                          wheels=self._saved_wheels(),
                          message=message, bad=bad)
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    def _saved_wheels(self) -> list:
        return self.wheels.all() if self.wheels is not None else []

    def _wheel_action(self, wanted: str, wheel: str, wheel_id: str) -> tuple:
        """
        Save, delete, or spin the wheel the page is holding.

        Spinning **saves first**. The wheel on screen is what somebody just
        edited, and spinning a different one than the one in front of them -
        or losing the edit because they spun instead of saving - are both
        worse than a write nobody asked for.
        """
        if self.wheels is None:
            return "Wheels are not available.", True

        if wanted == "wheel_delete":
            if self.wheels.drop(str(wheel_id)):
                return "Wheel deleted.", False
            return "That wheel is not here.", True

        kept = self.wheels.put(wheel)
        if kept is None:
            return "That wheel could not be saved.", True

        if wanted == "wheel_save":
            return f"Saved {kept['name']}.", False

        if wanted != "wheel_spin":
            return "", False

        live = wheels_module.normalise(kept["items"])
        if len(live) < 2:
            return ("A wheel needs at least two items turned on to be worth "
                    "spinning."), True

        # The winner is deliberately not in the reply - see the coin.
        self.spin(wheel_id=kept["id"])
        return "Spinning the wheel.", False

    # ── Skills ───────────────────────────────────────────────────────────────

    def _skills(self) -> list:
        wake = self.client.wake_word
        return [
            Skill(
                wake_word=wake, skill_key="coin-flip", kind="act",
                plugin_key=self.KEY,
                # More than one phrasing on purpose. A missing phrase is the
                # routing failure this panel has had most often, and "flip a
                # coin" is only the shortest of several things people say.
                examples=[
                    "flip a coin",
                    "flip the coin",
                    "flip a coin for me",
                    "toss a coin",
                    "toss the coin",
                    "coin flip",
                    "coin toss",
                    "heads or tails",
                    "give me a coin flip",
                    "do a coin toss",
                ],
                # "coin" means this and nothing else on the panel. A question
                # ABOUT a coin is an `ask`, and the two tracks cannot reach
                # each other, so owning the word cannot swallow one.
                owns=["coin"],
                func=self.skill_flip,
            ),
            Skill(
                wake_word=wake, skill_key="dice-roll", kind="act",
                plugin_key=self.KEY,
                examples=[
                    "roll a d20",
                    "roll the dice",
                    "roll dice",
                    "roll a die",
                    "roll 2d6",
                    "roll 3 d 4",
                    "roll some dice",
                    "throw the dice",
                    "give me a dice roll",
                    "roll a six sided die",
                    "roller d 20",
                ],
                # The notation is a payload rather than an argument. A phrase
                # can carry more than one group - "2d20 and a d10" - and an
                # argument takes the single widest span, which would silently
                # drop the rest. A payload also keeps the value out of the
                # score, so "roll 9 d 100s and 2 d 20s" ranks exactly as well
                # as "roll".
                # "roller" is not a word anybody says - it is what the
                # transcriber writes for "roll a", and fuzzy repair cannot
                # reach it: `fuzzy_equal` refuses one word being a prefix of
                # another, which "roll" and "roller" are. So it is listed.
                payload={"spec": ["roll a", "roll the", "roll some",
                                  "throw a", "throw the", "throw some",
                                  "roller", "rolla",
                                  "roll", "throw"]},
                owns=["dice"],
                # The handler needs the whole utterance, not only what came
                # after the anchor - deciding whether "roll" meant dice is a
                # question about the rest of the sentence.
                wants_phrase=True,
                func=self.skill_roll,
            ),
        ]

    def skill_flip(self) -> None:
        """
        Flip, and let the banner be the answer.

        No answer panel: the result banner is already the thing on screen
        saying what happened, and a panel over the top of it would cover the
        coin it is reporting on.
        """
        self.flip()

    def skill_roll(self, spec: str = "", phrase: str = "") -> None:
        """
        Roll whatever was named, or a die at random if nothing was.

        `spec` arrives verbatim - a payload is taken as said rather than
        trimmed - so "2 d 20 s and a d 10" reaches the parser intact.

        A phrase that carried "roll" without being about dice is declined and
        carries on to the next skill: "put on some rock and roll" is a music
        request that happens to contain this skill's anchor, and answering it
        with a d6 would be worse than not answering it at all.
        """
        if not dice_module.about_dice(phrase, spec):
            raise SkillDeclined(f"{phrase!r} carries 'roll' but names no dice")
        self.roll(spec)
