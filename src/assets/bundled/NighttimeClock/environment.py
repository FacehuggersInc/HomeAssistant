"""
What the night sky is doing, drawn.

The panel already fetches the weather for its widget. This turns that into
something to look at while the room is dark: rain that falls the way the wind
is actually blowing, snow when it is snowing, stars when the sky is clear,
cloud when it is not.

**Layers, not modes.** Weather is not one of five things, it is several at
once - overcast *and* raining *and* windy - so `layers_for()` returns a stack
and the page draws them in order. Cloud goes behind rain; fireflies only come
out when the sky is worth looking at.

Everything here is deliberately cheap. It repaints in full at 20fps for eight
hours on a wall panel, so: no per-particle QObjects, no gradients built per
frame that could be built once, and hard caps on every count.
"""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (
    QColor, QPen, QPainter, QRadialGradient, QLinearGradient,
)


#Wind direction from the API is where the wind comes FROM, in meteorological
#degrees. Screen drift is the opposite, and x/y are not compass north.
class Gusts:
    """
    Occasional surges, shared by every layer.

    `wind_gusts_10m` was fetched and ignored. A steady drift reads as a fan;
    wind reads as wind when it comes in pushes, and one shared multiplier
    means rain, snow, cloud and fireflies all lean at the same moment rather
    than each doing its own thing.
    """

    def __init__(self, base_mph: float = 0.0, gust_mph: float = 0.0):
        try:
            self.base = max(0.0, float(base_mph or 0))
            self.peak = max(self.base, float(gust_mph or 0))
        except (TypeError, ValueError):
            self.base = self.peak = 0.0
        # How much harder a gust blows than the steady wind.
        #
        # A minimum absolute difference as well as a ratio: 3mph gusting to
        # 4mph is a ratio of a sixth and physically nothing at all, and a
        # panel making the rain lurch for it would be inventing weather.
        gap = self.peak - self.base
        if self.base <= 0 or gap < 4.0:
            self.extra = 0.0
        else:
            self.extra = min(1.6, gap / max(6.0, self.base))
        self.multiplier = 1.0
        self._until = 0.0
        self._wait = random.uniform(4.0, 14.0)

    def step(self, dt: float) -> None:
        if self.extra <= 0.05:
            self.multiplier = 1.0
            return
        if self._until > 0:
            self._until -= dt
            # Rises fast, falls away slowly, like a real gust.
            self.multiplier = 1.0 + self.extra * min(1.0, self._until / 1.4)
            if self._until <= 0:
                self.multiplier = 1.0
                self._wait = random.uniform(6.0, 22.0)
            return
        self._wait -= dt
        if self._wait <= 0:
            self._until = random.uniform(1.8, 4.2)


def wind_vector(speed_mph: float, from_degrees: float) -> tuple:
    """(dx, dy) unit-ish drift for a wind, scaled to something watchable."""
    try:
        speed = max(0.0, float(speed_mph or 0))
        bearing = float(from_degrees or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0
    # Blowing TOWARDS bearing + 180. North is up, so a northerly (0) pushes
    # things down the screen.
    towards = math.radians((bearing + 180.0) % 360.0)
    dx = math.sin(towards)
    dy = -math.cos(towards)
    # Real mph would be unwatchable. This is "reads as the right strength".
    strength = min(1.0, speed / 35.0)
    return dx * strength, dy * strength


class Layer:
    """One thing drawn over the night page."""

    name = "layer"
    #set by the page, shared by every layer so they gust together
    gusts = None

    def gust(self) -> float:
        return self.gusts.multiplier if self.gusts is not None else 1.0

    def resize(self, width: float, height: float) -> None:
        pass

    def step(self, dt: float, width: float, height: float) -> None:
        pass

    def paint(self, painter, width: float, height: float) -> None:
        pass


## ── Fireflies ────────────────────────────────────────────────────────────────

class Firefly:
    __slots__ = ("x", "y", "vx", "vy", "phase", "speed", "size", "hue",
                 "wx", "wy")

    def __init__(self, width: float, height: float):
        self.x = random.uniform(0.05, 0.95) * width
        self.y = random.uniform(0.05, 0.95) * height
        self.wx = 0.0
        self.wy = 0.0
        angle = random.uniform(0, math.tau)
        drift = random.uniform(3.0, 11.0)
        self.vx = math.cos(angle) * drift
        self.vy = math.sin(angle) * drift
        self.phase = random.uniform(0, math.tau)
        self.speed = random.uniform(0.25, 0.7)
        self.size = random.uniform(2.6, 5.0)
        # Warmer and more saturated than a plain yellow: a firefly reads as
        # green-gold, and the odd cooler one keeps a group from looking flat.
        self.hue = random.choice((48, 52, 56, 64, 120))

    #How hard a gust pushes, and how quickly that push fades. Per second.
    PUSH  = 34.0
    DECAY = 1.6

    def step(self, dt, width, height, wind=(0.0, 0.0)):
        # Wind is a PUSH that fades, not a term added forever.
        #
        # `self.x += (self.vx + wind[0] * 26) * dt` added the same amount every
        # frame, so a firefly drifted downwind without limit - and the edge
        # bounce reverses vx but not the wind, so they piled into the corner
        # and stayed there. A gust that blows them and then lets them go is
        # both what wind looks like and what leaves the screen alive.
        self.wx += wind[0] * self.PUSH * dt
        self.wy += wind[1] * self.PUSH * dt
        fade = max(0.0, 1.0 - self.DECAY * dt)
        self.wx *= fade
        self.wy *= fade

        self.x += (self.vx + self.wx) * dt
        self.y += (self.vy + self.wy) * dt
        self.phase += self.speed * dt

        margin = 20.0
        if self.x < margin and self.vx < 0:
            self.vx = -self.vx
        if self.x > width - margin and self.vx > 0:
            self.vx = -self.vx
        if self.y < margin and self.vy < 0:
            self.vy = -self.vy
        if self.y > height - margin and self.vy > 0:
            self.vy = -self.vy
        # Wind can push one off the edge regardless of its own heading.
        self.x = min(max(self.x, 4.0), max(5.0, width - 4.0))
        self.y = min(max(self.y, 4.0), max(5.0, height - 4.0))

    def glow(self) -> float:
        return 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self.phase))


