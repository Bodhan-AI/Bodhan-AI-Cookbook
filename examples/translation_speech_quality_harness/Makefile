.PHONY: setup test check demo-data demo

setup:
	python -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	python -m pytest -q

check: test

demo-data:  # translate sources with Indic-Translate, make some audio, inject labelled errors (needs keys)
	python -m bodhan_eval.injector build --sources data/demo_sources.jsonl --langs hi,ta,bn --out data/demo_injected.jsonl --audio-fraction 0.25 --speech-share 0.9

demo:
	python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out ./report

demo-speech:
	python -m bodhan_eval --dataset data/demo_injected.jsonl --langs hi,ta,bn --out ./report_speech
