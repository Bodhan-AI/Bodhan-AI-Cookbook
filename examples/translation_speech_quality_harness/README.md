# Translation and Speech Quality Harness

Finds, locates and explains errors in Indic-Translate translations and Indic-Speak audio, with evidence for every flag.

**Bodhan models:** Indic-Translate, Indic-Speak, Indic-Transcribe
**Languages shown:** Hindi, Tamil, Bengali (any Indic-Speak production language works)
**Format:** CLI

> Work in progress. This README is completed with results, diagrams and limitations before submission.

## What it does

Reads a JSONL dataset of English sources with their translations and audio, runs cheap deterministic checks first and model round trips after, asks an LLM judge only about meaning, and writes a report that says what is wrong and where.

## Setup

Requires Python 3.10+ and `ffmpeg` on `PATH`.

```bash
cd examples/translation_speech_quality_harness
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in the keys
```

## Run

```bash
python -m bodhan_eval \
  --dataset path/to/data.jsonl \
  --langs hi,ta,bn \
  --out ./report \
  [--generate] [--baseline ./previous_report] [--calibration labels.jsonl] [--max-cost-inr 50]
```

Settings are read from environment variables; `--env-file .env` loads them from a file.

Run the tests with `pytest`.
