.PHONY: setup test test-live e2e lint fmt typecheck check validate docker demo clean

PY ?= python

setup:            ## one-command setup from a clean clone
	$(PY) -m pip install -r requirements-dev.txt

test:             ## unit + offline end-to-end tests (no keys, no network)
	$(PY) -m pytest -q

test-live:        ## small paid smoke test against real APIs (needs .env)
	$(PY) -m pytest -q -m live

e2e:              ## just the 10-item offline end-to-end test
	$(PY) -m pytest -q tests/e2e

lint:
	ruff check src tests

fmt:
	ruff format src tests && ruff check --fix src tests

typecheck:
	mypy

check: lint typecheck test   ## everything CI runs

validate:         ## Bodhan Cookbook recipe validator (needs COOKBOOK=/path/to/Bodhan-AI-Cookbook)
	$(PY) $(COOKBOOK)/scripts/validate_recipe.py .

docker:
	docker build -t bodhan-eval .

demo:             ## full demo run (paid): generate + score + calibrate
	$(PY) -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out outputs/demo_report --calibration data/demo_labels.jsonl --max-cost-inr 50

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache outputs/* && touch outputs/.gitkeep
