.PHONY: help setup install test lint typecheck check clean

PYTHON := python
PIP := pip

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: install nltk-data  ## Full setup from clean clone

install:  ## Install all dependencies (pinned)
	$(PIP) install -e ".[dev]"

nltk-data:  ## Download required NLTK data
	$(PYTHON) -c "import nltk; nltk.download('punkt'); nltk.download('punkt_tab')"

test:  ## Run all tests
	pytest tests/ -v --cov=src --cov-report=term-missing

test-unit:  ## Run unit tests only (no e2e)
	pytest tests/ -v -k "not e2e"

test-e2e:  ## Run end-to-end tests (requires .env with real keys)
	pytest tests/test_e2e.py -v -s

lint:  ## Lint with ruff
	ruff check src/ tests/

format:  ## Format with ruff
	ruff format src/ tests/

typecheck:  ## Type-check with mypy
	mypy src/

check: lint typecheck test-unit  ## Run all checks (lint + typecheck + unit tests)

clean:  ## Remove build artifacts and caches
	rm -rf dist/ build/ .pytest_cache/ htmlcov/ .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +

# --- Run Targets ---

run-mcp:  ## Start the MCP server on stdio
	$(PYTHON) -m bodhan_mcp

run-agent:  ## Run agent with example task (edit TASK and FILES as needed)
	$(PYTHON) -m bodhan_agent \
		--task "Transcribe this audio and translate to English" \
		--files examples/inputs/ \
		--out ./out \
		--max-steps 20 \
		--max-cost-inr 10

demo:  ## Run all 6 demo tasks
	$(PYTHON) scripts/run_demo_tasks.py

# --- Docker ---

docker-build:  ## Build Docker image
	docker build -t bodhan-tool-agent:latest .

docker-run-mcp:  ## Run MCP server in Docker
	docker run --rm -it --env-file .env bodhan-tool-agent:latest python -m bodhan_mcp
