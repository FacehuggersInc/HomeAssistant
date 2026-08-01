"""
Turning what came back into how the tile looks.

A list of rules, tried in order. The first whose condition holds decides the
tile's icon, its colours and its name; anything after it is not consulted.
Order is the whole of the logic, which is why a rule can be moved rather than
edited into place.

No Qt. What a rule matches and which rule wins are the parts worth testing,
and both are answerable from a value and a list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .action_runner import follow, as_state


#What a rule compares with. Deliberately few, and all of them things somebody
#can decide by looking at a value they have just seen come back.
TESTS = [
    ("on", "is on"),
    ("off", "is off"),
    ("equals", "is exactly"),
    ("contains", "contains"),
    ("above", "is more than"),
    ("below", "is less than"),
    ("missing", "is not there"),
    ("present", "is there"),
    ("always", "anything"),
]

TEST_LABELS = dict(TESTS)


def _number(value: Any):
    """A value as a number, or None if it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        text = str(value).strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


@dataclass
class Rule:
    """One condition, and how the tile looks when it holds."""

    #Where in the answer to look. Empty means the whole thing.
    path: str = ""
    test: str = "always"
    #What to compare with, for the tests that need one.
    against: str = ""

    #How the tile looks when this rule wins. Empty means "leave it alone",
    #so a rule can change only the colour without restating everything.
    label: str = ""
    icon: str = ""
    ink: str = ""            # the icon's colour
    background: str = ""
    border: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "test": self.test, "against": self.against,
                "label": self.label, "icon": self.icon, "ink": self.ink,
                "background": self.background, "border": self.border}

    @classmethod
    def from_dict(cls, raw: dict) -> "Rule":
        allowed = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in (raw or {}).items() if k in allowed}
        return cls(**clean)

    ## -- matching

    def matches(self, answer: Any) -> bool:
        """Whether this rule holds for one answer."""
        if self.test == "always":
            return True

        found, value = follow(answer, self.path)
        if self.test == "missing":
            return not found
        if self.test == "present":
            return found
        if not found:
            # Every other test is about a value, and there isn't one. A rule
            # asking whether a missing field is off should say `is not there`
            # rather than quietly counting as off.
            return False

        if self.test == "on":
            return as_state(value)
        if self.test == "off":
            return not as_state(value)

        if self.test == "equals":
            return str(value).strip().lower() == str(self.against).strip().lower()
        if self.test == "contains":
            return str(self.against).strip().lower() in str(value).lower()

        if self.test in ("above", "below"):
            left, right = _number(value), _number(self.against)
            if left is None or right is None:
                return False
            return left > right if self.test == "above" else left < right
        return False

    def describe(self) -> str:
        """The rule in words, for a list to show."""
        where = self.path or "the answer"
        verb = TEST_LABELS.get(self.test, self.test)
        if self.test in ("equals", "contains", "above", "below"):
            return f"{where} {verb} {self.against}"
        if self.test == "always":
            return "anything else"
        return f"{where} {verb}"


#What a label may put the reading into. `{value}` is the one worth having:
#a tile saying "22C" is worth more than one saying "Warm", and without this a
#rule can only choose between fixed words.
VALUE_TOKEN = "{value}"


def fill(text: str, value: Any) -> str:
    """
    Put the reading into a label.

    Only when it is asked for. A label with no token is left exactly as it
    was, so nothing has to be escaped and a rule that wants fixed words gets
    fixed words.
    """
    if not text or VALUE_TOKEN not in text:
        return text or ""
    if value is None:
        shown = ""
    elif isinstance(value, bool):
        shown = "yes" if value else "no"
    elif isinstance(value, float):
        # Trailing zeroes are noise on a tile. 22.0 is 22.
        shown = f"{value:g}"
    elif isinstance(value, (dict, list, tuple)):
        # A whole object on a tile is a smear. Its size is the useful part.
        shown = str(len(value))
    else:
        shown = str(value)
    return text.replace(VALUE_TOKEN, shown)


@dataclass
class Look:
    """How the tile should be drawn, once the rules have been through."""

    label: str = ""
    icon: str = ""
    ink: str = ""
    background: str = ""
    border: str = ""
    #Which rule decided this, so a dialog can say so. -1 means none did.
    rule: int = -1

    def to_dict(self) -> dict:
        return {"label": self.label, "icon": self.icon, "ink": self.ink,
                "background": self.background, "border": self.border}


def decide(answer: Any, rules: list, fallback: "Look" = None) -> Look:
    """
    The first rule that holds, over the tile's own look.

    Ordered, not scored. A rule that comes first wins outright, which is the
    only arrangement somebody can reason about without reading all of them -
    and it means "anything else" is a rule at the bottom rather than a special
    case somewhere in the code.

    A field a rule leaves empty falls back to the tile's own, so a rule that
    only changes the colour does not have to restate the icon and the name.
    """
    base = fallback or Look()
    for index, raw in enumerate(rules or []):
        rule = raw if isinstance(raw, Rule) else Rule.from_dict(raw)
        try:
            if not rule.matches(answer):
                continue
        except Exception:
            # A rule that cannot be judged is not a rule that wins.
            continue
        # The label may ask for the reading. Taken from the rule's own path,
        # so a rule watching `today.high` shows that rather than whatever the
        # tile happens to be reading elsewhere.
        _found, value = follow(answer, rule.path)
        return Look(
            label=fill(rule.label, value) or base.label,
            icon=rule.icon or base.icon,
            ink=rule.ink or base.ink,
            background=rule.background or base.background,
            border=rule.border or base.border,
            rule=index,
        )
    return Look(label=base.label, icon=base.icon, ink=base.ink,
                background=base.background, border=base.border, rule=-1)


def suggest(answer: Any, path: str = "") -> list:
    """
    A pair of rules to start from, given what came back.

    Offered rather than made: two rules covering on and off is what almost
    everybody wants first, and building that by hand from an empty list is
    four dialogs before anything happens.
    """
    found, value = follow(answer, path)
    if not found:
        # Nothing there to read now, but a rule for that is still worth
        # offering - it is the state somebody most wants to see.
        return [Rule(path=path, test="missing", label="Missing",
                     icon="mdi.help-circle-outline", ink="#e8c35a",
                     border="#e8c35a")]

    if isinstance(value, bool) or as_state(value) in (True, False):
        return [
            Rule(path=path, test="on", label="On", ink="#3ec08a",
                 border="#3ec08a"),
            Rule(path=path, test="off", label="Off",
                 ink="rgba(232,236,244,90)"),
            # A third state, not a special case of off. A field that is not
            # there is a different thing from one that is there and false -
            # usually it means whatever answers this has stopped answering.
            Rule(path=path, test="missing", label="Missing",
                 icon="mdi.help-circle-outline", ink="#e8c35a",
                 border="#e8c35a"),
        ]
    return []
