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
From the repository root, run the Cookbook recipe validator with
`python scripts\validate_recipe.py examples\translation_speech_quality_harness`.
A recipe-local copy of the same contract checks is in `scripts\validate_recipe.py`
(`python examples\translation_speech_quality_harness\scripts\validate_recipe.py examples\translation_speech_quality_harness`).

The candidate-triggered LLM judge requires `LLM_BASE_URL`, `LLM_MODEL`, and
`LLM_API_KEY` in the repository-root `.env`. Use the OpenAI-compatible endpoint
and model name provided by your chosen LLM service. It runs a blind pass followed
by a candidate-confirmation pass; if it is unavailable or not configured, items
requiring judge confirmation are reported as unchecked. Validated judge findings
are cached; unvalidated raw replies are not written to the cache. Use `--no-judge`
to explicitly disable the judge.
Any OpenAI-compatible chat-completions provider works. During development the judge was configured
with `gemini-3-flash-preview` through Google's OpenAI-compatible endpoint; set `LLM_BASE_URL`,
`LLM_MODEL` and `LLM_API_KEY` for your own provider.

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

Calibration labels may be the dataset itself or a labels-only file (`id`, `lang`, `labels`). Labels whose
`(id, lang)` pair is absent from the run are ignored with a warning recorded in `calibration.json`.
`--span-ref auto|translation|source` chooses which span a label is matched against (default `auto`:
whichever of the flag's translation and source spans overlaps the label best).

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

## Design, status and known limitations

`sample_report/` holds a real offline report (80 self-labelled IN22 rows, `--no-judge --no-speech`, zero cost).

See [DESIGN.md](DESIGN.md) for the architecture diagram, the dataset-to-report flow chart, one line per
check, the judge rubric, calibration and how results combine.

Honest status: the deterministic checks (numbers, script/language, Markdown/LaTeX skeleton, per-language
length outliers), the budgeted cache, the deterministic reports and the baseline diff are implemented
and tested. Not yet implemented or not yet measured:

- No Indic-Translate back-translation check; semantic recall (mistranslation, term, omission) is low.
- No WER/CER speech omission check and no clipping check.
- No injector-based recall/false-alarm results; the held-out numbers come from 80 self-annotated IN22
  reference rows and show recall 0 without the judge.
- Calls are sequential (no concurrency).
- Localization is matched at IoU >= 0.3, not the brief's 70% overlap target.
