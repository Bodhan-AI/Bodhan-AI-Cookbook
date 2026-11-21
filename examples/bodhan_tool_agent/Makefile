.PHONY: setup test lint format demo sample-data self-eval server server-http web docker clean

# venv layout differs between Windows and POSIX
ifeq ($(OS),Windows_NT)
VENV_PY := .venv/Scripts/python.exe
else
VENV_PY := .venv/bin/python
endif
PY ?= $(VENV_PY)
TASKS ?= tasks/demo_tasks.json

# One command from a clean clone: virtualenv + pinned deps + editable install.
setup:
	python -m venv .venv
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -r requirements-dev.txt
	@test -f .env || cp .env.example .env
	@echo "Done. Fill in .env, then: make test"

# Unit and end-to-end tests run against a fake Bodhan backend: no keys, no network, no cost.
test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests scripts

format:
	$(PY) -m ruff format src tests scripts

# Builds demo inputs into sample_data/ using Bodhan Indic-Speak/Translate (needs keys, costs ~INR 4-6 once; cached).
sample-data:
	$(PY) scripts/make_sample_data.py --out sample_data

# One demo task end to end (needs keys and an LLM endpoint).
demo:
	$(PY) -m bodhan_agent --task "Read this paragraph aloud in Bengali with a female voice" \
		--files sample_data/paragraph_en.txt --out outputs/demo_bengali_tts --max-cost-inr 5

# Runs the whole demo task suite and writes outputs/self_eval.json.
self-eval:
	$(PY) scripts/self_eval.py --tasks $(TASKS) --out outputs

# MCP server over stdio (what MCP clients launch).
server:
	$(PY) -m bodhan_mcp

# MCP server over streamable HTTP; requires BODHAN_MCP_TOKEN.
server-http:
	$(PY) -m bodhan_mcp --http --port 8765

# Local web UI: live plan and trace, replay of finished runs.
web:
	$(PY) -m bodhan_agent.web

docker:
	docker build -t bodhan-tool-agent .

clean:
	rm -rf .pytest_cache .ruff_cache bodhan_mcp_out
