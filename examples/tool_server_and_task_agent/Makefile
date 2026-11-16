.PHONY: check test lint demo sample-data

# Run with the venv active (see README Setup), or prefix with the venv
# python, e.g. /path/to/venv/bin/python -m pytest -q
check: test lint

test:
	python -m pytest -q

lint:
	python -m ruff check src tests scripts

demo:
	python scripts/run_demo_tasks.py

sample-data:
	python scripts/generate_sample_data.py
