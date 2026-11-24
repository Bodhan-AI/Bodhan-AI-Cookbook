.PHONY: setup check test lint clean install

setup:
	pip install -r requirements.txt
	pip install -e .

install:
	pip install -e .

check: lint test

lint:
	ruff check src/ tests/

test:
	pytest tests/ -v --tb=short

test-unit:
	pytest tests/unit/ -v

test-contract:
	pytest tests/contract/ -v

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache dist build *.egg-info

# Run the agent
run-example:
	python -m bodhan_agent \
		--task "Transcribe this Hindi voice note and give me English subtitles" \
		--files sample_data/ \
		--out ./out \
		--max-steps 20 \
		--max-cost-inr 10

# Run MCP server (stdio)
run-mcp:
	python -m bodhan_mcp
