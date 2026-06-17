#!/bin/bash
# Run this to update the application from GitHub then relaunch
cd "$(dirname "$0")"
source .venv/bin/activate
python app.py