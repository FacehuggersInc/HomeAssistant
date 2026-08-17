"""
Dice, drawn - and the notation people actually say out loud.

Same contract as the coin: every value is decided before anything is drawn,
and the animation is choreographed to arrive at values it was handed. A die
that tumbled and settled on whatever it happened to land on would be a
simulation whose fairness depended on its physics, and it would make the
result unavailable until the drawing finished.

Colours belong to the TYPE, not to the die. Nine d100s drawn in nine colours
are nine things nobody can name; one colour per type means the purple ones
are the d20s at a glance, which is the reading that matters when a handful
lands at once.
"""

from __future__ import annotations

import json
import math
import random
import re
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (QPainter, QPainterPath, QColor, QPen, QFont,
                         QFontMetrics)
from PyQt6.QtWidgets import QWidget

_rng = random.SystemRandom()

# What "roll the dice" picks from when nothing was named.
STANDARD = (4, 6, 8, 10, 12, 20, 100)

# Sanity bounds. A die with one side is not a die, and nobody needs a d10000
# except to find out what happens.
MIN_SIDES = 2
MAX_SIDES = 1000
MAX_DICE = 60



class _Look:
    """Border, fill and number for one type of die."""

    def __init__(self, border: str, fill: str, number: str, shape: str):
        self.border = QColor(border)
        self.fill = QColor(fill)
        self.number = QColor(number)
        self.shape = shape


# The shape names are the ones the dice themselves suggest - a d4 is a
# triangle from any angle, a d20 reads as a hexagon in outline. Anything not
# in this table is a circle, which says "a die with a number on it" without
# claiming to be a solid that exists.
LOOKS = {
    4:   _Look("#c0392b", "#e8604f", "#3c1310", "triangle"),
    6:   _Look("#1f6fb2", "#4a9fe0", "#0d2a44", "square"),
    8:   _Look("#1e8a5f", "#3fc78d", "#0a2f20", "rhombus"),
    10:  _Look("#7a4bb5", "#a97ee0", "#2a1442", "kite"),
    12:  _Look("#c2701a", "#f0a04b", "#3f2408", "pentagon"),
    20:  _Look("#127f86", "#2fc3cc", "#062e31", "hexagon"),
    100: _Look("#b03070", "#e069a5", "#3d0f26", "decagon"),
}
# Colours worked out for a die with no entry above, so a d5 and a d7 are not
# the same grey. Golden-ratio hue stepping keeps consecutive sizes far apart
# on the wheel, and it is a function of the number of sides - so a d7 is the
# same colour every time it is rolled, which is the whole point of colouring
# by type.
_GENERATED: dict = {}
_HUE_STEP = 0.6180339887


def look_for(sides: int) -> _Look:
    sides = int(sides)
    known = LOOKS.get(sides)
    if known is not None:
        return known
    if sides not in _GENERATED:
        hue = int(((sides * _HUE_STEP) % 1.0) * 360)
        _GENERATED[sides] = _Look(
            QColor.fromHsl(hue, 150, 92).name(),
            QColor.fromHsl(hue, 165, 150).name(),
            QColor.fromHsl(hue, 140, 38).name(),
            shape_for(sides)[0])
    return _GENERATED[sides]


def shape_for(sides: int) -> tuple:
    """
    The solid to draw for a die, as `(shape, corners)`.

    A die not in the table still gets a shape of its own where one exists: a
    d5 is a pentagon and a d7 a heptagon, which says more about what was
    rolled than a circle does. Only past a dozen sides does a polygon stop
    being countable at a glance, and then a circle is the honest answer.
    """
    sides = int(sides)
    known = LOOKS.get(sides)
    if known is not None:
        return known.shape, 0
    if 3 <= sides <= 12:
        return "ngon", sides
    return "circle", 0


def _polygon(corners: int, half: float, turn: float = -math.pi / 2) -> list:
    return [(math.cos(turn + i * 2 * math.pi / corners) * half,
             math.sin(turn + i * 2 * math.pi / corners) * half)
            for i in range(corners)]


def _closed(points: list) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    path.closeSubpath()
    return path


def _lines(segments: list) -> QPainterPath:
    path = QPainterPath()
    for start, end in segments:
        path.moveTo(QPointF(*start))
        path.lineTo(QPointF(*end))
    return path