class Fireflies(Layer):
    """
    Drifting, pulsing points of light.

    Brighter than a subtle hint: a white-hot core inside a saturated body
    inside a wide halo. On a near-black page at 12% backlight a dim dot is
    invisible, which rather defeats the point of having them.
    """

    name = "fireflies"

    def __init__(self, count: int = 16, wind=(0.0, 0.0)):
        self.count = max(0, min(60, int(count)))
        self.wind = wind
        self.flies: list = []
        self._size = (0.0, 0.0)

    def resize(self, width, height):
        if (width, height) == self._size and self.flies:
            return
        self._size = (width, height)
        self.flies = [Firefly(width, height) for _ in range(self.count)]

    def step(self, dt, width, height):
        gust = self.gust()
        # Only the GUST, not the wind under it.
        #
        # multiplier sits at 1.0 and rises while a gust is blowing, so the
        # excess over 1 is the gust itself. Feeding the whole multiplier in
        # meant a steady breeze pushed every frame for as long as the weather
        # said "windy", which walks them all into the downwind corner. A
        # firefly is not a leaf; it flies where it likes and gets shoved
        # occasionally.
        excess = max(0.0, gust - 1.0)
        wind = (self.wind[0] * excess, self.wind[1] * excess)
        for fly in self.flies:
            fly.step(dt, width, height, wind)

    def paint(self, painter, width, height):
        painter.setPen(Qt.PenStyle.NoPen)
        for fly in self.flies:
            glow = fly.glow()
            body = QColor()
            body.setHsv(fly.hue, 190, 255)

            # Halo
            radius = fly.size * 7.0
            gradient = QRadialGradient(QPointF(fly.x, fly.y), radius)
            inner = QColor(body)
            inner.setAlpha(int(120 * glow))
            mid = QColor(body)
            mid.setAlpha(int(40 * glow))
            edge = QColor(body)
            edge.setAlpha(0)
            gradient.setColorAt(0.0, inner)
            gradient.setColorAt(0.45, mid)
            gradient.setColorAt(1.0, edge)
            painter.setBrush(gradient)
            painter.drawEllipse(QPointF(fly.x, fly.y), radius, radius)

            solid = QColor(body)
            solid.setAlpha(int(235 * glow))
            painter.setBrush(solid)
            painter.drawEllipse(QPointF(fly.x, fly.y), fly.size, fly.size)

            # A white-hot centre. This is what makes it read as a light rather
            # than a coloured dot.
            core = QColor(255, 255, 232)
            core.setAlpha(int(230 * glow))
            painter.setBrush(core)
            painter.drawEllipse(QPointF(fly.x, fly.y),
                                fly.size * 0.42, fly.size * 0.42)


## ── Stars ────────────────────────────────────────────────────────────────────

class Stars(Layer):
    """
    Still points that twinkle. The cheapest layer by a distance.

    They do not move, so there is nothing to integrate - only a phase per
    star. Count falls away with cloud cover, which is most of what makes an
    overcast night look overcast.
    """

    name = "stars"

    def __init__(self, count: int = 70):
        self.count = max(0, min(240, int(count)))
        self.stars: list = []
        self._size = (0.0, 0.0)

    def resize(self, width, height):
        if (width, height) == self._size and self.stars:
            return
        self._size = (width, height)
        self.stars = []
        for _ in range(self.count):
            self.stars.append([
                random.uniform(0, width),
                # Weighted to the top: stars near the floor read as dirt.
                random.uniform(0, height * 0.72),
                random.uniform(0.6, 1.7),          # size
                random.uniform(0, math.tau),       # phase
                random.uniform(0.4, 1.4),          # twinkle speed
                random.uniform(0.35, 1.0),         # base brightness
            ])

    def step(self, dt, width, height):
        for star in self.stars:
            star[3] += star[4] * dt

    def paint(self, painter, width, height):
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, size, phase, _speed, base in self.stars:
            twinkle = base * (0.55 + 0.45 * (0.5 + 0.5 * math.sin(phase)))
            colour = QColor(226, 234, 255)
            colour.setAlpha(int(215 * twinkle))
            painter.setBrush(colour)
            painter.drawEllipse(QPointF(x, y), size, size)


## ── Moon ─────────────────────────────────────────────────────────────────────

class Moon(Layer):
    """
    Tonight's moon, at tonight's phase.

    Drawn as a lit disc with an unlit disc cut out of it, offset sideways -
    which is exactly how a moon is lit, and gives a real crescent and a real
    gibbous from the same two shapes. Compositing rather than arcs: an arc
    approximation of a gibbous moon has a straight edge, and the eye catches
    it immediately.

    Nothing animates. The phase changes over days, so it is worked out once
    and drawn identically until the page is rebuilt.
    """

    name = "moon"

    def __init__(self, phase: float, illumination: float, waxing: bool):
        self.phase = float(phase)
        self.illumination = max(0.0, min(1.0, float(illumination)))
        self.waxing = bool(waxing)
        self.x = 0.0
        self.y = 0.0
        self.radius = 0.0

    def resize(self, width, height):
        self.radius = max(14.0, min(46.0, min(width, height) * 0.035))
        # Upper corner, opposite the side the crescent points, so the lit
        # limb faces into the page rather than off it.
        self.x = (width * 0.86) if self.waxing else (width * 0.14)
        self.y = height * 0.17

    def paint(self, painter, width, height):
        if self.illumination < 0.015:
            # New moon. There is nothing to draw, and a faint disc would be
            # a moon that is not there.
            return

        radius = self.radius
        lit = QColor(232, 234, 226)
        lit.setAlpha(238)

        # A soft halo, so it sits in the sky rather than on top of it.
        halo = QRadialGradient(QPointF(self.x, self.y), radius * 3.2)
        halo.setColorAt(0.0, QColor(226, 232, 240, 34))
        halo.setColorAt(0.45, QColor(226, 232, 240, 12))
        halo.setColorAt(1.0, QColor(226, 232, 240, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(self.x, self.y), radius * 3.2, radius * 3.2)

        painter.save()
        painter.setBrush(lit)
        painter.drawEllipse(QPointF(self.x, self.y), radius, radius)

        # The shadow: a disc the same size, slid across. At half illumination
        # it sits exactly on centre and gives a straight terminator; at a thin
        # crescent it covers almost everything.
        if self.illumination < 0.985:
            offset = radius * 2.0 * (1.0 - self.illumination)
            offset = offset if self.waxing else -offset
            # Painted in the background colour rather than with a clip, so it
            # works over the gradient without a mask.
            shadow = QColor(9, 11, 18)
            painter.setBrush(shadow)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Source)
            painter.drawEllipse(QPointF(self.x - offset, self.y),
                                radius, radius)
        painter.restore()


