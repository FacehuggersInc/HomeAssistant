#!/bin/bash
# Direct launcher - skips the auto-updater
cd "$(dirname "$0")"
source .venv/bin/activate
python app.py force