class Geometry:
    """
    One die, as the three things that make it read as a solid.

    A polygon with a number in it is a coloured badge. What makes a d20 look
    like a d20 is that the shape you see and the shape you READ are different
    ones: the silhouette is a hexagon, the face carrying the number is a
    triangle in the middle of it, and the edges running between them are the
    other faces turned away.

    So the number is fitted to the FACE rather than to the die. Sizing it to
    the die is what made every shape look alike - the digits grew until they
    covered the facets, and all that was left to tell them apart was the
    outline and the colour.
    """

    def __init__(self, outline: QPainterPath, face: QPainterPath = None,
                 facets: QPainterPath = None, text_width: float = 0.55,
                 text_offset: tuple = (0.0, 0.0), pips: bool = False):
        self.outline = outline
        self.face = face
        self.facets = facets if facets is not None else QPainterPath()
        self.text_width = text_width
        self.text_offset = text_offset
        self.pips = pips


def geometry_for(shape: str, size: float, corners: int = 0) -> Geometry:
    """Everything needed to draw one die of this shape, at this size."""
    half = size / 2.0

    if shape == "square":
        # A cube, and the only die most people can picture without being
        # told: pips rather than a numeral, because that is what says die
        # more than any outline does.
        radius = size * 0.16
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(-half, -half, size, size),
                               radius, radius)
        return Geometry(outline, pips=True, text_width=0.42)

    if shape == "circle":
        # A d100 and anything past a dozen sides. Drawn as the rounded solid
        # it is, with a face panel so the number still sits on something.
        outline = QPainterPath()
        outline.addEllipse(QRectF(-half, -half, size, size))
        face = QPainterPath()
        face.addEllipse(QRectF(-half * 0.62, -half * 0.62,
                               half * 1.24, half * 1.24))
        ticks = []
        for x, y in _polygon(12, half * 0.98):
            ticks.append(((x * 0.66, y * 0.66), (x, y)))
        return Geometry(outline, face, _lines(ticks), text_width=0.44)

    if shape == "triangle":
        # A tetrahedron: the face you read is the inverted triangle in the
        # middle, and the three around it are the faces folded away.
        outer = [(0, -half * 1.05), (half, half * 0.72), (-half, half * 0.72)]
        inner = [((outer[i][0] + outer[(i + 1) % 3][0]) / 2,
                  (outer[i][1] + outer[(i + 1) % 3][1]) / 2)
                 for i in range(3)]
        return Geometry(_closed(outer), _closed(inner),
                        text_width=0.38, text_offset=(0.0, 0.06))

    if shape == "rhombus":
        # An octahedron on a vertex: a diamond, split across the middle, with
        # the two faces above it meeting on the centre line.
        outer = [(0, -half), (half * 0.88, 0), (0, half), (-half * 0.88, 0)]
        face = [(-half * 0.88, 0), (half * 0.88, 0), (0, half)]
        facets = [((0, -half), (0, 0)),
                  ((-half * 0.88, 0), (half * 0.88, 0))]
        return Geometry(_closed(outer), _closed(face), _lines(facets),
                        text_width=0.40, text_offset=(0.0, 0.20))

    if shape == "kite":
        # A pentagonal trapezohedron: six sides in outline, and the face is
        # the long kite down the middle of it.
        outer = [(0, -half), (half * 0.68, -half * 0.34),
                 (half * 0.68, half * 0.18), (0, half),
                 (-half * 0.68, half * 0.18), (-half * 0.68, -half * 0.34)]
        face = [(0, -half * 0.82), (half * 0.40, -half * 0.06),
                (0, half * 0.72), (-half * 0.40, -half * 0.06)]
        facets = [(outer[1], face[1]), (outer[5], face[3]),
                  (outer[2], face[1]), (outer[4], face[3])]
        return Geometry(_closed(outer), _closed(face), _lines(facets),
                        text_width=0.34)

    if shape == "hexagon":
        # An icosahedron seen face on. The silhouette really is a hexagon and
        # the face really is a triangle in the centre of it - drawing the
        # hexagon alone is why it looked like nothing in particular.
        outer = _polygon(6, half, turn=-math.pi / 2)
        face = _polygon(3, half * 0.58, turn=math.pi / 2)
        # One edge from each corner of the face to the corner of the
        # silhouette it points at - three lines, not six. Running them to
        # every corner drew the two sets crossing each other, which is not an
        # edge of anything.
        facets = [(face[index], outer[(3 + index * 2) % 6])
                  for index in range(3)]
        return Geometry(_closed(outer), _closed(face), _lines(facets),
                        text_width=0.36, text_offset=(0.0, -0.05))

    if shape == "pentagon":
        # A dodecahedron: ten sides in outline, a pentagon face in the middle,
        # and the five faces between them.
        outer = _polygon(10, half, turn=-math.pi / 2)
        face = _polygon(5, half * 0.50, turn=-math.pi / 2)
        facets = [(face[index], outer[(index * 2) % 10]) for index in range(5)]
        return Geometry(_closed(outer), _closed(face), _lines(facets),
                        text_width=0.34)

    if shape == "decagon":
        # A d100 in a set that has one: a many-sided ball with a flat panel.
        outer = _polygon(10, half, turn=-math.pi / 2)
        face = _polygon(10, half * 0.58, turn=-math.pi / 2)
        facets = [(face[index], outer[index]) for index in range(10)]
        return Geometry(_closed(outer), _closed(face), _lines(facets),
                        text_width=0.42)

    # Any polygon drawn for a size with no entry of its own.
    count = corners or 5
    outer = _polygon(count, half, turn=-math.pi / 2)
    face = _polygon(count, half * 0.54, turn=-math.pi / 2 + math.pi / count)
    facets = [(face[index], outer[index]) for index in range(count)]
    return Geometry(_closed(outer), _closed(face), _lines(facets),
                    text_width=0.38)


