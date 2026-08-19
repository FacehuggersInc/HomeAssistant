# Browsing

A file picker that opens behind the panel is a file picker nobody can use.
`QFileDialog` is a native window, and on a single-monitor panel running
fullscreen it opens underneath the app, where it cannot be seen or reached.

The panel has its own: `ItemGridDialog` with browsing switched on.

---

## From the client

```python
client.pick_file(on_chosen=lambda path: ..., start="~/Pictures")
client.pick_folder(on_chosen=lambda path: ...)
client.browse(on_chosen=lambda paths: ..., select="file", multiple=True)
```

The handler is given a **path as a string**, or a list of them when
`multiple`. A caller wanting a file wants a file; knowing what a `GridItem` is
to get one is an implementation detail escaping.

**Nothing is called back when it is cancelled.** A picker that answers with
nothing is one every caller has to guard, and none of them would.

| Argument    |                                                                                 |
|-------------|---------------------------------------------------------------------------------|
| `on_chosen` | Given a path, or a list of paths.                                               |
| `start`     | Where to open. Defaults to home. A missing one falls back rather than refusing. |
| `select`    | `"file"`, `"folder"` or `"both"`.                                               |
| `multiple`  | Several at once.                                                                |
| `title`     | Optional. There is a sensible one per `select`.                                 |

## The gesture

**One tap selects. Two taps open a folder. Always.**

The gesture cannot depend on what is being picked. A folder that opens on one
tap in one dialog and selects on one tap in another is two rules to learn from
the same-looking screen, and the one somebody learns first is the one they are
wrong about in the other.

So in a **file** picker, a single tap on a folder selects nothing and says
*Double tap to open* - which teaches the gesture rather than doing nothing at
all. In a **folder** picker, a single tap selects it and a double still walks
in.

`_Tile` and `_Row` both implement `mouseDoubleClickEvent`, because Qt sends
that **instead of** a second press: a widget watching only press and release
hears one tap where somebody made two.

## Choosing where you are standing

A folder picker with nothing selected answers with the folder on screen, and
**its confirm button is live from the moment one opens**. That is what walking
into it meant, and making somebody tap it in its own parent first is a step
that exists only because the code found it easier.

Waiting for a tap would also mean a folder with nothing in it could never be
chosen - and an empty folder is exactly the one somebody has just made to put
something in.

A file picker stays dead until something is picked: there is nothing that
standing in a folder means for it.

## What is switched off

`browse=None` is the dialog every other caller already uses: a fixed set of
items, no path bar, no shortcuts, no hidden toggle, closable by tapping away.
**Sorting stays** - it is the part of browsing a plain picker still wants.

A picker given `browse=` but `show_hidden_toggle=False` gets the rest without
the toggle.

## Rules worth knowing

* **A selection is dropped when you leave the folder.** Carrying it across
  means confirming files somebody can no longer see.
* **Search runs from the current folder downwards**, never above it. Results
  from outside are results nobody can place. It is bounded by count, time and
  depth, and says which bound it hit - a search that quietly returns the first
  four hundred of nine thousand tells somebody their file is not there.
* **Tapping outside does not close it.** The taps that miss a tile are exactly
  the ones that land on the blocker, and losing the dialog to one costs
  everywhere somebody had walked to. Cancel is a button.
* **A folder that will not open says so and stays put.** Closing, or jumping
  elsewhere, takes somebody further from what they were doing.

## Pictures arrive after the grid

Reading and scaling a 1200x800 photo costs about **12ms**; building the tile
that holds it costs **0.09ms**. Decoding eighty photos on the UI thread is
therefore a second of nothing on screen, and a re-sort is another one.

The tiles are built with their placeholder and the decoding is handed to a
thread pool. A `QPixmap` can only be made on the UI thread, so the worker
returns a `QImage` - already scaled, since scaling is not the cheap half.

Every rebuild bumps a generation counter and clears the queue. A picture
decoded for the folder somebody has since left is dropped on arrival rather
than drawn into the folder they walked into. Whatever was already running
finishes and is thrown away, because waiting for it is the pause this exists
to remove.

Measured against a blocking decode, 120 items of which 80 are photos:

|                   | Before  | After  |
|-------------------|---------|--------|
| Opening a folder  | 1074 ms | 236 ms |
| Changing the sort | 847 ms  | 48 ms  |

## The rail

**It scrolls.** A `QVBoxLayout` given less height than its children need does
not shrink them - they have fixed heights - it draws them on top of each
other. Three groups plus a couple of drives is taller than a dialog on a
600px panel, so the column lives in a scroll area.

The search glyph is a child `QLabel`, not `addAction()`. Qt sizes an action's
icon from the style - about 16px, which on a 46px field reads as a speck - and
there is no way to ask it for a bigger one. The field's text margin is moved
over to make room.

`style_scrollbar()` is called **after** the panel styling, never before.
`setStyleSheet` replaces and `style_scrollbar` appends, so the other order
wipes the shared bar and the rail gets the platform's own - which is the trap
that function's own docstring describes.

**A rail button has a fixed width.** A `QPushButton`'s size hint is its text
plus its chrome, and a layout never gives a widget less than its hint - so one
long asset name makes the column wider than the rail, whatever the rail's
width is, and the rail scrolls sideways. Widening it does not help: the next
name is longer. Eliding alone helps only until a theme's font is wider than
the one the elision was measured against.

`_rail_button_width` is `RAIL_WIDTH - RAIL_INSET`, and labels are elided to
that minus `RAIL_PADDING`. The column is then the same width on any theme, and
the whole name is in the tooltip alongside the path.

`RAIL_INSET` covers the panel border, the column's margins and the scrollbar,
which is drawn inside the viewport and so takes its width from the buttons -
which is also why the rail is wider than its buttons need.

**Rows carry no margins of their own.** A `QHBoxLayout` defaults to 9px all
round, so each row put 18px between itself and the next - four times the
dialog's own spacing, and nothing about that setting could reach it.


Home, the install, the data directory, the logs, every non-guarded `FOLDER`
asset, then drives.

Removable media and manual mounts are told apart: `/media` and `/run/media`
are where an automounter puts a stick somebody just plugged in, `/mnt` is
where somebody mounted a disk on purpose. Calling a permanent second drive
removable invites pulling it out.
