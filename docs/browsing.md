# Browsing

A file picker that opens behind the panel is a file picker nobody can use.
`QFileDialog` is a native window, and on a single-monitor panel running
fullscreen it opens underneath - so the Browse button appeared to do nothing.

The panel has its own, and it is `ItemGridDialog` with browsing switched on.

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
that holds it costs **0.09ms**. So a folder of eighty photos spent a second
decoding before it drew anything, and every sort change spent it again.

The tiles are built with their placeholder and the decoding is handed to a
thread pool. A `QPixmap` can only be made on the UI thread, so the worker
returns a `QImage` - already scaled, since scaling is not the cheap half.

Every rebuild bumps a generation counter and clears the queue. A picture
decoded for the folder somebody has since left is dropped on arrival rather
than drawn into the folder they walked into. Whatever was already running
finishes and is thrown away, because waiting for it is the pause this exists
to remove.

Measured, 120 items of which 80 are photos:

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

**A long name is shortened, not accommodated.** A `QPushButton` asks for
whatever width its text needs and never gives it back, so one long asset name
makes the column wider than the rail and the rail scrolls sideways - and
widening it does not help, because the next name is longer. Labels are elided
to `RAIL_WIDTH - RAIL_CHROME`, with the whole name in the tooltip.

`RAIL_CHROME` counts three things between the text and the rail's edge, not
one: the button's padding and icon, the column's margins, and the scrollbar.

The rail is wider than its buttons need, because the bar is drawn inside the
viewport: it takes its width from the column rather than from the panel, so a
rail sized for the buttons alone narrows them the moment there is enough in it
to need a bar.

**Rows carry no margins of their own.** A `QHBoxLayout` defaults to 9px all
round, so each row put 18px between itself and the next - four times the
dialog's own spacing, and nothing about that setting could reach it.


Home, the install, the data directory, the logs, every non-guarded `FOLDER`
asset, then drives.

Removable media and manual mounts are told apart: `/media` and `/run/media`
are where an automounter puts a stick somebody just plugged in, `/mnt` is
where somebody mounted a disk on purpose. Calling a permanent second drive
removable invites pulling it out.