# Where the pips go on a d6, in units of the die's half-width. The layouts
# are the ones on a real die, so a 3 reads as a 3 without being counted.
_PIPS = {
    1: [(0, 0)],
    2: [(-0.45, -0.45), (0.45, 0.45)],
    3: [(-0.45, -0.45), (0, 0), (0.45, 0.45)],
    4: [(-0.45, -0.45), (0.45, -0.45), (-0.45, 0.45), (0.45, 0.45)],
    5: [(-0.45, -0.45), (0.45, -0.45), (0, 0), (-0.45, 0.45), (0.45, 0.45)],
    6: [(-0.45, -0.45), (0.45, -0.45), (-0.45, 0), (0.45, 0),
        (-0.45, 0.45), (0.45, 0.45)],
}


def pip_path(value: int, size: float) -> QPainterPath:
    """The pips for one face of a d6, or an empty path if it has none."""
    path = QPainterPath()
    half = size / 2.0
    radius = size * 0.088
    for x, y in _PIPS.get(int(value), []):
        path.addEllipse(QPointF(x * half, y * half), radius, radius)
    return path


# ── Saying it ────────────────────────────────────────────────────────────────

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}


def _digits(text: str) -> str:
    """
    Spoken numbers as digits, including the two-word ones.

    Transcript normalisation converts most of these already and is not a
    guarantee, so this covers the rest rather than assuming.
    """
    words = text.split()
    out: list = []
    index = 0
    while index < len(words):
        word = words[index]
        value = _NUMBER_WORDS.get(word)
        if value is None:
            out.append(word)
            index += 1
            continue
        # "twenty five" is one number; "twenty d six" is not.
        if 20 <= value <= 90 and index + 1 < len(words):
            second = _NUMBER_WORDS.get(words[index + 1])
            if second is not None and 1 <= second <= 9:
                out.append(str(value + second))
                index += 2
                continue
        out.append(str(value))
        index += 1
    return " ".join(out)


#   3d6 / 3 d 6 / d20 / 2 d20s
#
# No `\b` around the notation. A word boundary before the `d` refuses "3d4",
# where a digit runs straight into it, and one after the number refuses
# "9 d 100s" and "2 d20s" - and a plural is how people say it out loud. A
# lookbehind for a letter does the one thing the boundary was wanted for:
# it keeps the `d` in "and" from starting a die.
_NOTATION = re.compile(r"(?:(\d+)\s*)?(?<![a-z])d\s*(\d+)")
#   20 sided die / a 6 sided dice
_SIDED = re.compile(r"(?:(\d+)\s+)?(\d+)\s*[- ]?\s*sided")


def parse_roll(text: str) -> list:
    """
    Every dice group in a phrase, as `(count, sides)` pairs.

    Returns an empty list when nothing was named, which the caller reads as
    "roll the dice" rather than as a failure - a bare request is a valid
    request, and declining it would send it to the fallback to be answered by
    something that cannot roll anything.

    A phrase carries several groups often enough to be worth handling:
    "2d20 and 1d10" is two, and matching a single widest span - which is what
    `arguments` would do - would take one of them and silently lose the other.
    """
    cleaned = re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())
    cleaned = re.sub(r"\s+", " ", _digits(cleaned)).strip()
    if not cleaned:
        return []

    groups = []
    seen_spans = []

    for match in _NOTATION.finditer(cleaned):
        count = int(match.group(1)) if match.group(1) else 1
        groups.append((count, int(match.group(2))))
        seen_spans.append(match.span())

    for match in _SIDED.finditer(cleaned):
        # "roll 2 20 sided dice" - the first number is how many.
        if any(start <= match.start() < end for start, end in seen_spans):
            continue
        count = int(match.group(1)) if match.group(1) else 1
        groups.append((count, int(match.group(2))))

    return _sane(groups)


