#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || echo "WARNING: install ffmpeg before a live run"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
[ -f .env ] || cp .env.example .env
python -m pytest -q
echo 'Offline setup ready. Configure your .env locally before a paid live run.'
