# Random Chance

Settling something by chance, drawn on the screen rather than reported as a
number. A coin flip and dice so far; a roulette wheel comes later.

## Flipping a coin

Say **"flip a coin"** — or "toss a coin", "coin flip", "heads or tails". The
coin appears over whatever page is up, turns end over end, lands, and only
then is the result shown on a banner and said out loud.

It draws on an overlay rather than on the home page. A flip asked for by
voice happens wherever the panel happens to be, and a home-screen widget only
exists while the home screen is on it. Nothing it puts up takes a tap, so the
page underneath stays usable while the coin is in the air.

**The crown side is heads. The plain side is tails.** One motif rather than
two: telling a crown from a blank face is easier at a distance than telling
two pictures apart, so the moment it lands is readable without waiting for
the banner.

## Rolling dice

Say **"roll a d20"**, **"roll 2d6"**, **"roll 2 d20s and a d10"**, or just
**"roll the dice"** for a random one from the standard set. Spoken numbers
work as well as digits, "20 sided die" works as well as "d20", and a phrase
can carry more than one group.

The dice tumble in from the edges of the screen and settle one after another.
When the last one lands the total is shown large, with what each die showed
underneath it — the total is the answer, and the breakdown is how it got
there.

**Each die is drawn as the solid it is**, not as a polygon with a number in
it. What makes a d20 look like a d20 is that the shape you see and the shape
you *read* are different: the silhouette is a hexagon, the face carrying the
number is a triangle in the middle of it, and the edges between them are the
other faces turned away. Every type is built the same way — a d12 is a
decagon with a pentagon face, a d10 a six-sided outline with a long kite down
the middle, a d4 a triangle with the inverted face of a tetrahedron inside
it. **A d6 gets pips**, because nothing says die faster and a cube face is
the one face everybody can already picture.

The number is fitted to the *face* rather than to the die. Sizing it to the
die is what made every shape look alike — the digits grew until they covered
the facets, and all that was left to tell them apart was the outline and the
colour.

Colour goes with the type too, so a handful that lands at once can be read at
a glance: the teal ones are d20s and the blue ones d6s, however many there
are.

A die with no entry of its own still gets a shape and a colour worked out
from its size: a d5 is a pentagon and a d7 a heptagon, in a hue derived from
the number of sides so it is the same every time. Past a dozen sides a
polygon stops being countable at a glance and it is drawn as a circle.

**Every die rolled is a die drawn.** The tray shrinks them to fit and the
grid follows its shape rather than being square, and once they hit their
minimum size the spacing closes instead — so a big roll crowds and overlaps
rather than being trimmed. A number beside a picture of only some of the
dice was the worse answer.

### They bump into each other

Dice that overlap shove each other apart, and a hard enough collision late in
a flight **knocks a die back into rolling**: it stops travelling, tumbles
where it was hit, and comes to rest there rather than carrying on to the spot
it was headed for.

**A knock never changes a value.** Every face was decided before any of this
was drawn, so a die clattered at the last moment tumbles again and lands on
exactly the number it was always going to. What a collision changes is how
long it takes and where on the tray it ends up. Anything else would make the
drawing the thing that decides the roll, and then a dropped frame would be a
different result.

Only late in a flight, or a die knocked on the way in would drop half a tray
from where it was going and the whole roll would pile up wherever the traffic
was worst. A die can be knocked twice at most and there is a hard ceiling on
the whole roll besides, because a crowded tray can otherwise keep re-opening
itself. `animation.dice_collide` turns it off.

A quiet roll is unaffected — three dice on a wall panel never come near each
other.

### "Roll" is also half of "rock and roll"

The skill anchors on the word *roll*, which means dice and also means music.
A phrase carrying the word that names no dice and no notation is handed back
rather than answered, so "put on some rock and roll" and "ride the roller
coaster" carry on to whatever else wants them. The command alone — just
"roll" — is still a request.

*roller* is an anchor too. It is not a word anybody says; it is what the
transcriber writes for "roll a", and fuzzy repair cannot reach it because one
word being a prefix of another is refused as a mishearing.

## From a phone

**Random Chance** is a card on the panel's dashboard, at `/`, alongside the
timer and calendar forms — not a URL you have to remember. It serves from
`/public/randomchance_page`. Two tabs, and the panel does the showing: the
phone is the control surface.

**Coin.** Name what is being decided and what the two sides stand for. The
title appears first, then the coin flips, then the side that won — by the
name you gave it. The coin itself is the same drawing either way; a label
belongs on a banner, where there is room to read it, not on a disc that
spends most of the flip edge on.