def _sane(groups: list) -> list:
    """Bounded, and never so many that the total is the only readable part."""
    out = []
    budget = MAX_DICE
    for count, sides in groups:
        sides = max(MIN_SIDES, min(MAX_SIDES, int(sides)))
        count = max(1, min(budget, int(count)))
        if count <= 0:
            break
        out.append((count, sides))
        budget -= count
        if budget <= 0:
            break
    return out


# Words that mean dice and are not said for any other reason on this panel.
DICE_WORDS = ("dice", "die", "dies")
# The command on its own, with nothing else in the phrase, is still a request.
BARE = ("roll", "throw", "roller", "rolla",
        "roll it", "throw it", "roll them", "throw them",
        "roll again", "throw again")


def about_dice(phrase: str, spec: str = "") -> bool:
    """
    Whether a phrase that carried the word "roll" was about dice at all.

    "Roll" is the word for this and it is also half of "rock and roll", so
    the anchor alone lets the skill compete for a request to play music. It
    cannot be narrowed - anchoring on "roll a" loses "roll 2d6" - so the
    phrase is judged after matching instead, and a phrase that turns out not
    to be about dice is declined rather than answered.

    Three things count: notation, the word dice or die, or the command
    standing alone. "Roll" by itself is a request; "play some rock and roll"
    is not, and the difference is everything else in the sentence.
    """
    # The phrase alone, not the phrase and the spec. A payload's value is cut
    # out of the utterance it came from, so joining the two repeats it -
    # "roll it" becomes "roll it it", which is no longer the command standing
    # alone. The spec is only used when there is no phrase, for a caller
    # asking directly rather than through a skill.
    base = str(phrase or "").strip() or str(spec or "")
    text = re.sub(r"[^a-z0-9 ]", " ", base.lower())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return False
    if parse_roll(text):
        return True
    if any(word in text.split() for word in DICE_WORDS):
        return True
    return text in BARE


def plan(spec: str = "", groups: list = None) -> list:
    """
    What to roll: what was asked for, or one standard die.

    The one place a request becomes a list of groups, so a caller never has
    to know about the bounds or about what a bare request means. `groups`
    wins over `spec` - a caller that already knows what it wants should not
    have it read back out of English.
    """
    wanted = list(groups) if groups else parse_roll(spec)
    if not wanted:
        wanted = [(1, _rng.choice(STANDARD))]
    return _sane(wanted)


#   Only two comparisons. A total is a number and the question asked of it is
#   almost always "did it beat the target" - and every operator offered is one
#   more thing to choose from on a phone, for a page somebody uses once.
OPERATORS = ("greater", "less")


def groups_from_json(raw) -> list:
    """
    `[{"count": n, "sides": n}]` from a form, as `[(count, sides)]`.

    Read defensively: this arrives from a page anybody on the network can
    post to, and a malformed entry should cost that entry rather than the
    request. The bounds are applied again in `plan()`, which is where they
    belong - this only has to produce something shaped right.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            return []
    out = []
    for entry in list(raw or [])[:MAX_DICE]:
        try:
            sides = int(entry["sides"])
            count = int(entry.get("count", 1))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if MIN_SIDES <= sides <= MAX_SIDES and count > 0:
            out.append((count, sides))
    return out


def outcomes_from_json(raw) -> list:
    """Outcome rules from a form, still in the order they were given."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            return []
    return list(raw or [])


def totals_range(groups: list) -> tuple:
    """
    The lowest and highest total these dice can produce.

    Every die shows at least 1, so the floor is how many of them there are.
    """
    low = sum(count for count, _sides in groups or [])
    high = sum(count * sides for count, sides in groups or [])
    return low, high


def outcome_bounds(operator: str, span: tuple) -> tuple:
    """
    The thresholds that mean something, for this comparison and these dice.

    The range is not the same for both, which is easy to get wrong. On 2d6 the
    total runs 2 to 12, but "over 12" can never hold and "over 1" holds every
    time - so a usable "over" sits in 2 to 11. "Under" is the mirror of it:
    3 to 12.

    Both ends matter and for different reasons. A threshold that can never be
    reached is merely useless; one that is always reached is worse, because
    it wins every roll and quietly kills every rule below it - which reads as
    the rules being broken rather than as the first one always winning.
    """
    low, high = span
    if operator == "greater":
        return low, max(low, high - 1)
    return min(high, low + 1), high


