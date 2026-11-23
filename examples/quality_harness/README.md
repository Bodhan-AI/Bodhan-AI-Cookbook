# Translation & Speech Quality Harness (`bodhan_eval`)

An agentic evaluation harness that finds and explains quality problems in **Indic-Translate**
translations and **Indic-Speak** audio, and reports what changed between two runs. It reads a JSONL
dataset of English sources, scores supplied outputs (or generates missing ones with Bodhan
models), flags specific errors **with evidence and spans**, calibrates against human labels, and
writes an actionable report. It runs unattended on data it has never seen.

> **Status:** the 108-row demo was generated with hosted Indic-Translate and contains 54
> injector-labelled errors. One live Hindi speech synthesis and transcription run succeeded; human
> review and broader speech and judge evaluation are still needed. The fixture report is not
> full-dataset performance.
>
> **Design in one line:** deterministic checks and back-translation, the LLM judge, then speech;
> every check only *proposes* `Evidence`, and a single combiner separates real system errors from
> noisy-checker mistakes before anything is flagged.

- Architecture diagram: [docs/architecture.md](docs/architecture.md)
- Flow chart (dataset → report): [docs/flow.md](docs/flow.md)
- Design notes (each check, the rubric, calibration, combiner): [docs/design_notes.md](docs/design_notes.md)
- Human review and calibration: [docs/human_review.md](docs/human_review.md)
- Sample report: [sample_report/summary.md](sample_report/summary.md) · interactive [sample_report/dashboard.html](sample_report/dashboard.html) (open in a browser)
- Demo translation-only report: [sample_report_demo/summary.md](sample_report_demo/summary.md)
  (no judge or speech; the injector labels are not human annotations)
- Judge benchmark: [sample_report_judge/summary.md](sample_report_judge/summary.md), on
  [48 selected demo items](data/judge_benchmark.jsonl); see [evaluation evidence](docs/evaluation_evidence.md).
- Full judge-enabled translation report: [sample_report_judge_full/summary.md](sample_report_judge_full/summary.md)
  (108 demo items; no speech or human-reviewed clean labels).
- Combined demo report: [sample_report_combined/summary.md](sample_report_combined/summary.md)
  (108 translation items, cached judge verdicts, 70 selected speech items).