## ── Frost ────────────────────────────────────────────────────────────────────

class Frost(Layer):
    """
    A sparkle around the edges when it is below freezing.

    Only at the border, and only a few points at a time: the middle of the
    page is a clock somebody is reading, and glitter across it would be
    unbearable at 3am.
    """

    name = "frost"

    def __init__(self, hardness: float = 0.5):
        self.hardness = max(0.0, min(1.0, float(hardness)))
        self.points: list = []
        self._size = (0.0, 0.0)

    def resize(self, width, height):
        if (width, height) == self._size and self.points:
            return
        self._size = (width, height)
        count = int(16 + 30 * self.hardness)
        band = min(width, height) * 0.16
        self.points = []
        for _ in range(count):
            # Along one edge, picked at random.
            edge = random.randint(0, 3)
            if edge == 0:
                x, y = random.uniform(0, width), random.uniform(0, band)
            elif edge == 1:
                x, y = random.uniform(0, width), random.uniform(height - band, height)
            elif edge == 2:
                x, y = random.uniform(0, band), random.uniform(0, height)
            else:
                x, y = random.uniform(width - band, width), random.uniform(0, height)
            self.points.append([
                x, y,
                random.uniform(0.7, 1.8),        # size
                random.uniform(0, math.tau),     # phase
                random.uniform(0.35, 1.1),       # speed
            ])

    def step(self, dt, width, height):
        for point in self.points:
            point[3] += point[4] * dt

    def paint(self, painter, width, height):
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, size, phase, _speed in self.points:
            # Mostly off. A sparkle is a thing that catches the eye once, not
            # a field of steady dots.
            sparkle = (0.5 + 0.5 * math.sin(phase)) ** 4
            if sparkle < 0.04:
                continue
            colour = QColor(206, 232, 255)
            colour.setAlpha(int(210 * sparkle))
            painter.setBrush(colour)
            painter.drawEllipse(QPointF(x, y), size, size)


## ── Rare sky events ──────────────────────────────────────────────────────────

#Named shapes, as unit offsets within their own box. Not the real sky - a
#constellation drawn at its true scale is either off the page or three pixels
#across - but the recognisable outline of each.
CONSTELLATIONS = {
    "Orion": (
        [(0.18, 0.05), (0.78, 0.02), (0.40, 0.42), (0.52, 0.45), (0.64, 0.48),
         (0.22, 0.88), (0.74, 0.92)],
        [(0, 2), (1, 4), (2, 3), (3, 4), (2, 5), (4, 6), (5, 6)],
    ),
    "The Plough": (
        [(0.02, 0.62), (0.22, 0.72), (0.44, 0.66), (0.62, 0.52),
         (0.70, 0.28), (0.90, 0.20), (0.86, 0.02)],
        [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (3, 6)],
    ),
    "Cassiopeia": (
        [(0.02, 0.30), (0.26, 0.78), (0.50, 0.24), (0.74, 0.80), (0.98, 0.34)],
        [(0, 1), (1, 2), (2, 3), (3, 4)],
    ),
    "Cygnus": (
        [(0.50, 0.02), (0.50, 0.38), (0.50, 0.72), (0.50, 0.98),
         (0.14, 0.52), (0.86, 0.50)],
        [(0, 1), (1, 2), (2, 3), (4, 1), (1, 5)],
    ),
    "Lyra": (
        [(0.20, 0.08), (0.52, 0.30), (0.36, 0.60), (0.68, 0.72), (0.52, 0.96)],
        [(0, 1), (1, 2), (2, 4), (4, 3), (3, 1)],
    ),
}


class ShootingStar(Layer):
    """
    A streak, once in a while.

    Rare on purpose. Something that happens every few seconds is a screensaver
    effect; something that happens twice an hour is worth looking up for, and
    the whole point of a clock you glance at is that most glances are ordinary.
    """

    name = "shooting_star"

    #average seconds between them, jittered
    AVERAGE_GAP = 150.0

    def __init__(self, gap: float = None):
        self.gap = float(gap or self.AVERAGE_GAP)
        self.wait = random.uniform(self.gap * 0.3, self.gap)
        self.active = None

    def step(self, dt, width, height):
        if self.active is not None:
            self.active[4] += dt
            self.active[0] += self.active[2] * dt
            self.active[1] += self.active[3] * dt
            if self.active[4] > self.active[5]:
                self.active = None
            return

        self.wait -= dt
        if self.wait > 0:
            return
        self.wait = random.uniform(self.gap * 0.55, self.gap * 1.6)

        # Always downward and mostly sideways, starting in the upper sky.
        speed = random.uniform(520, 900)
        angle = random.uniform(0.18, 0.62) * (1 if random.random() < 0.5 else -1)
        direction = 1 if angle > 0 else -1
        self.active = [
            random.uniform(0.05, 0.95) * width if direction > 0 else
            random.uniform(0.05, 0.95) * width,
            random.uniform(0.04, 0.42) * height,
            math.cos(angle) * speed * direction,
            abs(math.sin(angle)) * speed,
            0.0,                              # elapsed
            random.uniform(0.55, 1.15),       # lifetime
            random.uniform(90, 190),          # trail length
        ]

    def paint(self, painter, width, height):
        if self.active is None:
            return
        x, y, vx, vy, elapsed, lifetime, trail = self.active
        # Fades in quickly and out slowly, like the real thing.
        progress = elapsed / lifetime
        fade = min(1.0, progress * 6.0) * (1.0 - progress) ** 0.6

        length = math.hypot(vx, vy) or 1.0
        back_x, back_y = -vx / length * trail, -vy / length * trail

        gradient = QLinearGradient(x, y, x + back_x, y + back_y)
        head = QColor(255, 255, 245, int(235 * fade))
        tail = QColor(190, 214, 255, 0)
        gradient.setColorAt(0.0, head)
        gradient.setColorAt(1.0, tail)
        pen = QPen(gradient, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, y), QPointF(x + back_x, y + back_y))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 250, int(230 * fade)))
        painter.drawEllipse(QPointF(x, y), 1.9, 1.9)


