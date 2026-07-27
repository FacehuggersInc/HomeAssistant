# The on-screen keyboard

Any text field opens a keyboard dialog. It shows what you are editing, its
description, the current value, and the keys - a dialog rather than a strip
sliding up from the bottom, because on a short screen a strip covers the very
field it is editing and has no room to say which setting is in play.

* Rows are **staggered** like a physical keyboard - the home row sits half a
  key right of the number row, and the one below it nine tenths. Aligned
  columns look tidy and defeat muscle memory.
* Keys scale to the panel. The grid spans **10.9 keys**, not ten: the
  staggered rows start up to 0.9 of a key in, and sizing for ten alone clipped
  the right-hand column. The result is capped to the screen width so widening
  it cannot push the dialog off the edge. On a panel shorter than 660px key height shrinks toward a 44px
  floor and the description is dropped first, so it still fits an 800x480
  screen rather than running off the bottom.
* Shift is one-shot, the way a phone behaves - capitalise one letter and drop
  back rather than staying locked.
* `?123` switches to a symbols layer. Numeric settings get a numpad with a
  sign toggle instead.
* Tap the preview to place the caret; keys, space and backspace all act
  there rather than at the end.
* Nothing is written to the field until **Done**. Cancel leaves it untouched.
* A `body` setting gets a **multi-line preview** rather than a single line,
  and the keys drop to their touch floor so the text gets the room instead.
  The dialog then measures itself and trims the preview until it fits the
  panel, so it never runs off the bottom.
* Only **one** keyboard can be open at a time. A single tap fires
  `mousePressEvent` on the field and, unaccepted, on its parent, plus
  `focusInEvent` besides - without the guard that is two or three identical
  dialogs stacked on each other.

Numeric settings (`int`, `float`, `numeric`, `list[int]`, `list[float]`) get a
numpad with a sign toggle and no letters. Everything else gets the full
QWERTY board.

`make_keyboard(client, target, setting_type, label=..., description=...)`
builds it. The target may be a `QLineEdit`, `QTextEdit` or `QPlainTextEdit`.

## Fields are displays

Every editable field is **read-only** and opens the keyboard dialog on tap.
There is no physical keyboard on the target hardware, so a field that accepts
direct input only ever shows a caret nothing can type into. All editing goes
through the dialog, and the value is written back on Done.

If you add a field type, bind `mousePressEvent` as well as `focusInEvent` -
focus alone means a second tap on an already-focused field does nothing.

Pass `client` and `setting_type` in explicitly. The setting object carries
neither, so reading them off it raises `AttributeError` - and inside a bare
`except Exception: pass` that surfaces as a keyboard that simply never
opens.
