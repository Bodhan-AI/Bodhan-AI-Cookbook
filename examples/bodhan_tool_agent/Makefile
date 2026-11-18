.PHONY: setup test lint server inspect clean

setup:
	python -m venv .venv
	.venv/bin/pip install -q -r requirements-dev.txt
	@command -v ffmpeg >/dev/null || echo "WARNING: ffmpeg not found on PATH (brew install ffmpeg / apt-get install ffmpeg)"

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check src tests

server:
	.venv/bin/python -m bodhan_mcp

# Opens the MCP Inspector against the stdio server (needs Node.js).
inspect:
	npx @modelcontextprotocol/inspector .venv/bin/python -m bodhan_mcp

clean:
	rm -rf .cache out outputs/* && touch outputs/.gitkeep
