# The action tile

A tile that runs something registered on this panel, and shows what came back.

It is deliberately general: anything callable that any registry knows about
can be pointed at. That makes it powerful and makes it possible to point it at
something unsuitable, so the dialogs say what each thing answers with rather
than filtering the list down to what is convenient.

**If you need an exact behaviour, write a plugin.** This is for reaching things
that already exist; a widget or endpoint of your own will always serve a
specific need better than a general tool bent towards it.


## What it can run

Three sources, listed in this order because that is the order somebody reaches
for them.

| Badge               | Where from                                                                                         |
|---------------------|----------------------------------------------------------------------------------------------------|
| `Panel`             | The panel's own HTTP routes — `/process`, `/say`, `/dashboard/state`, `/notify`, and a dozen more. |
| `Endpoint` / `Page` | Endpoints plugins registered with `client.API.register()`. `Page` means it answers with HTML.      |
| `Public`            | Functions plugins published with `client.public.expose()`.                                         |

Only **callables**. A registry holds values too — the calendar exposes its
sticker store — and a tile that "runs" one of those has nothing to run.

The panel's own routes are described by hand in `action_sources.CORE_ROUTES`.
They are Flask views closed over the client, so `inspect.signature` sees a
function of no arguments while the real ones arrive in the query string;
listing them is the only way to say what they take. Adding one is a line in
that list.


## Arguments

Read from the function's signature: names, defaults, and whether each is
required. Nobody types an argument name they cannot see.

The kind is inferred — `dim: int = 0` is a number, `force=False` a boolean,
`opts: dict = None` an object. An object holds the same rows one level down,
to four levels, and each field removes itself.

Anything added by hand is passed as a keyword, which only works if the
function accepts one. That is said where it is typed rather than discovered
by it not working.


## Trying it

There is no dry run. An endpoint that posts, posts; a function that turns the
lights off turns them off. The button says so.

**Everything is written to the tile before the button is pressed.** Running an
action can open a page, restart the panel, or take the screen somewhere else,
and the answer to that should be "come back and carry on" rather than "start
again". The pencil on the tile's chrome reopens the dialog where it was left.

What came back is shown in full, fixed-width, in a column of its own — what is
being read there is a shape, which keys sit inside which, and proportional
text is what hides that.


## Reading a value out of it

A dotted path into the answer: `weather.today.high`, `sensors.0.name`. A number
is an index and anything else is a key. Empty means the whole answer, which is
what a bare `true` or `"armed"` wants.

The paths offered under **Which part the rules look at** come from the last
real answer, so they are the shape that actually came back rather than a guess.

`follow()` returns `(found, value)` rather than raising or answering `None`: a
path reaching a real null is a different thing from one reaching nothing, and a
tile showing "off" has to tell them apart.


## How it looks

A list of rules, tried top to bottom. The first whose condition holds decides
the tile's name, icon, icon colour, border and fill; anything below it is not
consulted.

Ordered rather than scored. First-match-wins is the only arrangement somebody
can reason about without reading all of them, and it makes "anything else" a
rule at the bottom rather than a special case in the code.

| Test                            | Holds when                                              |
|---------------------------------|---------------------------------------------------------|
| `is on` / `is off`              | The value reads as true or false. An empty list is off. |
| `is exactly` / `contains`       | Compared as text, case-insensitively.                   |
| `is more than` / `is less than` | Both sides read as numbers, or it does not hold.        |
| `is not there` / `is there`     | Whether the path resolved at all.                       |
| `anything`                      | Always. The bottom of a list.                           |

**A missing field is not "off".** A rule meaning that must say `is not there`,
or a typo'd path quietly reads as a state. It is offered as a third suggestion
beside On and Off for exactly that reason — a field that has stopped being
there usually means whatever answers this has stopped answering.

What a rule leaves empty falls back to the tile's own, so a rule that only
changes the colour does not restate the icon and the name.

A name may contain `{value}`, replaced by whatever that rule's path read — so
`{value}C` on a temperature shows `22C`. Floats drop trailing zeroes, booleans
read yes/no, and an object shows its size rather than smearing itself across
the tile.

A rule that cannot be judged is skipped rather than taking the tile down.


## Running on a timer

A checkbox in the left panel, with an interval from 15 seconds to 15 minutes.
The tile's `tick()` honours that interval — the grid ticks every tile far more
often, and a tile calling an endpoint on every tick is a tile hammering
something that did not ask to be hammered.

A poll runs quietly: a failure turns the tile amber rather than raising a
notification every half minute.

Not offered for something that answers with a page, since polling one would
keep opening it. Offered for something that changes the world, with a warning
that it will do that every time.


## Calling the panel's own routes

Through Flask's test client, not over the network. The same view function runs,
with no round trip to 127.0.0.1.

Every route wants a device token, and the panel is not a device — it holds one
of its own, made fresh each run and kept only in memory. See `users.md` for why
borrowing an approved device's token was rejected.
