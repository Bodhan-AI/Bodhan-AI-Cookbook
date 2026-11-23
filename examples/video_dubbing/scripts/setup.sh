#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
python3 -c 'import sys; assert (3,10) <= sys.version_info < (3,14), "Use Python 3.10–3.13"'
command -v ffmpeg >/dev/null || { echo 'Install ffmpeg and ffprobe first.' >&2; exit 1; }
command -v ffprobe >/dev/null || { echo 'Install ffmpeg and ffprobe first.' >&2; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install 'pip==25.3' 'setuptools==80.9.0'
if [ "$(uname -s)" = Linux ]; then
  .venv/bin/python -m pip install 'torch==2.8.0' --index-url https://download.pytorch.org/whl/cpu
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m bodhan_dub --help
