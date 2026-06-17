#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate

# On first run, launch normally
python app.py force
EXIT_CODE=$?

while true; do
    if [ $EXIT_CODE -eq 42 ]; then
        echo "[startup] Downloading and installing update..."
        python app.py update
        UPDATE_CODE=$?
        if [ $UPDATE_CODE -eq 42 ]; then
            echo "[startup] Update installed, relaunching..."
            python app.py force
            EXIT_CODE=$?
        else
            echo "[startup] Update failed (code $UPDATE_CODE), relaunching existing version..."
            python app.py force
            EXIT_CODE=$?
        fi
    else
        # Normal exit (0) or crash — stop looping
        break
    fi
done