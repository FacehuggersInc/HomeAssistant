"""
Converting one unit into another.

No Qt, no Skill, no plugin - a table and two functions, so the whole thing can
be exercised without a panel.

Everything reduces to one base unit per dimension: metres, grams, millilitres,
seconds, metres per second, bytes. Temperature is the exception and is handled
apart from the rest, because it is **affine** rather than linear - 0°C is not
zero of anything, and a factor cannot express an offset. Folding it into the
same table would make "20 C in F" come out as 36.
"""

from __future__ import annotations

import re

# alias -> [(dimension, factor to the base unit), ...]
#
# A list because some names are genuinely two units. An ounce is a mass and a
# fluid ounce is a volume, and "how many ounces in a gallon" means the second
# one - see `resolve()`, which picks whichever pair makes the two sides agree
# rather than guessing from the word alone.
UNITS: dict = {}

# canonical name -> (singular, plural) for reading back
NAMES: dict = {}


def _unit(canonical: str, singular: str, plural: str, dimension: str,
          factor: float, *aliases: str) -> None:
    NAMES[canonical] = (singular, plural)
    for alias in (canonical, singular, plural) + aliases:
        UNITS.setdefault(alias.lower(), []).append((dimension, factor, canonical))


# ── Length, base metre ────────────────────────────────────────────────────
_unit("millimetre", "millimetre", "millimetres", "length", 0.001,
      "mm", "millimeter", "millimeters")
_unit("centimetre", "centimetre", "centimetres", "length", 0.01,
      "cm", "centimeter", "centimeters")
_unit("metre", "metre", "metres", "length", 1.0, "m", "meter", "meters")
_unit("kilometre", "kilometre", "kilometres", "length", 1000.0,
      "km", "kilometer", "kilometers", "kilometre", "klick", "klicks")
_unit("inch", "inch", "inches", "length", 0.0254, "in", "\"")
_unit("foot", "foot", "feet", "length", 0.3048, "ft", "'")
_unit("yard", "yard", "yards", "length", 0.9144, "yd", "yds")
_unit("mile", "mile", "miles", "length", 1609.344, "mi")
_unit("nautical mile", "nautical mile", "nautical miles", "length", 1852.0, "nmi")

# ── Mass, base gram ───────────────────────────────────────────────────────
_unit("milligram", "milligram", "milligrams", "mass", 0.001, "mg")
_unit("gram", "gram", "grams", "mass", 1.0, "g", "gramme", "grammes")
_unit("kilogram", "kilogram", "kilograms", "mass", 1000.0, "kg", "kilo", "kilos")
_unit("ounce", "ounce", "ounces", "mass", 28.349523125, "oz")
_unit("pound", "pound", "pounds", "mass", 453.59237, "lb", "lbs")
_unit("stone", "stone", "stone", "mass", 6350.29318, "st")
_unit("tonne", "tonne", "tonnes", "mass", 1_000_000.0, "metric ton", "metric tons")
_unit("ton", "ton", "tons", "mass", 907184.74, "short ton", "short tons")

# ── Volume, base millilitre. US customary, since that is where the panel is
#    and "a cup" means 236ml there and 250 in a British recipe book. ────────
_unit("millilitre", "millilitre", "millilitres", "volume", 1.0,
      "ml", "milliliter", "milliliters")
_unit("litre", "litre", "litres", "volume", 1000.0, "l", "liter", "liters")
_unit("teaspoon", "teaspoon", "teaspoons", "volume", 4.92892159375, "tsp")
_unit("tablespoon", "tablespoon", "tablespoons", "volume", 14.78676478125,
      "tbsp", "tbs")
_unit("fluid ounce", "fluid ounce", "fluid ounces", "volume", 29.5735295625,
      "fl oz", "floz", "fluid oz")
_unit("cup", "cup", "cups", "volume", 236.5882365)
_unit("pint", "pint", "pints", "volume", 473.176473, "pt")
_unit("quart", "quart", "quarts", "volume", 946.352946, "qt")
_unit("gallon", "gallon", "gallons", "volume", 3785.411784, "gal")

# An ounce asked about alongside a volume is a fluid ounce. Registered as a
# second reading of the same word rather than a rule in the parser, so
# `resolve()` settles it the same way it settles everything else.
UNITS["ounce"].append(("volume", 29.5735295625, "fluid ounce"))
UNITS["ounces"].append(("volume", 29.5735295625, "fluid ounce"))
UNITS["oz"].append(("volume", 29.5735295625, "fluid ounce"))

# ── Time, base second ─────────────────────────────────────────────────────
_unit("second", "second", "seconds", "time", 1.0, "sec", "secs", "s")
_unit("minute", "minute", "minutes", "time", 60.0, "min", "mins")
_unit("hour", "hour", "hours", "time", 3600.0, "hr", "hrs", "h")
_unit("day", "day", "days", "time", 86400.0)
_unit("week", "week", "weeks", "time", 604800.0)
_unit("year", "year", "years", "time", 31557600.0)

