.PHONY: help install test check smoke lint run-mcp clean

PYTHON ?= python

help:
	@echo "Bodhan AI MCP Server and Task Agent Makefile"
	@echo "Available targets:"
	@echo "  install    - Install dependencies from requirements.lock and install in editable mode"
	@echo "  test       - Run full pytest test suite"
	@echo "  smoke      - Run MCP stdio smoke test against all registered tools"
	@echo "  check      - Run compile check, pytest, ruff check, and ruff format --check"
	@echo "  lint       - Run ruff check and ruff format --check"
	@echo "  run-mcp    - Launch Bodhan MCP Server over stdio"
	@echo "  clean      - Remove transient cache and build files"

install:
	$(PYTHON) -m pip install -r requirements.lock
	$(PYTHON) -m pip install -e .

test:
	$(PYTHON) -m pytest -v

smoke:
	$(PYTHON) scripts/mcp_smoke.py

check:
	$(PYTHON) -m compileall src tests
	$(PYTHON) -m pytest
	ruff check src tests scripts
	ruff format --check src tests scripts

lint:
	ruff check src tests scripts
	ruff format --check src tests scripts

run-mcp:
	$(PYTHON) -m bodhan_mcp

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist *.egg-info
