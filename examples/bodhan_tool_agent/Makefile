.PHONY: install test check eval demo clean run-server

install:
	pip install -e .
	pip install pytest pytest-asyncio

test:
	pytest tests/ -v

check: test eval
	@echo "All tests and self-evaluation benchmark suite passed successfully."

eval:
	python -m bodhan_agent.eval ./eval_results

demo:
	python scripts/run_demo_suite.py

run-server:
	python -m bodhan_mcp

clean:
	rm -rf .pytest_cache/ build/ dist/ *.egg-info/ out/ eval_results/
