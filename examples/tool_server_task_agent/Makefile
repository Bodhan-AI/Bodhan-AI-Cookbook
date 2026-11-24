.PHONY: setup test lint server agent-help demo-inputs demo eval inspector docker-build

PYTHON ?= python

# One-command setup: pinned dependencies + the two importable packages (bodhan_mcp, bodhan_agent).
# System dependencies (not pip): ffmpeg + ffprobe, poppler-utils. See README.
setup:
	$(PYTHON) -m pip install -r requirements-dev.txt
	$(PYTHON) -m pip install -e . --no-deps

test:
	$(PYTHON) -m pytest tests -v

lint:
	$(PYTHON) -m ruff check src tests scripts

# The MCP server on its own (stdio). Point any MCP client at this command.
server:
	$(PYTHON) -m bodhan_mcp

agent-help:
	$(PYTHON) -m bodhan_agent --help

# Tiny demo inputs (media is git-ignored). Needs the four Bodhan keys; costs about Rs 1 once, then cached.
demo-inputs:
	$(PYTHON) scripts/make_demo_inputs.py

# The 8 demo tasks (6 required + injection + PDF), saved to demo_runs/<name>/
demo:
	$(PYTHON) scripts/run_demos.py

# Demo + edge cases with pass/fail expectations and a pass rate
eval:
	$(PYTHON) scripts/eval_suite.py

# Off-the-shelf client check (needs Node): bash scripts/inspector_check.sh > docs/inspector_session.md
inspector:
	PYTHON=$(PYTHON) bash scripts/inspector_check.sh

docker-build:
	docker build -t bodhan-tool-agent .
