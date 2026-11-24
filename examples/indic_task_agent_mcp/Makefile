.PHONY: setup test lint check demo-inputs eval server docker

PY ?= python3

setup:
	$(PY) -m venv .venv && . .venv/bin/activate && pip install -q -r requirements-dev.txt && pip install -q --no-deps -e .
	@test -f .env || cp .env.example .env
	@echo "Setup done. Put your keys in .env, then: . .venv/bin/activate"

test:
	pytest -q

lint:
	ruff check .

check: lint test
	cd ../.. && python scripts/validate_recipe.py examples/indic_task_agent_mcp

demo-inputs:
	python scripts/make_demo_inputs.py

eval:
	python scripts/self_eval.py

server:
	python -m bodhan_mcp

docker:
	docker build -t indic-task-agent .