**Dice.** Tap a die type to add one, then − and + on its chip to change how
many of that type, or × to take it off. The tray shows what is picked as
`3d20`, so going from five to three is two taps rather than clearing it and
starting again.

Then **outcomes**: a list of rules read against the total, each one *over* or
*under* a number with a line to show if it holds.

The rules are checked **in order, and the first one that matches is the one
shown** — so the list is a sequence rather than a set, and a rule you want to
win goes above the looser one that would also match. They live as long as the
page is open and are sent with the roll; nothing is stored.

**A threshold has to be one the dice can actually reach**, and the usable
range is not the same for both comparisons. On 2d6 the total runs 2 to 12,
but *over 12* can never hold and *over 1* holds every time — so *over* is
capped at 11 and *under* floored at 3. The number field carries that range
and moves a value into it when the dice change underneath it; the panel drops
anything outside it as well, so the two agree.

Both ends matter, for different reasons. A threshold that can never be
reached is merely useless. One that is always reached is worse: it wins every
roll and quietly kills every rule below it, which reads as those rules being
broken rather than as the first one always winning.

**Nothing resets when you flip or roll.** The page posts in the background
and only the banner changes — the dice and the rules are still there for the
next one.

**The phone is not told the answer.** It says what it started, not how it
came out. The result exists before anything is drawn, so it could be sent
back immediately — and reading "Chris" off a phone while the coin is still
turning spoils the only interesting second of it. The panel is where the
answer is.

An outcome is shown *after* the total, not instead of it. A rule is a reading
of the number, and replacing the number with it hides the thing being read.

## The wheel

A wheel is a name and a list of items. Each item is on or off and holds a
**percentage** of the wheel, and the percentages of the enabled items always
come to 100 — so setting one to 40 moves the others, because there is only
ever 100 to go round.

Two behaviours worth knowing, both deliberate:

- **A wheel with everything at zero reads as equal**, not as unspinnable.
  Turning everything down is what somebody means by "no preference".
- **An item on 0% is on the list and not on the wheel.** It cannot be landed
  on, which is what setting it to zero means.

Disabled items are gone from the wheel and from the arithmetic. Slice colours
come from the item's own label rather than being stored, so a wheel looks
like itself every time and deleting one item does not reshuffle everybody
else's colour.

The pointer does not move. A wheel with a travelling pointer has two things
to watch and no fixed place to look, and the moment worth watching is the
wheel slowing under a mark that has been there the whole time.

Wheels are saved to `wheels.json` in the user data directory — not the
plugin's `settings.json`, which ships with the app and would be replaced by
an update.

```python
client.public.randomchance["spin"](wheel_id="w123")
client.public.randomchance["spin"](items=[...], title="Who buys lunch?")
```

`spin()` returns the winner immediately and publishes `on_wheel_spin`:

| Key      | Is                                          |
|----------|---------------------------------------------|
| `winner` | `{"label", "share", "id"}`, or `None`       |
| `items`  | The slices as drawn, shares summing to 100  |
| `title`  | The wheel's name, or one that was given     |

`client.public.randomchance["wheels"]` is the store itself, for anything that
wants to read or add one.

## How long the answer stays up

**What is on the stage decides it.** A coin is one face and a word; forty
dice are a total and a breakdown line, and the three seconds that is generous
for the first is not enough to find your own die in the second.

So the hold is `result_ms` for one thing, plus `result_per_item_ms` for each
one after it, capped at `result_max_ms` — one rule for every kind of chance
rather than a number per caller. A roulette wheel will use the same rule
without knowing anything about it.

| On the stage | Held for |
|--------------|----------|
| a coin       | 2.4s     |
| 6 dice       | 3.0s     |
| 20 dice      | 4.7s     |
| 60 dice      | 6.5s     |

An outcome banner has its own `outcome_ms` and does not scale — it is one
line however much was rolled to reach it.

**A wheel is the exception.** It is one name to read whatever its slice
count, so it does not scale — but it gets `wheel_bonus_ms` on top, because
everybody watching wants a moment on the wheel itself once it has stopped.
The bonus is still subject to the ceiling: a caller can ask for longer
without being able to ask for forever.

### The wheel tab

Pick a saved wheel or start a new one. Each row is an item: a **On/Off**
toggle, its name, and its share of the wheel. Below them, **Enable all**,
**Disable all** and **Equal chances**.

Turning an item off leaves it on the list and takes it off the wheel — which
is what "just not this week" means, and why it is a toggle rather than a
delete. Turning it back on gives it a share again, since coming back at 0%
would be a slice that cannot be landed on.

