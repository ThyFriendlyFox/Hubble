#!/bin/bash
# Launches the Observatory dev server with every telescope enabled.
cd "$(dirname "$0")/.."
source .venv/bin/activate
export OBSERVATORY_ENABLED=all
exec python app.py