class Constellation(Layer):
    """
    One named shape, faintly joined, fading in and out over minutes.

    Drawn over the star field rather than instead of it: its own stars are a
    little brighter and the lines between them barely there, so it emerges
    from the sky rather than being pasted on top of it.
    """

    name = "constellation"

    HOLD = 70.0        # seconds fully visible
    FADE = 14.0        # in and out
    GAP = 130.0        # dark between shapes

    def __init__(self, names: list = None):
        self.names = list(names or CONSTELLATIONS.keys())
        self.name_shown = ""
        self.points: list = []
        self.lines: list = []
        self.phase = "waiting"
        self.timer = random.uniform(10.0, self.GAP)
        self._size = (0.0, 0.0)

    def resize(self, width, height):
        self._size = (width, height)
        if self.points:
            self._place(width, height)

    def _place(self, width, height):
        shape = CONSTELLATIONS.get(self.name_shown)
        if not shape:
            self.points = []
            return
        offsets, lines = shape
        # Sized to a corner of the sky, never over the clock in the middle.
        box = random.uniform(0.20, 0.30) * min(width, height * 1.5)
        left = random.choice([random.uniform(0.04, 0.22),
                              random.uniform(0.66, 0.82)]) * width
        top = random.uniform(0.05, 0.30) * height
        self.points = [(left + ox * box, top + oy * box) for ox, oy in offsets]
        self.lines = lines

    def step(self, dt, width, height):
        self.timer -= dt
        if self.timer > 0:
            return
        if self.phase == "waiting":
            self.name_shown = random.choice(self.names)
            self._place(width, height)
            self.phase = "in"
            self.timer = self.FADE
        elif self.phase == "in":
            self.phase = "hold"
            self.timer = self.HOLD
        elif self.phase == "hold":
            self.phase = "out"
            self.timer = self.FADE
        else:
            self.phase = "waiting"
            self.points = []
            self.timer = random.uniform(self.GAP * 0.6, self.GAP * 1.5)

    def alpha(self) -> float:
        if self.phase == "hold":
            return 1.0
        if self.phase == "in":
            return max(0.0, 1.0 - (self.timer / self.FADE))
        if self.phase == "out":
            return max(0.0, self.timer / self.FADE)
        return 0.0

    def paint(self, painter, width, height):
        fade = self.alpha()
        if fade <= 0.01 or not self.points:
            return

        line = QColor(150, 178, 226, int(58 * fade))
        painter.setPen(QPen(line, 1.1))
        for a, b in self.lines:
            if a < len(self.points) and b < len(self.points):
                painter.drawLine(QPointF(*self.points[a]), QPointF(*self.points[b]))

        painter.setPen(Qt.PenStyle.NoPen)
        for x, y in self.points:
            halo = QColor(206, 224, 255, int(70 * fade))
            painter.setBrush(halo)
            painter.drawEllipse(QPointF(x, y), 4.2, 4.2)
            core = QColor(240, 247, 255, int(225 * fade))
            painter.setBrush(core)
            painter.drawEllipse(QPointF(x, y), 1.5, 1.5)


## ── Rain ─────────────────────────────────────────────────────────────────────

class Rain(Layer):
    """
    Streaks, angled by the actual wind.

    Drawn as lines rather than particles with a trail: a line from where a
    drop is to where it was a frame ago *is* the trail, for one draw call.
    """

    name = "rain"

    def __init__(self, intensity: float = 0.4, wind=(0.0, 0.0)):
        self.intensity = max(0.05, min(1.0, float(intensity)))
        self.wind = wind
        self.drops: list = []
        self._size = (0.0, 0.0)

    def count_for(self, width, height) -> int:
        # Scaled to the screen so a large panel is not sparser than a small
        # one, and capped so a downpour cannot cost more than a drizzle plus a
        # bit.
        base = (width * height) / 26000.0
        return int(max(20, min(220, base * (0.4 + self.intensity))))

    def resize(self, width, height):
        if (width, height) == self._size and self.drops:
            return
        self._size = (width, height)
        self.drops = [self._spawn(width, height, seeded=True)
                      for _ in range(self.count_for(width, height))]

    def _spawn(self, width, height, seeded=False):
        speed = random.uniform(760, 1180) * (0.65 + 0.35 * self.intensity)
        return [
            random.uniform(-width * 0.2, width * 1.2),
            random.uniform(0, height) if seeded else random.uniform(-height * 0.3, 0),
            speed,
            random.uniform(0.35, 1.0),      # opacity
            random.uniform(9.0, 19.0),      # streak length in frames-worth
        ]

    def step(self, dt, width, height):
        drift = self.wind[0] * 620.0 * self.gust()
        for drop in self.drops:
            drop[0] += drift * dt
            drop[1] += drop[2] * dt
            if drop[1] > height + 20 or drop[0] < -width * 0.3 \
                    or drop[0] > width * 1.3:
                fresh = self._spawn(width, height)
                drop[0], drop[1], drop[2], drop[3], drop[4] = fresh

    def paint(self, painter, width, height):
        drift = self.wind[0] * 620.0 * self.gust()
        for x, y, speed, opacity, length in self.drops:
            colour = QColor(170, 200, 240)
            colour.setAlpha(int(150 * opacity))
            painter.setPen(QPen(colour, 1.4))
            # Where it was a moment ago, which is the direction it is going.
            back = length / 1000.0
            painter.drawLine(QPointF(x, y),
                             QPointF(x - drift * back, y - speed * back))


## ── Snow ─────────────────────────────────────────────────────────────────────