**Changing one percentage moves the others**, because there is only ever 100
to go round. What is left is shared out in proportion to what the other items
already had, so the shape of the rest of the wheel survives an edit to one
row of it.

**Spin saves first.** The wheel on screen is the one somebody just edited,
and spinning a different one than the one in front of them — or losing the
edit because they spun instead of saving — are both worse than a write nobody
asked for. **Save** is there for when you are not spinning yet, and **Delete**
removes a saved wheel.

A wheel needs two items turned on before it can be spun. One item is not a
chance.

## Every result is kept

Flips and rolls go into the notification history, with the title if one was
given. No toast: the result is already on screen in a banner, and a toast
beside it is the same answer twice. The banner is gone in three seconds and
the record is not, which is the point — turn it off with
`notify.remember_results`.

## The outcome is decided first

Every face and every value is chosen before the animation starts, and the
animation is choreographed to arrive at them. A dropped frame, a slow panel,
or animation turned off entirely cannot change what was rolled — and `flip()`
and `roll()` both hand the answer back straight away, while it is still in
the air.

That is also the trap in it. Anything *announcing* the outcome has to wait
for the drawing, not for the decision: spoken replies fire when the coin
lands and when the last die settles, or the panel calls the result while it
is still turning. The notification history is the exception and is written at
once, because a record is not an announcement.

## For other plugins

```python
if client.public.has("randomchance"):
    result = client.public.randomchance["flip"](
        title="Who goes first?", heads="Colin", tails="Chris")
```

`flip()` returns `"heads"` or `"tails"` — the raw face, so a caller comparing
outcomes does not have to know what the sides were called this time. `title`,
`heads` and `tails` are all optional; with none of them it is an ordinary coin
that says Heads or Tails.

`announce=False` shows the flip without speaking it.

```python
outcome = client.public.randomchance["roll"]("2d20 and a d10")
outcome = client.public.randomchance["roll"](groups=[(2, 20), (1, 10)])
```

`roll()` takes what somebody said, or `groups` as `[(count, sides), ...]` for
a caller that already knows what it wants and would rather not have it read
out of English. `outcomes` reads the total:

```python
client.public.randomchance["roll"](groups=[(1, 20)], outcomes=[
    {"op": "greater", "value": 15, "text": "You may enter"},
    {"op": "less",    "value": 5,  "text": "The door holds"},
])
```

It returns a dict and publishes the same one as `on_dice_roll`:

| Key      | Is                                             |
|----------|------------------------------------------------|
| `total`  | Every die added up                             |
| `rolls`  | `{"sides": n, "value": n}` per die, in order   |
| `groups` | `{"count": n, "sides": n}` as asked for        |
| `detail` | The breakdown line, or empty for a single die  |
| `title`  | The question, if one was asked                 |
| `outcome`| The rule that matched, or `None`               |

Nothing about a roll declines: an unparseable spec falls back to a random
standard die, because a bare "roll the dice" is a complete request.

It also publishes `on_coin_flip`:

```python
client.subscribe_to_event("on_coin_flip", lambda event: ...)
```

| Key       | Is                                     |
|-----------|----------------------------------------|
| `result`  | `heads` or `tails`                     |
| `label`   | What that side was called this time    |
| `heads`   | The name given to heads                |
| `tails`   | The name given to tails                |
| `title`   | The question, if one was asked         |

## Settings

| Setting                  | Does                                                   |
|--------------------------|--------------------------------------------------------|
| `animation.enabled`      | Off shows the result immediately, with no flip or roll.|
| `animation.flip_ms`      | How long the coin is in the air.                       |
| `animation.roll_ms`      | How long the dice tumble before the first one settles. |
| `animation.spin_ms`      | How long the wheel turns before it stops.              |
| `animation.dice_collide` | Let dice shove and knock each other. Never changes a result. |
| `animation.frame_ms`     | Time between frames. Raise it on a panel that stutters.|
| `stage.title_ms`         | How long a title is held before the coin appears.      |
| `stage.result_ms`        | How long the result stays up for one thing.            |
| `stage.result_per_item_ms`| Added for each thing after the first.                 |
| `stage.result_max_ms`    | The longest it will ever hold.                         |
| `stage.wheel_bonus_ms`   | Added to a wheel's result, to look at where it stopped.|
| `stage.outcome_ms`       | How long an outcome banner stays up after it.          |
| `speech.speak_result`    | Say the result as well as showing it.                  |
| `notify.remember_results`| Keep every flip and roll in the notification history.  |
