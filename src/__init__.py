"""
Desktop Home Assistant.

This package root is intentionally empty.

It used to be three things at once: a prelude of ~40 re-exported stdlib and
third-party names for `from src import *` to pick up, a home for app
constants, and a public facade whose last two lines were
`from src.styling import *` and `from src.main import Client`.

That last job is what made import order load-bearing. `src.styling` did
`from src import *` while this file imported `src.styling` at the bottom, so
the whole thing only worked because the stdlib names happened to already be
bound into the partially-initialized module by the time execution reached
line 324. Nothing enforced that but the line numbers.

Where things went:
  - constants           -> src/constants.py
  - the spaCy model     -> src/assistant/nlp.py  (now lazy)
  - the Windows guards  -> deleted; pyautogui/pygetwindow/winsdk/pynput
                           had no consumers anywhere in the tree
  - QT_STYLE_OVERRIDE   -> src/main.py, at module scope

Import Client explicitly:  from src.main import Client
"""