class Snow(Layer):
    """
    Flakes, which do not fall straight and do not fall fast.

    Each carries its own sway so a field of them does not move as one sheet -
    that is the thing that makes cheap snow look like a screensaver.
    """

    name = "snow"

    def __init__(self, intensity: float = 0.4, wind=(0.0, 0.0)):
        self.intensity = max(0.05, min(1.0, float(intensity)))
        self.wind = wind
        self.flakes: list = []
        self._size = (0.0, 0.0)

    def count_for(self, width, height) -> int:
        base = (width * height) / 34000.0
        return int(max(18, min(170, base * (0.4 + self.intensity))))

    def resize(self, width, height):
        if (width, height) == self._size and self.flakes:
            return
        self._size = (width, height)
        self.flakes = [self._spawn(width, height, seeded=True)
                       for _ in range(self.count_for(width, height))]

    def _spawn(self, width, height, seeded=False):
        return [
            random.uniform(-width * 0.1, width * 1.1),
            random.uniform(0, height) if seeded else random.uniform(-60, -6),
            random.uniform(26, 74) * (0.7 + 0.3 * self.intensity),  # fall
            random.uniform(1.2, 4.6),        # radius
            random.uniform(0, math.tau),     # sway phase
            random.uniform(0.5, 1.5),        # sway speed
            random.uniform(9, 26),           # sway width
            random.uniform(0.45, 1.0),       # opacity
        ]

    def step(self, dt, width, height):
        drift = self.wind[0] * 190.0 * self.gust()
        for flake in self.flakes:
            flake[4] += flake[5] * dt
            flake[0] += (drift + math.sin(flake[4]) * flake[6]) * dt
            flake[1] += flake[2] * dt
            if flake[1] > height + 8 or flake[0] < -width * 0.2 \
                    or flake[0] > width * 1.2:
                fresh = self._spawn(width, height)
                for i, value in enumerate(fresh):
                    flake[i] = value

    #below this a flake is drawn as a dot: arms on a 2px flake are three
    #draw calls producing one grey pixel
    ARM_ABOVE = 2.1

    def paint(self, painter, width, height):
        for x, y, _fall, radius, phase, _s, _w, opacity in self.flakes:
            colour = QColor(238, 246, 255)
            colour.setAlpha(int(210 * opacity))

            if radius < self.ARM_ABOVE:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(colour)
                painter.drawEllipse(QPointF(x, y), radius, radius)
                continue

            # Six arms, because snowflakes have six. Rotated by the flake's own
            # sway phase so a field of them is not a field of identical stars,
            # and given a small centre so the arms meet in something rather
            # than crossing in a gap.
            painter.setPen(QPen(colour, max(0.9, radius * 0.30)))
            spin = phase * 0.35
            for index in range(3):
                angle = spin + index * (math.pi / 3.0)
                dx, dy = math.cos(angle) * radius, math.sin(angle) * radius
                painter.drawLine(QPointF(x - dx, y - dy), QPointF(x + dx, y + dy))
                # A short cross bar near each tip is what reads as "flake"
                # rather than "asterisk".
                for end in (1, -1):
                    tip_x, tip_y = x + dx * 0.62 * end, y + dy * 0.62 * end
                    barb = radius * 0.34
                    bx, by = math.cos(angle + 1.05) * barb, math.sin(angle + 1.05) * barb
                    painter.drawLine(QPointF(tip_x - bx, tip_y - by),
                                     QPointF(tip_x + bx, tip_y + by))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(QPointF(x, y), radius * 0.30, radius * 0.30)


## ── Hail ─────────────────────────────────────────────────────────────────────

class Hail(Layer):
    """
    Small, hard, fast, and it bounces.

    Distinguished from rain by shape and behaviour rather than colour: rain is
    a streak that vanishes at the bottom, hail is a round stone that hits and
    kicks back up. The bounce is the whole reason it reads as hail at a glance.
    """

    name = "hail"

    def __init__(self, intensity: float = 0.5, wind=(0.0, 0.0)):
        self.intensity = max(0.1, min(1.0, float(intensity)))
        self.wind = wind
        self.stones: list = []
        self._size = (0.0, 0.0)

    def count_for(self, width, height) -> int:
        base = (width * height) / 40000.0
        return int(max(14, min(120, base * (0.5 + self.intensity))))

    def resize(self, width, height):
        if (width, height) == self._size and self.stones:
            return
        self._size = (width, height)
        self.stones = [self._spawn(width, height, seeded=True)
                       for _ in range(self.count_for(width, height))]

    def _spawn(self, width, height, seeded=False):
        return [
            random.uniform(-width * 0.1, width * 1.1),
            random.uniform(0, height) if seeded else random.uniform(-height * 0.25, -8),
            random.uniform(600, 980) * (0.7 + 0.3 * self.intensity),  # fall
            random.uniform(1.4, 3.0),        # radius
            random.uniform(0.6, 1.0),        # opacity
            0,                               # bounces used
        ]

    def step(self, dt, width, height):
        drift = self.wind[0] * 420.0 * self.gust()
        # Where the ground is. Not the very bottom: a stone bouncing off the
        # edge of the screen looks like it hit glass.
        floor = height * 0.965
        for stone in self.stones:
            stone[0] += drift * dt
            stone[1] += stone[2] * dt

            if stone[1] >= floor and stone[2] > 0 and stone[5] < 1:
                # One bounce, losing most of its speed. A stone that bounced
                # twice would still be on screen when the next one landed.
                stone[1] = floor
                stone[2] = -stone[2] * random.uniform(0.22, 0.38)
                stone[5] = 1
            elif stone[5] == 1 and stone[2] < 0:
                # Rising, and gravity takes it back.
                stone[2] += 2400 * dt

            if stone[1] > height + 12 or stone[0] < -width * 0.2 \
                    or stone[0] > width * 1.2:
                fresh = self._spawn(width, height)
                for i, value in enumerate(fresh):
                    stone[i] = value

    def paint(self, painter, width, height):
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, speed, radius, opacity, _bounced in self.stones:
            body = QColor(226, 238, 252)
            body.setAlpha(int(235 * opacity))
            painter.setBrush(body)
            painter.drawEllipse(QPointF(x, y), radius, radius)
            # A bright edge on the leading side, so it looks like ice rather
            # than a pale dot.
            if radius > 1.8:
                shine = QColor(255, 255, 255, int(180 * opacity))
                painter.setBrush(shine)
                painter.drawEllipse(QPointF(x - radius * 0.28, y - radius * 0.3),
                                    radius * 0.34, radius * 0.34)


