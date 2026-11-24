.PHONY: setup test lint check demo-inputs demo eval server docker clean

PY ?= python

# One-command setup from a clean clone (Python 3.12+, ffmpeg on PATH).
setup:
	$(PY) -m pip install -r requirements-dev.txt

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests scripts

# Local gate: tests + lint. (In the Cookbook repo root, `make check` also validates this recipe.)
check: test lint

# Synthetic demo inputs. Add AUDIO=1 to also generate the voice note, clips and video with Bodhan (~₹2).
demo-inputs:
	$(PY) scripts/make_demo_inputs.py $(if $(AUDIO),--audio,)

# Run the demo task suite through the agent and write evals/results.md (needs keys in .env).
eval:
	$(PY) scripts/run_evals.py

demo: demo-inputs eval

server:
	$(PY) -m bodhan_mcp

docker:
	docker build -t bodhan-task-agent .

clean:
	rm -rf out bodhan_mcp_outputs .pytest_cache .ruff_cache
