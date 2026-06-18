#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
LOG="$SCRIPT_DIR/startup.log"

log() {
    echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG"
}

log "=== startup.sh launched ==="
log "DISPLAY=$DISPLAY"
log "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
log "XDG_SESSION_TYPE=$XDG_SESSION_TYPE"
log "SCRIPT_DIR=$SCRIPT_DIR"

if [ -f ".venv/bin/activate" ]; then
    source ".venv/bin/activate"
    log "venv activated"
else
    log "ERROR: .venv not found"
    exit 1
fi

PYTHON=$(which python 2>/dev/null || which python3 2>/dev/null)
log "python=$PYTHON"

set +e

while true; do
    log "Launching app..."
    "$PYTHON" app.py force >> "$LOG" 2>&1
    EXIT_CODE=$?
    log "App exited with code $EXIT_CODE"

    if [ $EXIT_CODE -eq 42 ]; then
        log "Relaunching after update..."
        sleep 1
        continue
    fi

    log "Done."
    break
done