- Updated cached re-evaluation: [sample_report_combined_updated/summary.md](sample_report_combined_updated/summary.md).
- Dashboard design: [docs/dashboard.md](docs/dashboard.md)
- Demo video: [Watch the demonstration](https://drive.google.com/file/d/1dj-dtYGGQCWNwLau13VujNblcoXU0mbt/view?usp=sharing)

## Setup

Quick start (no keys, offline):
```bash
make setup                 # venv + install (Python 3.11+)
make check                 # ruff + pytest (offline, zero cost)
make run-demo              # runs on data/tiny10.jsonl with deterministic fake clients
```
`make run-demo` writes `report/` with `summary.md`, `summary.json`, `items.jsonl`,
`calibration.json`. It uses offline **fake** clients so you can see the whole pipeline without
Bodhan keys or network.

On Windows PowerShell, where the Makefile's Unix virtual-environment paths do not apply:
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m bodhan_eval --dataset data/tiny10.jsonl --langs hi,ta,bn --out report --fake --no-judge --no-speech
```
The pinned cross-platform lock was tested in a fresh Python 3.11 Windows environment; a clean
Linux-container and Cookbook-root check are still needed.

## Run

Real run (the exact command the graders use):
```bash
cp .env.example .env       # then fill in the keys below
python -m bodhan_eval \
  --dataset path/to/data.jsonl \
  --langs hi,ta,bn \
  --out ./report \
  [--generate] [--baseline ./previous_report] [--calibration labels.jsonl] [--max-cost-inr 50]
```
Environment (`.env`): `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_TRANSCRIBE_KEY`,
`BODHAN_BASE_URL` (default `https://api.bodhan.ai/v1`), and `LLM_BASE_URL`, `LLM_MODEL`,
`LLM_API_KEY` for the judge.

Extra flags (all optional): `--concurrency --cache-dir --speech-sample-rate --judge-repeats N
--no-judge --no-speech --fail-on-regression --regression-threshold --seed --log-level --fake
--thresholds --judge-input-inr-per-million --judge-output-inr-per-million`.

System dependency: **ffmpeg** (and libsndfile) for audio chunking/silence detection. A
[Dockerfile](Dockerfile) installs them; `docker build -t bodhan-eval . && docker run bodhan-eval`
runs the offline demo.

## What it checks
| Area | Check | Strength | Category |
| --- | --- | --- | --- |
| Translation | script / language, untranslated | strong | `wrong_language_script` |
| Translation | number & unit preservation | strong | `number_unit` |
| Translation | Markdown / table / LaTeX integrity | strong | `format` |
| Translation | length ratio (z-scored) | weak | `omission`/`addition` |
| Translation | chrF++ vs reference (if present) | medium | `mistranslation` |
| Translation | term / entity preservation & consistency | medium | `term_entity` |
| Translation | sentence coverage | weak/med | `omission`/`addition` |
| Translation | back-translation round-trip | noisy | `mistranslation` |
| Translation | LLM judge (blind, rubric, verbatim quotes) | noisy | any |
| Speech | duration / silence / clipping (free) | weak/med | `speech_*` |
| Speech | ASR CER/WER, repetition, omission (paid) | noisy | `speech_*` |

Full taxonomy: `omission, addition, mistranslation, number_unit, term_entity, format,
wrong_language_script, speech_omission, speech_repetition, speech_garbled`. Severity:
`minor|major|critical` (MQM weights 1/5/10).

## Outputs (`report/`)
- `summary.md` — headline numbers, detection vs targets, per-language and per-category tables, top failures with evidence, honest limitations.
- `summary.json` — machine metrics (counts, flags, cost, judge consistency, skipped reasons, disagreements).
- `items.jsonl` — one line per item: checks run, flags with category/severity/spans/evidence, status.
- `calibration.json` — precision/recall/F1/localization/category-accuracy per category & language, plus threshold-fit notes.
- `diff.md` — only with `--baseline`: regressions first, then improvements, with deltas.
- `logs/run.jsonl` — structured per-check/per-item trace. `review_queue.csv` — uncertain items. `meta.json` — timestamps, actual spend, rubric hash.
- `dashboard.html` — a single self-contained, offline **static dashboard** ("the proofreader's desk"): KPI gauges, a language×category recall heatmap, filters, and a proof-sheet drawer that highlights the flagged spans in the source and translation. See [docs/dashboard.md](docs/dashboard.md). Disable with `--no-dashboard`; rebuild with `python -m bodhan_eval.dashboard --report ./report`.

## Building the demo dataset
Indic translations must come from Bodhan, and neither the LLM nor the injector may *write* Indic
text, so the demo dataset is **generated**, not hand-written:
```bash
make sources               # writes data/sources_en.jsonl (108 English sources, hi/ta/bn balanced)
make demo                  # Indic-Translate generates translations, then injects labelled errors
                           #   -> data/demo.jsonl (~50% unperturbed, with injector labels)
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out report --generate
```
See [DATA_SOURCES.md](DATA_SOURCES.md) for licensing. `data/tiny10.jsonl` is a committed offline
fixture (candidate-authored) used by tests and `make run-demo`.

## Quality targets
Targets (recall ≥80%, false alarms ≤10%, localization ≥70%, category accuracy ≥70%, judge
consistency ≥90%, deterministic cached reruns, 200 items/3 langs with speech < 30 min) are
reported in `summary.md`; where a run misses one, the reason is stated. Determinism is verified in
the test suite (`test_e2e_determinism_from_cache`).

## Bodhan API assumptions (important)
The [API reference](https://console.bodhan.ai/api-docs/) documents a translation product route and
an OpenAI-compatible chat route. In this environment the product route returned 404, while a single
Hindi translation succeeded using `POST /v1/chat/completions` and `indic-translate`. One Hindi speech
synthesis and transcription run also succeeded; broader live testing remains necessary.

The configuration contains model names and rates, but the client also has fixed API method and
request assumptions. Verify these against the current provider documentation; code changes may
be necessary. `config/languages.yaml` lists 13 language configurations, which still require
verification against the provider's supported languages.

### What was actually exercised during development
- Hosted Indic-Translate generated the current demo through the chat endpoint. One Hindi speech and
  transcription run succeeded; it is not a measurement of multilingual speech quality.
- A translation-only run evaluated 108 demo items and reported 0.83 detection recall on 54
  injector-labelled items. False-alarm rate is unavailable without reviewed clean labels. The
  uncached run took about 398 seconds and recorded ₹0.03804 for back-translation; cached report
  regeneration recorded no model spend. This run used neither judge nor speech checks.
- The existing sample report attributes judge results to a previous run on `tiny10`. Its provenance
  and reproducibility have not been independently verified, and it does not measure the full demo.

## Limitations
- Without the judge (`--no-judge`) or audio, purely semantic errors with no number/format/script
  signal rely on the round-trip and are often routed to *uncertain* rather than flagged — trading
  recall for precision by design.
- Spelled-out (word) numbers are handled only conservatively.
- Speech signal checks need ffmpeg/libsndfile; without them those signals are disabled gracefully.
- The offline `--fake` translations are placeholders, so a `--fake` run exercises plumbing and the
  deterministic checks, not real translation quality.
- Generate reproducible audio faults from supplied audio with `python -m bodhan_eval.speech_inject
  --dataset clean_with_audio.jsonl --out speech_cases.jsonl --audio-dir speech_cases_audio
  --kind repetition` (also supports `omission` and `garbled`). Generated audio must stay out of git.
- Explicit `labels: []` identifies a reviewed clean item; missing `labels` means unlabelled and is
  excluded from calibration. Labels may be provided as JSONL objects with `id` and `labels`.
  Declare `span_space` as `source` or `target` (default `source`).
- Supplied audio is selected for speech checking; generated speech is sampled. Failures and budget
  limits can still prevent a check. Supply both `--judge-input-inr-per-million` and
  `--judge-output-inr-per-million` (from your provider's pricing and exchange rate) to include
  estimated judge charges in the shared cap. Otherwise judge charges are unmetered. Provider
  billing, retries, exchange rates and token estimates can differ from the ledger.
- Longer audio is split into at most 28-second transcription requests; long text is split for
  synthesis and the WAV chunks are joined. Audio splitting currently uses fixed boundaries, which
  can cut through words and reduce recognition accuracy at a boundary.
- If transcription degenerates into a character loop, the harness retries two segments. On three
  development audio faults this recovered usable text but raised only one final flag; broader,
  independent speech evaluation and human-reviewed clean examples are still needed.

## AI assistance
Built with Claude Code for scaffolding, tests and refactoring. Every design decision and line is
the candidate's and can be explained in the debrief.
