.PHONY: setup test lint server demo-inputs demos eval docker

PY ?= python

setup:            ## one-command setup (Python 3.11+, ffmpeg on PATH)
	$(PY) -m pip install -r requirements.txt

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests evals scripts

server:           ## MCP server over stdio
	$(PY) -m bodhan_mcp

demo-inputs:      ## generate synthetic demo inputs into sample_data/ (uses Bodhan translate + speak)
	$(PY) scripts/make_demo_inputs.py --out sample_data

demos:            ## run the demo task suite into demo_runs/
	$(PY) evals/run_evals.py --suite evals/demo_tasks.json --inputs sample_data --out demo_runs

eval:             ## self-evaluation: run the suite and report a pass rate
	$(PY) evals/run_evals.py --suite evals/demo_tasks.json --inputs sample_data --out runs/eval

docker:
	docker build -t bodhan-tool-agent .
