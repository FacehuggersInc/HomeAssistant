#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f ".venv/bin/activate" ]; then
    source ".venv/bin/activate"
fi

set +e

PYTHON=$(which python 2>/dev/null || which python3 2>/dev/null)

echo "[update] Running update..."
"$PYTHON" app.py update
EXIT_CODE=$?

if [ $EXIT_CODE -eq 42 ]; then
    echo "[update] Update installed, launching..."
    exec "$PYTHON" app.py force
else
    echo "[update] Update failed with code $EXIT_CODE"
    exit $EXIT_CODE
fi