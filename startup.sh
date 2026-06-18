#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv
if [ -f ".venv/bin/activate" ]; then
    source ".venv/bin/activate"
else
    echo "[startup] ERROR: .venv not found in $SCRIPT_DIR"
    exit 1
fi

# Confirm python
PYTHON=$(which python 2>/dev/null || which python3 2>/dev/null)
if [ -z "$PYTHON" ]; then
    echo "[startup] ERROR: python not found"
    exit 1
fi

echo "[startup] Using python: $PYTHON"
echo "[startup] Working dir: $SCRIPT_DIR"

# Unset -e so exit codes from the app don't abort the script
set +e

while true; do
    echo "[startup] Launching application..."
    "$PYTHON" app.py force
    EXIT_CODE=$?
    echo "[startup] Application exited with code $EXIT_CODE"

    if [ $EXIT_CODE -eq 42 ]; then
        echo "[startup] Relaunching after update..."
        sleep 1
        continue
    fi

    echo "[startup] Exiting."
    break
done