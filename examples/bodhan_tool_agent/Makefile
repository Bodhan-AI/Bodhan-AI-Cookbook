PYTHON ?= python3

.PHONY: setup test lint check run-mcp demo self-eval clean

setup:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -v

lint:
	$(PYTHON) -m ruff check src/ tests/

check: lint test

run-mcp:
	$(PYTHON) -m bodhan_mcp

self-eval:
	$(PYTHON) -m bodhan_agent.eval_suite --out ./demos/outputs

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .ruff_cache
