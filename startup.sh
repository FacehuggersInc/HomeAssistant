#!/bin/bash
# Startup script for Desktop Home Assistant
# Loops so that after an update the app relaunches automatically.

cd "$(dirname "$0")"
source .venv/bin/activate

while true; do
    python app.py force
    EXIT_CODE=$?

    # Exit code 0 = normal close, stop looping
    if [ $EXIT_CODE -eq 0 ]; then
        break
    fi

    # Exit code 42 = update was triggered, loop back and relaunch
    if [ $EXIT_CODE -eq 42 ]; then
        echo "[startup] Update complete, relaunching..."
        sleep 1
        continue
    fi

    # Any other exit = unexpected crash, stop
    break
done