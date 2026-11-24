# Convenience targets for this recipe (the Cookbook's own `make check` lives at the repo root).
.PHONY: setup test lint server demo-inputs eval docker

setup:            ## create .venv and install pinned dependencies
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test:             ## unit + tool + MCP stdio/HTTP + end-to-end tests (offline, fake Bodhan + scripted LLM)
	python -m pytest -q

lint:
	ruff check src tests scripts

server:           ## the MCP server on stdio
	python -m bodhan_mcp

demo-inputs:      ## build sample_data/demo/ with Bodhan (about Rs 2, cached after the first run)
	python scripts/make_demo_inputs.py

eval:             ## run every task in tasks/demo_tasks.json and write outputs/eval/summary.md
	python scripts/run_eval.py

docker:
	docker build -t indic-task-agent .