def clean_outcomes(raw, span: tuple = None) -> list:
    """
    Outcome rules, in the order they were given.

    Order is priority: the first rule whose comparison holds is the one that
    shows, so the list is not a set of conditions but a sequence of them. A
    rule that cannot be read at all is dropped rather than guessed at.

    `span` is what the dice can actually total, and a threshold that means
    nothing against it is dropped - see `outcome_bounds` for which those are.
    """
    out = []
    for entry in list(raw or []):
        try:
            operator = str(entry.get("op", "")).strip().lower()
            if operator not in OPERATORS:
                continue
            value = int(float(entry.get("value")))
            text = str(entry.get("text", "")).strip()
        except (AttributeError, TypeError, ValueError):
            continue
        if not text:
            continue
        if span:
            low, high = outcome_bounds(operator, span)
            if not low <= value <= high:
                continue
        out.append({"op": operator, "value": value, "text": text})
    return out


def match_outcome(total: int, outcomes: list) -> dict:
    """The first rule the total satisfies, or None."""
    for rule in outcomes or []:
        if rule["op"] == "greater" and total > rule["value"]:
            return rule
        if rule["op"] == "less" and total < rule["value"]:
            return rule
    return None


def roll_groups(groups: list) -> list:
    """
    Every die's value, decided here and nowhere else.

    Returns `[(sides, value), ...]` in the order they were asked for, so the
    drawing and the breakdown agree about which die is which.
    """
    rolled = []
    for count, sides in groups:
        for _ in range(count):
            rolled.append((sides, _rng.randint(1, sides)))
    return rolled


def describe(rolled: list) -> str:
    """
    The breakdown line: what each die showed, grouped by type.

    The total is the headline and this is the second line, so it is written
    to be scanned rather than read - "3d6: 4, 2, 6" rather than a sentence.
    """
    if len(rolled) <= 1:
        return ""
    order: list = []
    by_type: dict = {}
    for sides, value in rolled:
        if sides not in by_type:
            by_type[sides] = []
            order.append(sides)
        by_type[sides].append(value)
    parts = []
    for sides in order:
        values = by_type[sides]
        label = f"{len(values)}d{sides}" if len(values) > 1 else f"d{sides}"
        parts.append(f"{label}: " + ", ".join(str(v) for v in values))
    return "   ".join(parts)


