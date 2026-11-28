#!/bin/zsh
# Start the JARVIS web UI:  ./run.sh   (then it opens http://127.0.0.1:8765)
# Options: PORT=9000 ./run.sh      ./run.sh --no-browser
cd "$(dirname "$0")" || exit 1

# Python env that has the `groq` and `python-dotenv` packages installed
PY=/opt/miniconda3/envs/onto/bin/python
[ -x "$PY" ] || PY=python3

# Install missing deps if needed (server itself needs only the stdlib)
$PY -c "import groq, dotenv, plotly" 2>/dev/null || $PY -m pip install -q groq python-dotenv plotly

# Free the port if an old server is still running
PORT=${PORT:-8765}
lsof -ti tcp:$PORT 2>/dev/null | xargs kill 2>/dev/null && sleep 0.5

export PORT
exec $PY server.py "$@"
