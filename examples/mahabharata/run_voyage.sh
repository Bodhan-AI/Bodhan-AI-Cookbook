#!/bin/zsh
# Start "MAHABHARATA — The Living Epic":  ./run_voyage.sh   (opens http://127.0.0.1:8765)
#   Voyage UI  : http://127.0.0.1:8765/          Original JARVIS UI : http://127.0.0.1:8765/classic
#   Options    : PORT=9000 ./run_voyage.sh        ./run_voyage.sh --no-browser
cd "$(dirname "$0")" || exit 1

# 1. Python environment that has groq, python-dotenv and plotly
PY=/opt/miniconda3/envs/onto/bin/python
[ -x "$PY" ] || PY=python3
$PY -c "import groq, dotenv, plotly" 2>/dev/null || $PY -m pip install -q groq python-dotenv plotly

# 2. Answer questions from the Mahabharata knowledge graph (llm.py reads this)
export KNOWLEDGE_GRAPH=mahabharata

# 3. Check the voyage data before starting (offline, takes a second)
if ! $PY tools/validate_voyage.py > /tmp/voyage_validate.log 2>&1; then
  echo "Voyage data failed validation — see /tmp/voyage_validate.log"
  exit 1
fi

# 4. Free the port if an old server is still running, then start
PORT=${PORT:-8765}
lsof -ti tcp:$PORT 2>/dev/null | xargs kill 2>/dev/null && sleep 0.5
export PORT
exec $PY server.py "$@"
