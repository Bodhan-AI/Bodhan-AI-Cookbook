.PHONY: help install check test demo run-mcp run-agent clean

PYTHON ?= python
UV ?= uv

help:
	@echo "Bodhan Tool Server and Task Agent Makefile"
	@echo "=========================================="
	@echo "  make install    - Install package in editable mode with development dependencies"
	@echo "  make check      - Run hygiene audit, import validation, and complete test suite"
	@echo "  make test       - Run all unit and integration tests (pytest)"
	@echo "  make demo       - Execute the automated 6+ task demo suite (including impossible tasks)"
	@echo "  make run-mcp    - Start standalone Bodhan MCP server over stdio"
	@echo "  make run-agent  - Display CLI options for bodhan_agent"
	@echo "  make clean      - Remove build artifacts, cached files, and temporary outputs"

install:
	$(UV) pip install -e ".[dev]"

check:
	@echo "--- 1. Auditing Git hygiene (verifying no secrets or .env committed) ---"
	@git status --short
	@echo "--- 2. Verifying package imports ---"
	$(PYTHON) -c "import bodhan_mcp, bodhan_agent; print('Imports verified successfully.')"
	@echo "--- 3. Running automated pytest suite ---"
	pytest -v tests/

test:
	pytest -v tests/

demo:
	$(PYTHON) scripts/run_demo_suite.py

run-mcp:
	$(PYTHON) -m bodhan_mcp

run-agent:
	$(PYTHON) -m bodhan_agent --help

clean:
	rm -rf build dist *.egg-info .pytest_cache .cache htmlcov .coverage runs test_outputs demo_run
