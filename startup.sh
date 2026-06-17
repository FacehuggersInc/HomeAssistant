#!/bin/bash
# Launches the app directly (no auto-update).
# Uses 'exec' to replace this bash process with python,
# so there is no bash parent that can die and take the terminal.
cd "$(dirname "$0")"
source .venv/bin/activate
exec python app.py force