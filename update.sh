#!/bin/bash
# Run this manually to update and relaunch.
# Uses the same startup loop as startup.sh.
cd "$(dirname "$0")"
source .venv/bin/activate

# Run the inline updater (exits 42), then loop like startup.sh does
python app.py update
EXIT_CODE=$?

if [ $EXIT_CODE -eq 42 ]; then
    echo "[update.sh] Update installed, launching..."
    exec python app.py force
fi