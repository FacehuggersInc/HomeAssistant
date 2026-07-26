#!/usr/bin/env bash
# Thin wrapper. All supervision logic lives in launcher.py so that it is
# shared with Windows -- this file only activates the venv and re-runs the
# launcher when the launcher updates itself (exit 44).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source ".venv/bin/activate"
elif [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "venv/bin/activate"
else
    echo "ERROR: no virtualenv found (.venv/ or venv/)" >&2
    exit 1
fi

PYTHON="$(command -v python3 || command -v python)"
if [ -z "$PYTHON" ]; then
    echo "ERROR: no python interpreter found" >&2
    exit 1
fi

while true; do
    "$PYTHON" launcher.py
    EXIT_CODE=$?
    # 44 = launcher.py replaced itself during an update; re-run so the new
    # code takes effect. Anything else is final.
    [ $EXIT_CODE -eq 44 ] || break
    echo "[startup] launcher updated, re-running..."
done

exit $EXIT_CODE
