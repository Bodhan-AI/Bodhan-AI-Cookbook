.PHONY: setup test smoke check
setup:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && pip install -e . --no-deps
test:
	python -m pytest -q
smoke:
	python scripts/live_smoke.py
check: test
