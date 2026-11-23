.PHONY: help setup test check demo mcp mcp-sse ui docker-build docker-test clean

PYTHON ?= python
IMAGE  ?= bodhan-tool-agent:latest

help:
	@echo "Bodhan Tool Server & Task Agent — Available targets:"
	@echo "  make setup        Install pinned dependencies and editable package"
	@echo "  make test         Run all 58 unit & regression tests (offline mock mode)"
	@echo "  make check        Run unit tests + Cookbook recipe validator"
	@echo "  make demo         Run the 6-task automated self-evaluation suite"
	@echo "  make mcp          Start standalone MCP server over stdio"
	@echo "  make mcp-sse      Start MCP server over Streamable HTTP/SSE (port 8080)"
	@echo "  make ui           Launch live trace & artifact preview Web UI (port 8081)"
	@echo "  make docker-build Build Docker image with ffmpeg + pinned deps"
	@echo "  make docker-test  Run unit tests inside the Docker image"
	@echo "  make clean        Remove temporary caches and build artifacts"

setup:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m pip install --no-deps -e .

test:
	BODHAN_MOCK=1 $(PYTHON) -m unittest discover -s tests -p "test_*.py" -v

check: test
	$(PYTHON) ../../scripts/validate_recipe.py .

demo:
	$(PYTHON) run_demo_tasks.py

mcp:
	$(PYTHON) -m bodhan_mcp

mcp-sse:
	$(PYTHON) -m bodhan_mcp --transport sse --port 8080

ui:
	$(PYTHON) -m bodhan_agent.ui --out ./out --port 8081

docker-build:
	docker build -t $(IMAGE) .

docker-test:
	docker run --rm -e BODHAN_MOCK=1 $(IMAGE) python -m unittest discover -s tests -p "test_*.py" -v

clean:
	rm -rf .bodhan_cache build dist *.egg-info src/*.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} +