# ── Speed, base metres per second ─────────────────────────────────────────
_unit("mile per hour", "mile per hour", "miles per hour", "speed", 0.44704,
      "mph", "miles an hour")
_unit("kilometre per hour", "kilometre per hour", "kilometres per hour",
      "speed", 0.277777778, "kph", "kmh", "km/h", "kilometers per hour")
_unit("metre per second", "metre per second", "metres per second", "speed", 1.0,
      "m/s", "meters per second")
_unit("knot", "knot", "knots", "speed", 0.514444444, "kt", "kts")

# ── Data, base byte ───────────────────────────────────────────────────────
_unit("byte", "byte", "bytes", "data", 1.0)
_unit("kilobyte", "kilobyte", "kilobytes", "data", 1024.0, "kb")
_unit("megabyte", "megabyte", "megabytes", "data", 1024.0 ** 2, "mb")
_unit("gigabyte", "gigabyte", "gigabytes", "data", 1024.0 ** 3, "gb")
_unit("terabyte", "terabyte", "terabytes", "data", 1024.0 ** 4, "tb")

# ── Temperature, on its own ───────────────────────────────────────────────
TEMPERATURES = {
    "c": "celsius", "celsius": "celsius", "centigrade": "celsius",
    "degrees celsius": "celsius", "celcius": "celsius",
    "f": "fahrenheit", "fahrenheit": "fahrenheit", "farenheit": "fahrenheit",
    "degrees fahrenheit": "fahrenheit",
    "k": "kelvin", "kelvin": "kelvin", "kelvins": "kelvin",
}
TEMPERATURE_NAMES = {"celsius": ("degree Celsius", "degrees Celsius"),
                     "fahrenheit": ("degree Fahrenheit", "degrees Fahrenheit"),
                     "kelvin": ("kelvin", "kelvin")}

# Spoken numbers that survive transcript normalisation. Not a full table -
# normalisation converts the rest - but "a", "an" and "half" are words rather
# than numbers and never become digits.
WORD_NUMBERS = {
    "a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0,
    "five": 5.0, "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0,
    "ten": 10.0, "eleven": 11.0, "twelve": 12.0, "twenty": 20.0,
    "fifty": 50.0, "hundred": 100.0, "thousand": 1000.0,
    "half": 0.5, "quarter": 0.25, "a half": 0.5, "a quarter": 0.25,
}

# Single-token unit words, for the skill's own Matcher patterns.
#
# A unit is an OPAQUE value the way a song title is: no example can contain
# every one of them, and scoring an utterance against examples that name
# specific units punishes it for naming different ones. "How many inches in a
# foot" against the example "how many feet in a mile" shares two lemmas of
# three and scores 0.67 - under the threshold, so it matched nothing at all
# and the whole of length, area and data was unreachable while the arithmetic
# for all of it worked.
#
# The fix is to match on the SHAPE - "how many <unit> in <unit>" - which
# needs the unit words as a token set rather than as examples. Multi-word
# names contribute their last word, which is enough for the pattern to fire;
# the parser above is what actually reads the phrase.
PATTERN_TOKENS = sorted(
    {word for word in list(UNITS) + list(TEMPERATURES) if " " not in word}
    | {word.split()[-1] for word in list(UNITS) + list(TEMPERATURES)}
)

# The longest unit names first, so "fluid ounces" is not read as "ounces" and
# "miles per hour" is not read as "miles".
_UNIT_WORDS = sorted(set(list(UNITS) + list(TEMPERATURES)),
                     key=len, reverse=True)
_UNIT_ALT = "|".join(re.escape(word) for word in _UNIT_WORDS)
# `33 point 7` as well as `33.7`. Transcript normalisation turns "thirty
# three point seven" into "33 point 7" - digits either side of the word,
# because "point" is not a number and nothing converts it. Spoken decimals
# are ordinary in a conversion ("how many inches are in 33.7 millimetres")
# and arrived here as an unparseable phrase.
_NUMBER = r"[-+]?\d+(?:[.,]\d+)?(?:\s+point\s+\d+)?(?:\s*/\s*\d+)?"
_WORD_NUM = "|".join(re.escape(word) for word in
                     sorted(WORD_NUMBERS, key=len, reverse=True))

# "how many X in (a) Y", and the amount is optional because "in a cup" is one
# cup and nobody says the one.
#
# `_ARTICLE` sits between the number and the unit because "half a cup" puts a
# word there. Without it the amount group ends up matching the ARTICLE - "a"
# is itself a word number - and half a cup was read as one cup.
_ARTICLE = r"(?:of\s+)?(?:an?\s+)?"

