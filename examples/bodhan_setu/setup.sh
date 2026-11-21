#!/usr/bin/env bash
set -euo pipefail

RECIPE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-python3}"

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Bodhan Setu requires Python 3.10 or newer")
PY

"$PYTHON_BIN" -m venv "$RECIPE_DIR/.venv"
"$RECIPE_DIR/.venv/bin/python" -m pip install --requirement "$RECIPE_DIR/requirements.txt"
"$RECIPE_DIR/.venv/bin/python" -m pip install --editable "$RECIPE_DIR[test]"

if [[ ! -f "$RECIPE_DIR/.env" ]]; then
  cp "$RECIPE_DIR/.env.example" "$RECIPE_DIR/.env"
  echo "Created .env from .env.example; add your Bodhan and LLM credentials before running tasks."
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "FFmpeg and ffprobe are required for audio/video conversion. Install FFmpeg and rerun if you need those tools."
fi

echo "Setup complete. Activate with: source \"$RECIPE_DIR/.venv/bin/activate\""
