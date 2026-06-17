#!/bin/bash
# Triggers an update then relaunch.
cd "$(dirname "$0")"
source .venv/bin/activate
exec python app.py