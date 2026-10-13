#!/usr/bin/env bash
# ShelterApp Resource Tool — setup for macOS / Linux.
# (Windows users: double-click setup.bat instead.)
set -euo pipefail

echo "============================================================"
echo "  ShelterApp Resource Tool — setup"
echo "============================================================"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "[!] '$PY' not found. Install Python 3.11+ (https://www.python.org/downloads/)."
  exit 1
fi

echo "Installing Python packages (playwright, openpyxl)..."
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt

echo "Downloading the browser Playwright needs (first time only)..."
"$PY" -m playwright install chromium

echo
echo "Setup complete. Next:"
echo "  1) put your Google Places API key in google_places_key.txt (see README.md)"
echo "  2) run:  $PY run.py"
