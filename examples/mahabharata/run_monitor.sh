#!/bin/zsh
# Live view of Claude Code background workflows:  ./run_monitor.sh   (opens http://localhost:8501)
cd "$(dirname "$0")" || exit 1
PY=/opt/miniconda3/envs/onto/bin/python
lsof -ti tcp:8501 2>/dev/null | xargs kill 2>/dev/null && sleep 0.5
exec $PY -m streamlit run workflow_monitor.py --server.port 8501 --browser.gatherUsageStats false
