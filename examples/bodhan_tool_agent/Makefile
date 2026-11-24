.PHONY: setup test check demo inputs eval server

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

test:
	. .venv/bin/activate && pytest -q

check: test

inputs:
	. .venv/bin/activate && python demo/make_inputs.py

eval:
	. .venv/bin/activate && python evals/run_suite.py

server:
	. .venv/bin/activate && python -m bodhan_mcp
