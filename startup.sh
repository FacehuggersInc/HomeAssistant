#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate

while true; do
    python app.py force
    EXIT_CODE=$?

    # Exit 42 = update was downloaded and installed, just relaunch
    if [ $EXIT_CODE -eq 42 ]; then
        echo "[startup] Relaunching after update..."
        sleep 1
        continue
    fi

    # Any other code = normal close or crash, stop
    break
done