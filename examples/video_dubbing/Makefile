.PHONY: setup lint test check web frontend
.PHONY: setup check test lint web
setup:
	pip install -r requirements-dev.txt && pip install -e .
lint:
	ruff check src tests
test:
	python -m pytest tests -q
check: lint test
web:
	python -m bodhan_dub.web --port 8000

frontend:   ## refresh the Vercel static copy from the served UI
	cp src/bodhan_dub/web/index.html frontend/index.html