# The verb and the preposition are BOTH optional, and either alone is a
# question. "How many inches are in 33.7 mm" has both, "how many cups in a
# litre" has only the preposition, and "how many inches is 33.7 mm" has only
# the verb - and that last one reached nothing at all while the other two
# worked.
_HOW_MANY = re.compile(
    rf"how\s+many\s+(?P<to>{_UNIT_ALT})\s+"
    rf"(?:(?:are|is|equals?|makes?)\s+)?(?:(?:in|to|per|into)\s+)?"
    rf"(?:(?P<amount>{_NUMBER}|{_WORD_NUM})\s+)?{_ARTICLE}"
    rf"(?P<from>{_UNIT_ALT})\b", re.I)

# "convert 5 miles to km", "what's 350 F in C", "5 miles in km".
_AMOUNT_FIRST = re.compile(
    rf"(?P<amount>{_NUMBER}|{_WORD_NUM})\s*{_ARTICLE}(?:degrees?\s+)?"
    rf"(?P<from>{_UNIT_ALT})"
    rf"\s+(?:in|to|into|as|equals?)\s+(?:degrees?\s+)?(?P<to>{_UNIT_ALT})\b",
    re.I)


def _amount(text: str) -> float:
    """A number from digits, a word, or a fraction like `1 / 2`."""
    if text is None:
        return 1.0
    text = text.strip().lower()
    if not text:
        return 1.0
    if text in WORD_NUMBERS:
        return WORD_NUMBERS[text]
    text = text.replace(",", "")
    # "33 point 7" - a spoken decimal that normalisation left half converted.
    if " point " in text:
        whole, _, fraction = text.partition(" point ")
        try:
            return float(f"{float(whole):.0f}.{fraction.strip().replace(' ', '')}")
        except ValueError:
            pass
    if "/" in text:
        top, _, bottom = text.partition("/")
        try:
            return float(top.strip()) / float(bottom.strip())
        except (ValueError, ZeroDivisionError):
            return 1.0
    try:
        return float(text)
    except ValueError:
        return 1.0


def resolve(left: str, right: str) -> tuple | None:
    """
    Which reading of each unit name makes the two sides agree.

    Returns ((dimension, factor, name), (dimension, factor, name)) or None.
    "How many ounces in a gallon" is a volume on both sides; "how many ounces
    in a pound" is a mass on both. The word is the same and the answer is not,
    and nothing but the other side of the question can decide it.
    """
    left, right = (left or "").lower().strip(), (right or "").lower().strip()

    left_temp, right_temp = TEMPERATURES.get(left), TEMPERATURES.get(right)
    if left_temp and right_temp:
        return ("temperature", left_temp, left_temp), \
               ("temperature", right_temp, right_temp)

    for a in UNITS.get(left, []):
        for b in UNITS.get(right, []):
            if a[0] == b[0]:
                return a, b
    return None


def parse(phrase: str) -> tuple | None:
    """(amount, from, to) as raw unit words, or None if this is not a conversion."""
    text = (phrase or "").strip()
    for pattern in (_HOW_MANY, _AMOUNT_FIRST):
        match = pattern.search(text)
        if match:
            return (_amount(match.groupdict().get("amount")),
                    match.group("from"), match.group("to"))
    return None


def convert(amount: float, source: tuple, target: tuple) -> float:
    """The value in the target unit. Both sides must be the same dimension."""
    if source[0] == "temperature":
        celsius = {"celsius": lambda v: v,
                   "fahrenheit": lambda v: (v - 32.0) * 5.0 / 9.0,
                   "kelvin": lambda v: v - 273.15}[source[1]](amount)
        return {"celsius": lambda v: v,
                "fahrenheit": lambda v: v * 9.0 / 5.0 + 32.0,
                "kelvin": lambda v: v + 273.15}[target[1]](celsius)
    return amount * source[1] / target[1]


def pretty(value: float) -> str:
    """
    A number somebody would say.

    Scaled to the size of the answer rather than fixed: 4.23 cups is useful
    and 4.2288 is a reading, while 0.0394 inches rounded the same way is zero.
    """
    if value != value:
        return "?"
    size = abs(value)
    if size >= 1000:
        text = f"{value:,.0f}"
    elif size >= 100:
        text = f"{value:.0f}"
    elif size >= 10:
        text = f"{value:.1f}"
    elif size >= 1:
        text = f"{value:.2f}"
    elif size >= 0.01:
        text = f"{value:.4f}"
    elif size == 0:
        return "0"
    else:
        return f"{value:.3g}"
    # Trailing zeros are noise: "4.00 cups" is four cups.
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def name(unit: tuple, value: float) -> str:
    """The unit read back, singular or plural to match the number."""
    canonical = unit[2]
    table = TEMPERATURE_NAMES if unit[0] == "temperature" else NAMES
    singular, plural = table.get(canonical, (canonical, canonical))
    return singular if abs(value - 1.0) < 1e-9 else plural
