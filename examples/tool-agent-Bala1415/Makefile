.PHONY: setup test lint check clean agent server

# One-command setup from a clean clone
setup:
	python -m pip install -e ".[dev]"
	@echo "✅ Setup complete. Copy .env.example to .env and fill in your keys."

# Run all tests
test:
	python -m pytest tests/ -v --tb=short

# Run unit tests only (no API calls)
test-unit:
	python -m pytest tests/ -v --tb=short -m unit

# Run end-to-end tests (requires API keys)
test-e2e:
	python -m pytest tests/ -v --tb=short -m e2e

# Lint and format check
lint:
	python -m ruff check src/ tests/
	python -m ruff format --check src/ tests/

# Format code
format:
	python -m ruff format src/ tests/
	python -m ruff check --fix src/ tests/

# Full check (lint + test) — run before PR
check: lint test

# Run the task agent
agent:
	python -m bodhan_agent $(ARGS)

# Run the MCP server (stdio)
server:
	python -m bodhan_mcp

# Clean build artifacts
clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