## ── Lightning ────────────────────────────────────────────────────────────────

class Lightning(Layer):
    """
    A flash behind the cloud, occasionally with a bolt.

    Mostly **sheet lightning** - the whole sky brightening for a moment - and
    only sometimes a drawn bolt. That is both what most lightning looks like
    from indoors and the cheaper thing to draw, and a jagged line every few
    seconds would read as a fault rather than a storm.

    Flashes come in bursts of one to three, a fraction of a second apart, the
    way real ones do. A single clean flash every twenty seconds looks
    mechanical.
    """

    name = "lightning"

    #average seconds between bursts
    AVERAGE_GAP = 22.0
    #chance a burst draws a bolt rather than only lighting the sky
    BOLT_CHANCE = 0.45

    def __init__(self, intensity: float = 1.0):
        self.intensity = max(0.2, min(1.0, float(intensity)))
        self.wait = random.uniform(2.0, self.AVERAGE_GAP)
        self.flashes: list = []      # [remaining, peak]
        self.bolt: list = []         # points, in unit coordinates
        self.bolt_life = 0.0

    def step(self, dt, width, height):
        for flash in self.flashes:
            flash[0] -= dt
        self.flashes = [f for f in self.flashes if f[0] > 0]

        if self.bolt_life > 0:
            self.bolt_life -= dt
            if self.bolt_life <= 0:
                self.bolt = []

        self.wait -= dt
        if self.wait > 0:
            return

        gap = self.AVERAGE_GAP / self.intensity
        self.wait = random.uniform(gap * 0.45, gap * 1.7)

        # One to three flashes, staggered.
        delay = 0.0
        for index in range(random.randint(1, 3)):
            peak = random.uniform(0.35, 1.0) * self.intensity
            if index:
                peak *= random.uniform(0.4, 0.8)
            self.flashes.append([random.uniform(0.09, 0.20) + delay, peak])
            delay += random.uniform(0.06, 0.22)

        if random.random() < self.BOLT_CHANCE:
            self.bolt = self._make_bolt()
            self.bolt_life = random.uniform(0.10, 0.20)

    @staticmethod
    def _make_bolt() -> list:
        """A jagged path down from the cloud, in unit coordinates."""
        x = random.uniform(0.15, 0.85)
        y = random.uniform(0.04, 0.12)
        points = [(x, y)]
        while y < random.uniform(0.42, 0.66):
            x += random.uniform(-0.05, 0.05)
            y += random.uniform(0.05, 0.11)
            points.append((max(0.02, min(0.98, x)), y))
        return points

    def brightness(self) -> float:
        return min(1.0, sum(peak for _left, peak in self.flashes))

    def paint(self, painter, width, height):
        level = self.brightness()
        if level <= 0.01:
            return

        # The sky lights up from the top, where the cloud is.
        wash = QLinearGradient(0, 0, 0, height)
        wash.setColorAt(0.0, QColor(196, 214, 255, int(96 * level)))
        wash.setColorAt(0.45, QColor(176, 198, 245, int(44 * level)))
        wash.setColorAt(1.0, QColor(160, 184, 235, int(10 * level)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(wash)
        painter.drawRect(QRectF(0, 0, width, height))

        if not self.bolt or self.bolt_life <= 0:
            return
        points = [QPointF(x * width, y * height) for x, y in self.bolt]
        # Drawn twice: a wide soft pass for the glow, a thin bright one for
        # the channel itself.
        glow = QColor(200, 218, 255, int(120 * level))
        painter.setPen(QPen(glow, 6.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for a, b in zip(points, points[1:]):
            painter.drawLine(a, b)
        core = QColor(255, 255, 255, int(235 * level))
        painter.setPen(QPen(core, 1.8, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for a, b in zip(points, points[1:]):
            painter.drawLine(a, b)


## ── Cloud ────────────────────────────────────────────────────────────────────

class Clouds(Layer):
    """
    Soft dark masses drifting across the top of the sky.

    Each cloud is **several overlapping lobes**, not one ellipse. A single
    radial gradient reads as a sphere or a smudge, whatever you do to its
    proportions - it is the irregular silhouette that makes something look
    like cloud, and that costs a handful of extra fills rather than anything
    clever.

    The lobes sit on a shared baseline with only a little sag, because cloud
    bottoms are flat and cloud tops are not. Sizes and offsets are fixed at
    build time, so a drifting cloud keeps its shape instead of boiling.
    """

    name = "clouds"

    #where the band of cloud sits, as a fraction of the page height
    TOP = 0.0
    BOTTOM = 0.24

    def __init__(self, cover: float = 0.5, wind=(0.0, 0.0)):
        self.cover = max(0.0, min(1.0, float(cover)))
        self.wind = wind
        self.clouds: list = []
        self._size = (0.0, 0.0)

    def resize(self, width, height):
        if (width, height) == self._size and self.clouds:
            return
        self._size = (width, height)
        count = int(2 + round(self.cover * 4))
        self.clouds = []
        for _ in range(count):
            span = random.uniform(0.26, 0.52) * width
            body = random.uniform(0.055, 0.105) * height
            lobes = []
            # A run of lobes along the baseline, tallest near the middle, so
            # the silhouette rises and falls instead of being one dome.
            for index in range(random.randint(4, 7)):
                across = random.uniform(-0.5, 0.5)
                middle = 1.0 - abs(across) * 1.35
                radius = body * random.uniform(0.62, 1.18) * max(0.42, middle)
                lobes.append((
                    across * span,                       # x offset
                    -radius * random.uniform(0.12, 0.55),  # lifted off the base
                    radius * random.uniform(1.25, 2.0),  # width
                    radius,                              # height
                ))
            self.clouds.append([
                random.uniform(-0.1, 1.1) * width,
                random.uniform(self.TOP, self.BOTTOM) * height + body,
                random.uniform(0.09, 0.24),              # opacity
                random.uniform(3.0, 12.0),               # own drift px/s
                span,
                lobes,
            ])

    def step(self, dt, width, height):
        drift = self.wind[0] * 60.0 * self.gust()
        for cloud in self.clouds:
            cloud[0] += (drift + cloud[3]) * dt
            span = cloud[4]
            if cloud[0] - span > width * 1.25:
                cloud[0] = -span - random.uniform(0, width * 0.3)
            elif cloud[0] + span < -width * 0.25:
                cloud[0] = width * 1.25 + random.uniform(0, width * 0.3)

    def paint(self, painter, width, height):
        painter.setPen(Qt.PenStyle.NoPen)
        for x, base, opacity, _drift, _span, lobes in self.clouds:
            for offset_x, offset_y, lobe_w, lobe_h in lobes:
                centre = QPointF(x + offset_x, base + offset_y)
                radius = max(lobe_w, lobe_h)
                gradient = QRadialGradient(centre, radius)
                inner = QColor(126, 138, 162)
                inner.setAlpha(int(255 * opacity))
                middle = QColor(126, 138, 162)
                middle.setAlpha(int(255 * opacity * 0.45))
                edge = QColor(126, 138, 162, 0)
                gradient.setColorAt(0.0, inner)
                gradient.setColorAt(0.55, middle)
                gradient.setColorAt(1.0, edge)
                painter.setBrush(gradient)
                painter.drawEllipse(centre, lobe_w, lobe_h)


## ── Fog ──────────────────────────────────────────────────────────────────────

class Fog(Layer):
    """
    Drifting banks of haze. Only ever from a WMO fog code, never guessed.

    Cloud cover plus low wind is not fog, and a panel claiming fog on a clear
    cold night is worse than one that never mentions it.

    **Blobs, not bands.** This was horizontal gradient rectangles, and a
    rectangle with a soft left and right edge still has a hard top and bottom -
    which on a near-black page at low brightness is a visible seam right across
    the screen. Overlapping wide radial blobs have no edge to band along.
    """

    name = "fog"

    def __init__(self, wind=(0.0, 0.0), density: float = 1.0):
        self.wind = wind
        self.density = max(0.2, min(1.6, float(density)))
        self.banks: list = []
        self._size = (0.0, 0.0)

    def resize(self, width, height):
        if (width, height) == self._size and self.banks:
            return
        self._size = (width, height)
        self.banks = []
        for _ in range(9):
            self.banks.append([
                random.uniform(-0.2, 1.2) * width,
                # Low: fog sits on the ground, and haze across a clock face is
                # only annoying.
                random.uniform(0.45, 1.05) * height,
                random.uniform(0.24, 0.52) * width,      # radius across
                random.uniform(0.06, 0.15) * height,     # radius down
                random.uniform(0.045, 0.10) * self.density,
                random.uniform(3.0, 11.0),               # drift
            ])

    def step(self, dt, width, height):
        drift = self.wind[0] * 34.0 * self.gust()
        for bank in self.banks:
            bank[0] += (drift + bank[5]) * dt
            if bank[0] - bank[2] > width * 1.3:
                bank[0] = -bank[2] - random.uniform(0, width * 0.2)
            elif bank[0] + bank[2] < -width * 0.3:
                bank[0] = width * 1.3 + random.uniform(0, width * 0.2)

    def paint(self, painter, width, height):
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, across, down, opacity, _drift in self.banks:
            radius = max(across, down)
            gradient = QRadialGradient(QPointF(x, y), radius)
            inner = QColor(196, 206, 222, int(255 * opacity))
            mid = QColor(196, 206, 222, int(255 * opacity * 0.5))
            edge = QColor(196, 206, 222, 0)
            gradient.setColorAt(0.0, inner)
            gradient.setColorAt(0.5, mid)
            gradient.setColorAt(1.0, edge)
            painter.setBrush(gradient)
            painter.drawEllipse(QPointF(x, y), across, down)


## ── Choosing ─────────────────────────────────────────────────────────────────

#WMO codes worth reacting to. Everything else falls back to the raw amounts,
#which are what the panel had before weather_code was requested.
FOG_CODES = {45, 48}
THUNDER_CODES = {95, 96, 99}
DRIZZLE_CODES = {51, 53, 55, 56, 57}
#thunderstorm WITH hail, and the two ice-pellet codes
HAIL_CODES = {96, 99, 77}

#Fahrenheit throughout, converted at the boundary. The reading arrives in
#whichever unit the weather setting asked for, and a bare `temperature <= 32`
#means freezing in one and a warm afternoon in the other - so every comparison
#in here is against a known scale rather than against whatever turned up.
FREEZING_F = 32.0
#below this fireflies are not flying. They are insects, and a firefly over
#frost is a stranger sight than no fireflies at all.
FIREFLY_MIN_F = 45.0
WARM_F = 75.0


def to_fahrenheit(value: float, unit: str = "fahrenheit"):
    """Whatever the API returned, in Fahrenheit. None stays None."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if str(unit or "").strip().lower().startswith("c"):
        return value * 9.0 / 5.0 + 32.0
    return value


def condition(weather: dict) -> str:
    """
    The single strongest thing the sky is doing.

    One word for a caption, unlike describe(), which lists everything at once.
    Ordered by what somebody would name first if asked what it was doing
    outside: they would say "hailing", not "hailing and overcast".
    """
    if not weather:
        return "unknown"

    def value(key):
        try:
            return float(weather.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    code = int(value("weather_code"))
    rain_amount = value("rain") + value("showers")

    if code in HAIL_CODES:
        return "hail"
    if code in THUNDER_CODES:
        return "thunderstorms"
    if value("snowfall") > 0:
        return "snow"
    if code in FOG_CODES:
        return "fog"
    if code in DRIZZLE_CODES:
        return "drizzle"
    if rain_amount > 0 or value("precipitation") > 0:
        return "heavy rain" if rain_amount >= 0.2 else "rain"

    cover = value("cloud_cover")
    if cover >= 85:
        return "overcast"
    if cover >= 35:
        return "partly cloudy"
    if value("wind_speed_10m") >= 25:
        return "windy"
    return "clear"


def describe(weather: dict) -> str:
    """A short line for the page, and for anyone debugging the choice."""
    if not weather:
        return ""
    parts = []
    code = int(weather.get("weather_code") or 0)
    if code in FOG_CODES:
        parts.append("fog")
    if float(weather.get("snowfall") or 0) > 0:
        parts.append("snow")
    elif float(weather.get("rain") or 0) > 0 or \
            float(weather.get("showers") or 0) > 0:
        parts.append("thunderstorms" if code in THUNDER_CODES else
                    "drizzle" if code in DRIZZLE_CODES else "rain")
    cover = float(weather.get("cloud_cover") or 0)
    if not parts:
        parts.append("clear" if cover < 35 else
                     "partly cloudy" if cover < 80 else "overcast")
    wind = float(weather.get("wind_speed_10m") or 0)
    if wind >= 18:
        parts.append("windy")
    return ", ".join(parts)


def layers_for(weather: dict, fireflies: bool = True,
               firefly_count: int = 16, events: bool = True,
               moon: bool = True, when=None,
               unit: str = "fahrenheit") -> list:
    """
    The stack to draw, back to front.

    Composed rather than chosen: an overcast night that is also raining and
    also blowing a gale is three things at once, and picking one of them would
    throw away the two that make it look like weather.
    """
    if not weather:
        return [Fireflies(firefly_count)] if fireflies else []

    def value(key):
        try:
            return float(weather.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    code = int(value("weather_code"))
    cover = max(0.0, min(100.0, value("cloud_cover")))
    wind = wind_vector(value("wind_speed_10m"), value("wind_direction_10m"))

    rain_amount = value("rain") + value("showers")
    snow_amount = value("snowfall")
    hailing = code in HAIL_CODES
    raining = (rain_amount > 0 or code in DRIZZLE_CODES
               or code in THUNDER_CODES) and not hailing
    snowing = snow_amount > 0 and not hailing
    foggy = code in FOG_CODES

    layers: list = []

    # The moon goes behind everything, because everything else is nearer than
    # it is. Hidden under heavy cloud, which is what actually happens.
    if moon and cover < 75 and not (raining or snowing or hailing or foggy):
        try:
            from .astronomy import moon_phase, moon_illumination, moon_waxing
            layers.append(Moon(moon_phase(when), moon_illumination(when),
                               moon_waxing(when)))
        except Exception:
            pass

    # Stars first, thinned by cloud. Below a sliver there is no point drawing
    # any: a handful of dots on an overcast sky reads as dust on the screen.
    star_count = int(70 * max(0.0, 1.0 - (cover / 85.0)))
    clear_sky = not (raining or snowing or foggy or hailing)
    if star_count >= 12 and clear_sky:
        layers.append(Stars(star_count))
        # Only where there are stars to see them among. A shooting star over
        # an overcast sky is a bright line with no explanation.
        if events:
            layers.append(Constellation())
            layers.append(ShootingStar())

    if cover >= 25 or raining or snowing or hailing:
        layers.append(Clouds(cover / 100.0, wind))

    if hailing:
        layers.append(Hail(min(1.0, max(rain_amount, 0.2) / 0.5), wind))
    elif snowing:
        layers.append(Snow(min(1.0, snow_amount / 0.4), wind))
    elif raining:
        intensity = min(1.0, max(rain_amount, value("precipitation")) / 0.25)
        if code in DRIZZLE_CODES:
            intensity = min(intensity, 0.35)
        layers.append(Rain(max(0.15, intensity), wind))

    if code in THUNDER_CODES:
        # Over the rain, because a flash lights the rain in front of it.
        layers.append(Lightning(1.0 if code == 95 else 1.25))

    if foggy:
        # 48 is depositing rime fog - thicker than plain fog, and the only
        # distinction the codes give.
        layers.append(Fog(wind, density=1.35 if code == 48 else 1.0))

    # Below freezing, a sparkle at the edges. Not during rain, which would be
    # sleet and is a different thing.
    temperature = to_fahrenheit(weather.get("temperature_2m"), unit)
    if temperature is not None and temperature <= FREEZING_F and not raining:
        layers.append(Frost(min(1.0, (FREEZING_F - temperature) / 25.0)))

    # Last, and only on a night worth being outside on. Fireflies in a
    # downpour would be a lie, in a blizzard an absurd one, and on a frozen
    # night simply wrong - they are insects, and they are not out.
    warm_enough = temperature is None or temperature >= FIREFLY_MIN_F
    if fireflies and clear_sky and warm_enough and cover < 70:
        layers.append(Fireflies(firefly_count, wind))

    return layers


def gusts_for(weather: dict) -> Gusts:
    """The shared surge source for a set of layers."""
    if not weather:
        return Gusts(0, 0)
    def value(key):
        try:
            return float(weather.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
    return Gusts(value("wind_speed_10m"), value("wind_gusts_10m"))


def sky_colours(weather: dict, unit: str = "fahrenheit") -> tuple:
    """
    Top and bottom of the background gradient.

    Nudged by temperature, because a freezing night and a warm one do not look
    the same and it is nearly free to say so. Overcast lifts the floor a
    little - a cloudy sky is never as black as a clear one.
    """
    top = [6, 7, 11]
    bottom = [14, 17, 26]
    if not weather:
        return top, bottom

    try:
        cover = float(weather.get("cloud_cover") or 0)
    except (TypeError, ValueError):
        return top, bottom
    temperature = to_fahrenheit(weather.get("temperature_2m"), unit)
    if temperature is None:
        temperature = 55.0

    # Below freezing bluer, above 75F warmer. Small numbers on purpose.
    if temperature <= FREEZING_F:
        shift = min(1.0, (FREEZING_F - temperature) / 30.0)
        bottom = [int(bottom[0]), int(bottom[1] + 4 * shift),
                  int(bottom[2] + 12 * shift)]
    elif temperature >= WARM_F:
        shift = min(1.0, (temperature - WARM_F) / 25.0)
        bottom = [int(bottom[0] + 10 * shift), int(bottom[1] + 4 * shift),
                  int(bottom[2])]

    lift = int(6 * min(1.0, cover / 100.0))
    top = [c + lift for c in top]
    bottom = [c + lift for c in bottom]
    return top, bottom