class DiceTray(QWidget):
    """
    Every die in one widget, because they share an area and settle together.

    One widget rather than one per die: the overlay layer is translucent and
    a repaint on it drags everything composited above into redrawing, so what
    matters is how much area changes per frame rather than how many objects
    are in it. A tray repaints its own rect once; twelve widgets would repaint
    twelve rects and force twelve composites.
    """

    #how long the last die takes to settle after the first
    STAGGER_MS = 110
    #and the longest the whole spread may run to. Sixty dice at the full
    #stagger is seven seconds of watching, which is a wait rather than a roll.
    MAX_SPREAD_MS = 1500
    #how often the tumbling faces change while a die is still moving
    FACE_MS = 70
    #centre-to-centre spacing, as a multiple of a die's width. A ceiling, not
    #a rule: a crowded tray closes it up and lets them overlap.
    GAP = 1.28
    #how much of the tray the grid may use, leaving a little air at the edges
    MARGIN = 0.94
    MIN_DIE = 34
    MAX_DIE = 132

    #how close two dice may be, centre to centre, before they shove
    TOUCH = 0.92
    #how deep an overlap has to be to knock a die back into rolling
    KNOCK = 0.30
    #how far along its flight a die has to be before a knock can catch it.
    #Knocked earlier it would drop wherever it was hit, which for most of
    #them is halfway across the tray from where they were going.
    KNOCK_AFTER = 0.55
    #how long a knocked die tumbles again for
    KNOCK_MS = 340
    #how many times one die may be knocked. Without a ceiling a crowded tray
    #can keep re-opening itself and never finish.
    MAX_KNOCKS = 2
    #and a hard stop, whatever the knocks say
    MAX_EXTRA_MS = 900

    def __init__(self, rolled: list, tray_w: int, tray_h: int, parent=None):
        super().__init__(parent)

        self.setFixedSize(int(tray_w), int(tray_h))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Every die that was rolled is drawn. Sizing down and letting them
        # crowd shows the whole roll; drawing twelve of forty and counting
        # the rest showed a number beside a picture of something else.
        self.rolled = list(rolled)
        self.drawn = list(rolled)

        self._elapsed = 0
        self._frame_ms = 33
        self._running = False
        self._on_settled: Optional[Callable] = None
        self._face_seed = 0

        self.collide = True
        self._ceiling = 0

        self.die_size, self._slots = self._layout(len(self.drawn))
        self._paths = {}
        self._plan = self._choreograph()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ── Where they end up ────────────────────────────────────────────────────

    def _layout(self, count: int) -> tuple:
        """
        A resting place for every die, and how big they can be.

        The grid follows the tray's shape rather than being square. A square
        arrangement in a wide tray leaves the sides empty and shrinks every
        die to fit the height it did not need to fill.

        Below `MIN_DIE` the dice stop shrinking and start **overlapping**
        instead, by closing the spacing rather than the dice. A crowded tray
        of readable dice that touch is a better picture of forty dice than a
        tidy grid of unreadable ones - and they are going to shove each other
        apart on the way in anyway.
        """
        if count <= 0:
            return self.MIN_DIE, []

        usable_w = self.width() * self.MARGIN
        usable_h = self.height() * self.MARGIN

        columns = max(1, min(count, int(round(
            math.sqrt(max(1.0, count * usable_w / max(1.0, usable_h)))))))
        rows = max(1, int(math.ceil(count / columns)))

        # The grid spans (n-1) gaps plus one whole die.
        across = (columns - 1) * self.GAP + 1
        down = (rows - 1) * self.GAP + 1
        size = min(usable_w / across, usable_h / down)
        size = max(self.MIN_DIE, min(self.MAX_DIE, size))

        # Spacing is whatever is left once the dice have their size. At the
        # floor that is less than a die wide, which is the overlap.
        gap_x = (min(size * self.GAP, (usable_w - size) / (columns - 1))
                 if columns > 1 else 0.0)
        gap_y = (min(size * self.GAP, (usable_h - size) / (rows - 1))
                 if rows > 1 else 0.0)

        slots = []
        for index in range(count):
            row, column = divmod(index, columns)
            in_row = min(columns, count - row * columns)
            x = self.width() / 2 + (column - (in_row - 1) / 2.0) * gap_x
            y = self.height() / 2 + (row - (rows - 1) / 2.0) * gap_y
            slots.append((x, y))
        return size, slots

    def _choreograph(self) -> list:
        """
        A start, an end and a spin for each die.

        They come in from off the tray's edges so the roll reads as something
        thrown rather than as shapes fading up in place, and each is given its
        own duration so they do not all stop on the same frame - a dozen dice
        halting in unison looks like a freeze rather than a settle.
        """
        plan = []
        for index, (target_x, target_y) in enumerate(self._slots):
            side = index % 4
            spread = self.die_size * 1.4
            if side == 0:
                start = (-spread, _rng.uniform(0, self.height()))
            elif side == 1:
                start = (self.width() + spread, _rng.uniform(0, self.height()))
            elif side == 2:
                start = (_rng.uniform(0, self.width()), -spread)
            else:
                start = (_rng.uniform(0, self.width()), self.height() + spread)

            plan.append({
                "start": start,
                "end": (target_x, target_y),
                "spin": _rng.uniform(2.0, 4.0) * 360 * _rng.choice((-1, 1)),
                "hop": _rng.uniform(0.10, 0.26),
                "settle_at": 0,
                # Where shoving has pushed it, on top of everything else.
                # Kept rather than decayed, so a pile that fought its way
                # apart stays apart once it is still.
                "shove": [0.0, 0.0],
                "knocks": 0,
                "knocked": False,
            })
        return plan

    # ── Running it ───────────────────────────────────────────────────────────

    def start(self, duration_ms: int = 1600, frame_ms: int = 33,
              animate: bool = True, on_settled: Callable = None) -> None:
        self._on_settled = on_settled
        self._frame_ms = max(16, int(frame_ms))
        self._elapsed = 0

        if not animate or duration_ms <= 0 or not self._plan:
            self._running = False
            for entry in self._plan:
                entry["settle_at"] = 0
            self.update()
            QTimer.singleShot(0, self._settled)
            return

        base = max(300, int(duration_ms))
        # Squeezed rather than dropped: they still land one after another,
        # just closer together the more of them there are.
        stagger = min(self.STAGGER_MS,
                      self.MAX_SPREAD_MS / max(1, len(self._plan) - 1))
        for index, entry in enumerate(self._plan):
            entry["settle_at"] = int(base + index * stagger)
        self._ceiling = self._finish_at() + self.MAX_EXTRA_MS

        self._running = True
        self._timer.start(self._frame_ms)

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self._on_settled = None

    def _finish_at(self) -> int:
        return max((entry["settle_at"] for entry in self._plan), default=0)

    def _tick(self) -> None:
        self._elapsed += self._frame_ms
        self._face_seed = self._elapsed // self.FACE_MS

        if self.collide:
            self._jostle()

        # The hard stop. Knocks extend a die's settle, and on a crowded tray
        # a chain of them could keep re-opening the same pair - so there is a
        # ceiling that ends it regardless.
        if self._ceiling and self._elapsed >= self._ceiling:
            for entry in self._plan:
                entry["settle_at"] = 0
                entry["knocked"] = False

        if self._elapsed >= self._finish_at():
            self._running = False
            self._timer.stop()
            self.update()
            self._settled()
            return

        self.update()

    def _settled(self) -> None:
        hook, self._on_settled = self._on_settled, None
        if callable(hook):
            hook()

    # ── Bumping into each other ──────────────────────────────────────────────

    def _jostle(self) -> None:
        """
        Dice that overlap shove each other apart, and a hard enough shove
        knocks a settled one back into rolling.

        **A knock never changes a value.** Every face was decided before any
        of this was drawn, so a die knocked at the last moment tumbles again
        and lands on exactly the number it was always going to. What the
        collision changes is how long it takes to get there and where on the
        tray it ends up - which is the part worth watching anyway.

        Anything else would make the drawing the thing that decides the roll,
        and then a dropped frame would be a different result.
        """
        count = len(self._plan)
        if count < 2:
            return

        spots = [self._where(index) for index in range(count)]
        touch = self.die_size * self.TOUCH
        knock = self.die_size * self.KNOCK

        for first in range(count):
            for second in range(first + 1, count):
                dx = spots[second][0] - spots[first][0]
                dy = spots[second][1] - spots[first][1]
                gap = math.hypot(dx, dy)
                if gap >= touch:
                    continue
                if gap < 1e-6:
                    # Exactly on top of each other - any direction will do.
                    dx, dy, gap = _rng.uniform(-1, 1), _rng.uniform(-1, 1), 1.0
                overlap = touch - gap
                push_x = dx / gap * overlap / 2.0
                push_y = dy / gap * overlap / 2.0

                self._plan[first]["shove"][0] -= push_x
                self._plan[first]["shove"][1] -= push_y
                self._plan[second]["shove"][0] += push_x
                self._plan[second]["shove"][1] += push_y
                spots[first] = (spots[first][0] - push_x,
                                spots[first][1] - push_y)
                spots[second] = (spots[second][0] + push_x,
                                 spots[second][1] + push_y)

                if overlap < knock:
                    continue
                for index in (first, second):
                    self._knock(index, spots[index])

    def _knock(self, index: int, where: tuple) -> None:
        """
        Set one die rolling again, where it was hit.

        It stops travelling and tumbles on the spot, which is what being
        clattered into looks like - and it comes to rest there rather than
        carrying on to the place it was originally headed.

        Only late in a flight. Knocked on the way in, a die would drop half a
        tray from where it was going and the whole roll would pile up
        wherever the traffic was worst.
        """
        entry = self._plan[index]
        if entry["knocks"] >= self.MAX_KNOCKS:
            return
        if self._ceiling and self._elapsed + self.KNOCK_MS > self._ceiling:
            return
        if not self._is_still(index) and self._progress(index) < self.KNOCK_AFTER:
            return

        edge = self.die_size / 2.0
        entry["end"] = (min(max(where[0], edge), self.width() - edge),
                        min(max(where[1], edge), self.height() - edge))
        entry["shove"] = [0.0, 0.0]
        entry["knocks"] += 1
        entry["knocked"] = True
        entry["settle_at"] = self._elapsed + self.KNOCK_MS

    def _progress(self, index: int) -> float:
        """How far through its flight a die is, 0 to 1."""
        settle_at = self._plan[index]["settle_at"]
        if settle_at <= 0:
            return 1.0
        return min(1.0, self._elapsed / float(settle_at))

    def _is_still(self, index: int) -> bool:
        entry = self._plan[index]
        return (not self._running or entry["settle_at"] <= 0
                or self._elapsed >= entry["settle_at"])

    # ── Drawing ──────────────────────────────────────────────────────────────

    def _where(self, index: int) -> tuple:
        """Just the position, for the collision pass."""
        return self._pose(index)[:2]

    def _flicker(self, index: int, sides: int) -> int:
        """
        A face while it is moving.

        Seeded off the frame and the die, so every die shows something
        different at any moment and none of them lands early on the value it
        is going to keep.
        """
        return 1 + (hash((self._face_seed, index)) % max(1, sides))

    def _pose(self, index: int) -> tuple:
        """Where die `index` is, how far it has turned, and what it shows."""
        entry = self._plan[index]
        sides, value = self.drawn[index]
        shove_x, shove_y = entry["shove"]

        # Clamped to the tray. Shoving is what keeps a crowded roll readable
        # and it is also what would push the outside of a pile off the edge,
        # where a die is half drawn and unreadable.
        edge = self.die_size / 2.0
        end_x = min(max(entry["end"][0] + shove_x, edge), self.width() - edge)
        end_y = min(max(entry["end"][1] + shove_y, edge), self.height() - edge)

        if self._is_still(index):
            return end_x, end_y, 0.0, value

        settle_at = entry["settle_at"]

        if entry["knocked"]:
            # Knocked back into rolling by something landing on it. It
            # tumbles where it lies rather than flying in again - and it
            # lands on the same value, because the value was never the
            # drawing's to decide.
            left = max(0.0, (settle_at - self._elapsed) / float(self.KNOCK_MS))
            angle = entry["spin"] * 0.10 * left
            return end_x, end_y, angle, self._flicker(index, sides)

        progress = self._elapsed / float(settle_at)
        eased = 1.0 - (1.0 - progress) ** 2.4

        start_x, start_y = entry["start"]
        x = start_x + (end_x - start_x) * eased
        y = start_y + (end_y - start_y) * eased
        # A hop on the way in, so it arcs rather than sliding along a line.
        y -= math.sin(eased * math.pi) * self.height() * entry["hop"]

        angle = entry["spin"] * (1.0 - (1.0 - eased) ** 1.4)
        return x, y, angle, self._flicker(index, sides)

    def _geometry_for(self, sides: int) -> Geometry:
        """The solid for one type, built once and kept."""
        if sides not in self._paths:
            shape, corners = shape_for(sides)
            self._paths[sides] = geometry_for(shape, self.die_size, corners)
        return self._paths[sides]

    def _font_for(self, text: str, width: float) -> QFont:
        """
        Fitted to the FACE, not to the die.

        A d100 showing 100 has three digits in the space a d20 uses for two,
        and the face carrying them is a fraction of the die - so a fixed size
        either clips the wide case or grows over the facets, which is what
        made every shape read the same.
        """
        limit = self.die_size * width
        size = max(8, int(limit * 0.95))
        font = QFont("poppins-medium", size)
        font.setBold(True)
        while size > 7:
            if QFontMetrics(font).horizontalAdvance(text) <= limit:
                break
            size -= 1
            font.setPointSize(size)
        return font

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        for index, (sides, _value) in enumerate(self.drawn):
            x, y, angle, shown = self._pose(index)
            look = look_for(sides)
            solid = self._geometry_for(sides)

            painter.save()
            painter.translate(x, y)
            if angle:
                painter.rotate(angle)

            # The silhouette.
            painter.setPen(QPen(look.border, max(2.0, self.die_size * 0.075)))
            painter.setBrush(look.fill)
            painter.drawPath(solid.outline)

            # The faces turned away from us, as the edges between them.
            crease = QColor(look.border)
            crease.setAlpha(130)
            painter.setPen(QPen(crease, max(1.0, self.die_size * 0.035)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(solid.facets)

            # The face being read, lifted a shade so the number sits on
            # something rather than floating over the whole die.
            if solid.face is not None:
                painter.setPen(QPen(crease, max(1.0, self.die_size * 0.04)))
                painter.setBrush(look.fill.lighter(114))
                painter.drawPath(solid.face)

            if solid.pips:
                # A d6 gets pips. Nothing says die faster, and a cube face is
                # the one face everybody can already picture.
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(look.number)
                painter.drawPath(pip_path(shown, self.die_size))
                painter.restore()
                continue

            # The number does not turn with the die. A rolling die shows a
            # different face every moment rather than the same face at an
            # angle, and a number spinning with the outline reads as one
            # sticker on a tumbling shape.
            if angle:
                painter.rotate(-angle)
            text = str(shown)
            painter.setFont(self._font_for(text, solid.text_width))
            painter.setPen(look.number)
            offset_x, offset_y = solid.text_offset
            box = QRectF(-self.die_size / 2 + offset_x * self.die_size,
                         -self.die_size / 2 + offset_y * self.die_size,
                         self.die_size, self.die_size)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

            painter.restore()

        painter.end()
