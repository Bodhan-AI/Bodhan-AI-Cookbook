# Bodhan Translation and Speech Quality Harness

A deterministic, cacheable quality harness for Indic-Translate and Indic-Speak outputs.

## Setup

From the repository root on Windows:

```powershell
$env:PYTHONUTF8="1"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r examples\translation_speech_quality_harness\requirements.txt `
  -r examples\translation_speech_quality_harness\requirements-dev.txt
python -m pip install -e .\examples\translation_speech_quality_harness
```

Copy `.env.example` to `.env` only for real, opt-in API runs. Tests use `test-key` and mocks.
From the repository root, run the recipe contract validator with
`python examples\translation_speech_quality_harness\scripts\validate_recipe.py`.

The candidate-triggered LLM judge requires `LLM_BASE_URL`, `LLM_MODEL`, and
`LLM_API_KEY` in the repository-root `.env`. Use the OpenAI-compatible endpoint
and model name provided by your chosen LLM service. It runs a blind pass followed
by a candidate-confirmation pass; if it is unavailable or not configured, items
requiring judge confirmation are reported as unchecked. Validated judge findings
are cached; unvalidated raw replies are not written to the cache. Use `--no-judge`
to explicitly disable the judge.
The verified development judge is `gemini-3-flash-preview` through the
Google Gemini OpenAI-compatible chat-completions endpoint. Set
`LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/`,
`LLM_MODEL=gemini-3-flash-preview`, and `LLM_API_KEY` locally.

## Run

```powershell
$env:PYTHONUTF8="1"
python -m bodhan_eval --dataset examples/translation_speech_quality_harness/data/demo.jsonl --langs hi --out report --max-cost-inr 50
```

The CLI requires `--dataset`, `--langs`, and `--out`. Optional flags include `--generate`, `--baseline`, `--calibration`, `--cache-dir`, `--concurrency`, `--seed`, `--span-ref`, `--speech-sample`, `--judge-consistency-n`, `--no-speech`, `--no-judge`, and `--fail-on-regression`.

With `--generate`, rows missing a translation are sent to Bodhan Indic-Translate using `BODHAN_TRANSLATE_KEY`; existing translations are preserved. The run retries one truncated response with a larger output cap, then records an explicit failed/unchecked state if it remains truncated or the request fails. Calls are charged against `--max-cost-inr` using the requested output-token cap and cached under the selected cache directory.

### Prepare human labels

Create an 80-row review queue from licensed IN22 clean-pool rows:

```powershell
python examples\translation_speech_quality_harness\scripts\prepare_label_queue.py `
  --dataset examples\translation_speech_quality_harness\data\in22_gen.jsonl `
  --out examples\translation_speech_quality_harness\.cache\label_queue.csv
```

Open the CSV, fill `reviewed_clean`, `type`, `severity`, and text span columns
for reviewed items, then ingest it:

```powershell
python examples\translation_speech_quality_harness\scripts\ingest_labels.py `
  --csv examples\translation_speech_quality_harness\.cache\label_queue.csv `
  --out examples\translation_speech_quality_harness\data\labels_self.jsonl
```

Every ingested row is tagged `annotator:"self"` and clean rows remain negative
examples for calibration. The queue is an aid for human review, not generated
machine labels.

For calibration, run predictions on the same `(id, lang)` items as the labels.
For example, to evaluate the labeled file without model calls:

```powershell
python -m bodhan_eval `
  --dataset examples\translation_speech_quality_harness\data\labels_self.jsonl `
  --langs hi,ta,bn `
  --out examples\translation_speech_quality_harness\.cache\label_calibration `
  --calibration examples\translation_speech_quality_harness\data\labels_self.jsonl `
  --no-speech --no-judge --max-cost-inr 0
```

The CLI rejects calibration labels whose `(id, lang)` pair is absent from the
run dataset; labels for a different dataset cannot be applied to the demo file.

The Bodhan Translate client uses Bodhan's OpenAI-compatible `/v1/chat/completions` transport, with `indic-translate`, the target language, and `max_output_tokens`; it maps the response's `finish_reason` to the truncation signal. Verify provider routes against the current [Bodhan API reference](https://api.bodhan.ai/api-docs/).

### Acquire public evaluation rows

The Windows PowerShell instructions for accepting Hugging Face access terms and importing licensed IN22-Gen or IN22-Conv text are in [data/README.md](data/README.md). Start with `--dry-run`; imports use the reference translations as clean text and make no Bodhan calls. They are not blind or model-output evaluations.

Each run prints one ASCII-only stdout line with item count, coverage, flags, cost, and runtime. With `--baseline`, the diff reports paired flag-rate confidence intervals; `--fail-on-regression` exits with status 2 when the 95% paired-bootstrap interval indicates a flag-rate increase.

Docker:

```powershell
docker build -f examples/translation_speech_quality_harness/Dockerfile -t bodhan-eval .
docker run --rm -v "${PWD}:/work" bodhan-eval --dataset /work/data/demo.jsonl --langs hi --out /work/report
```

Ffmpeg is required for non-WAV inputs and speech conversion. If it is absent, speech checks are skipped with an actionable reason.

## Add a check in 10 minutes

1. Add a check function in `src/bodhan_eval/checks/` using `_template.py` as a starting point.
2. Decorate it with `@register_check(name=..., categories=[...], modality="text"|"speech", cost_tier=0|1|2|3)`.
3. Accept `(item, stats)` and return `list[Flag]`; include evidence and a detector name.
4. Add a focused test, then run Ruff and the complete test suite.

The pipeline discovers registered checks, sorts them by cost tier, and logs each scheduled check in the per-item `decisions` field.
