.PHONY: setup test lint check run-demo run demo sources sample-report clean

PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

# Make the package importable without `pip install -e .` (handy on Python < 3.11 where the
# editable install's version gate blocks it). Harmless when the package is installed.
export PYTHONPATH := src

setup:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -r requirements.txt
	$(BIN)/pip install --no-deps -e .
	@echo "Setup done. Copy .env.example to .env and add keys for real runs."

lint:
	$(BIN)/ruff check src tests scripts

test:
	$(BIN)/pytest -q

# The Cookbook's gate: lint + tests must pass.
check: lint test

# Offline demo on the committed 10-item fixture (no keys, deterministic).
run-demo:
	$(BIN)/python -m bodhan_eval --dataset data/tiny10.jsonl --langs hi,ta,bn \
		--out report --fake --no-judge --no-speech

sources:
	$(BIN)/python scripts/make_sources.py

# Build the full >=100-item demo dataset (needs Bodhan keys in .env).
demo: sources
	$(BIN)/python scripts/build_demo.py

# Real run on the generated demo dataset (needs Bodhan + LLM keys in .env).
run:
	$(BIN)/python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn \
		--out report --generate

# Regenerate the committed sample report from the fixture.
sample-report:
	rm -rf sample_report
	$(BIN)/python -m bodhan_eval --dataset data/tiny10.jsonl --langs hi,ta,bn \
		--out sample_report --fake --no-speech
	rm -rf sample_report/logs

clean:
	rm -rf report .cache .ruff_cache .pytest_cache **/__pycache__
