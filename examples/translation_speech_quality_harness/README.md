# bodhan-eval: quality harness for Indic-Translate and Indic-Speak

Finds and explains quality problems in translations and speech, with evidence, spans and severity.
Cheap deterministic checks run first; model round trips and an LLM judge run only where they add signal;
speech synthesis (the expensive step) is triggered by suspicion or a deterministic sample. Every model
output is cached by exact input, so a rerun is free and byte-identical.

> **Status.** Verified against the real Bodhan APIs (translate, speak, transcribe) and a real judge LLM; 53 unit and
> end-to-end tests pass. Results below are from the demo dataset only; see [Results](#results-on-the-demo-dataset).

## Demo video

[Watch the demo (3 min 26 s)](https://drive.google.com/file/d/19zW4f4eWSzz23LPhnOd3m0tm6HIt15Al/view?usp=sharing): a full run on the demo dataset (served from the cache, so it takes seconds and costs INR 0), then a walk through the report, the dashboard and the baseline diff.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                          # pinned deps in pyproject.toml
cp .env.example .env                             # fill in keys
# system dependency: ffmpeg (only needed to decode non-WAV audio). Or: docker build -t bodhan-eval .
make check                                       # tests
```

## Run

```bash
python -m bodhan_eval \
  --dataset data/demo.jsonl --langs hi,ta,bn --out ./report \
  [--generate] [--baseline ./previous_report] [--calibration labels.jsonl] [--max-cost-inr 50]
```

Extra flags: `--cache-dir`, `--concurrency` (default 8), `--judge-runs` (default 3), `--no-judge`, `--no-speech`, `--no-bt`,
`--speech-sample 0.25`, `--holdout-frac 0.3`, `--bodhan-interval` / `--llm-interval` (min seconds between calls, default 0.1;
use `--llm-interval 6` on a ~10 calls/min free LLM tier), `--ci` / `--ci-tolerance` (exit 2 on regression vs `--baseline`).
`--langs` accepts any code: script checks use Unicode blocks, nothing is per-language code. Speech synthesis was verified with the
default voice for hi, bn, ta, te, mr, gu, kn, ml, pa, or, as, ur, ne (override per language with `BODHAN_SPEAK_VOICE_<LANG>`).

Config: `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_TRANSCRIBE_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`. `BODHAN_API_KEY` is an optional fallback for any model without its own key (the Cookbook convention).
Rate limits (429) are retried with long backoff shared across threads; a run that hits a hard daily quota reports the affected
checks as stage errors in `summary.md` rather than silently scoring without them.

Quick start on the committed text-only demo set (no audio needed):

```bash
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out ./report
```

## Demo data

| File | What | Items |
| --- | --- | --- |
| `data/demo_sources.jsonl` | 37 English source texts written for this project (numbers, units, tables, LaTeX, lists, names) | 37 |
| `data/demo.jsonl` | **Text-only** labelled set: sources x hi/ta/bn, translated by Indic-Translate, errors injected (balanced across 7 translation error types, 64 clean) | 101 |
| `data/demo_injected.jsonl` | Same plus audio for 28 items and 10 speech errors (truncation, loop, silence/noise) | 111 |

Labels: every label in these files has `"label_source": "injector"`. Add your own human labels with a different `label_source`.
Reference translations are present for ~25% of items; the rest are reference-free.
Regenerate (needs keys; translations and audio are cached): `make demo-data`.

**Generated audio is not in git.** `data/demo_injected.jsonl` refers to `audio/*.wav`. Download the audio zip (14.8 MB, 38 WAV files) from
[Google Drive](https://drive.google.com/file/d/1r8WBD1sgb73iZ7lOOKOMNAMqk7Vqjsbj/view?usp=sharing) and unzip it next to `data/`, or rebuild it with `make demo-data`. Without the audio the harness
logs `read_audio` stage errors for those items and continues.

## Output

`summary.md`, `summary.json`, `items.jsonl` (per item: checks run, flags with category / severity / source+translation span /
evidence, suppressed candidates with the reason), `calibration.json` (precision/recall per category; checks-only vs judge-only),
`diff.md` (with `--baseline`), `dashboard.html` (see below), plus `review_queue.csv` (uncertain items for human labelling) and `run.log` (structured JSON events).

### Dashboard

Every run also writes `dashboard.html`: one self-contained file (no server, no network) with target KPIs (met / missed), per-language and
per-category tables, cost, and an item browser filterable by language, category, severity, outcome (caught / missed / wrong category /
false alarm / ok) and held-out vs tuning split, plus free-text search. Clicking an item shows the source and translation with the flagged
spans highlighted, the back-translation, each flag with its evidence and the checks that agreed, suppressed weak signals, and the truth label.
Open it by double-clicking, or deep-link to an item: `dashboard.html#s13-bn`. A committed example is `sample_report/dashboard.html`.

## How it works

The harness is a pipeline of cheap checks first and costly model calls only where they pay off.

### Architecture

```mermaid
flowchart LR
  D[(JSONL dataset)] --> L[loader: validate, skip + count bad rows]
  L --> T{translation?}
  T -- missing + --generate --> GT[Indic-Translate]
  T -- supplied --> DET
  GT --> DET[Deterministic checks<br/>numbers/units, script, Markdown/LaTeX, length prior, chrF vs reference]
  DET --> BT[Back-translation per sentence<br/>Indic-Translate]
  BT --> J[LLM judge x N runs<br/>rubric, verbatim quotes]
  J --> TR{speech triage}
  TR -- audio supplied / suspicious / sampled --> TTS[Indic-Speak] --> ASR[Indic-Transcribe] --> AM[audio metrics + transcript diff]
  TR -- otherwise --> SIG
  AM --> SIG[(signals per item)]
  SIG --> TUNE[tune thresholds on labelled items]
  TUNE --> DEC[decide: hard flags / soft candidates + judge / suppress noise]
  DEC --> R[report + calibration + diff]
  C[(content-addressed cache + cost guard)] -. every model call .- GT & BT & J & TTS & ASR
```

| Check | Kind | Cost | Flags |
| --- | --- | --- | --- |
| numbers & units | deterministic, digits normalized across scripts | free | `number` (hard) |
| script / untranslated | Unicode-block share of letters, Latin runs, identical-to-source; markup masked | free | `wrong_language` |
| Markdown / LaTeX | structural profile diff (fences, tables, headings, math delimiters, LaTeX commands, braces) | free | `format` (hard) |
| length prior | ratio vs the per-language median of the run (no hard-coded ratios) | free | soft `omission`/`addition` |
| chrF++ | vs reference when present (reported, never a flag on its own) | free | - |
| back-translation | sentence-level coverage of source content words; reverse coverage for additions | Indic-Translate | soft `omission`/`addition`/`mistranslation`/`term` |
| LLM judge | rubric, N runs, quote must appear verbatim | LLM | any text category |
| speech | transcript diff (tail loss, contiguous deleted runs, adjacent repeats), silence, clipping, chars/s | Indic-Speak + Indic-Transcribe | `speech_omission`, `repetition`, `garbled` |

### Harness design notes

**Separating system errors from checker errors.** Back-translation and ASR are noisy, so they are never trusted alone:
- Soft candidates (BT coverage, length prior) flag only when extreme, or when the judge independently confirms the same category at an overlapping span. Otherwise they go to `suppressed` with a reason.
- If a reference exists, the reference is back-translated too; a source sentence the BT cannot recover from the *reference* either is `checker_noise`, not a system error.
- Speech omission needs a contiguous deleted run, tail loss, or a large deleted fraction; scattered ASR substitutions are ignored.
- The judge sees the BT labelled "noisy hint only".

**Judge.** The rubric is in `judge.py`. It reads Indic text but may only output a category, severity, English explanation and a *verbatim quote*; quotes not found in the source/translation are dropped (and counted), non-Latin characters are stripped from explanations, so it can never write or correct Indic text. Run 0 is temperature 0, further runs are 0.3 to expose drift; findings are clustered across runs and `frac` (agreement) gates emission. Verdict and category consistency are reported in `summary.md`.

**Calibration.** `--calibration labels.jsonl` (`{"id", "labels"}`) or inline `labels`. Signals are collected once (model calls), then `decide()` is a pure function of (signals, thresholds), so coordinate-descent tuning of ~12 thresholds on micro-F1 (with a false-alarm penalty) costs nothing. Ties keep the generic defaults; with fewer than 8 labelled items nothing is tuned.

**Normalization.** Tokens keep original offsets; digits of every Unicode script map to ASCII (length-preserving), NFC, zero-width removal, danda to full stop. Spans therefore always index the text as supplied.

**Spans.** Flags carry `span_source` and/or `span_translation`. Omission / number / mistranslation spans point into the source (as in the spec example), addition / format / wrong-language into the translation. Calibration counts an overlap with either.

**Determinism and cost.** No timestamps in reports; cache keyed by sha256 of the exact input (text, languages, model, audio hash, temperature, run index). Reports show the *uncached-equivalent* cost, so a cached rerun matches byte for byte. `--max-cost-inr` caps new spend; hitting it skips later paid calls and is reported.

**Robustness.** Bad rows, missing audio, API failures, and budget exhaustion are logged per stage and counted; one item never stops the run. Retries with exponential backoff; concurrency-limited.

### Error injector

`python -m bodhan_eval.injector build|inject`: omission (delete sentence/clause), addition (insert a sentence from another item), number (shift one digit in the same script), mistranslation/term (swap a token with one from another item; swap letters in a Latin entity), format (drop `|`, `$`, fence or heading marker), wrong language (English source or another language's output); audio: truncate, loop a segment, insert silence or noise. Everything is programmatic; clean text kept as `reference` for 25% of items so reference-free is the common path.

## Limitations (known)

- `term` is the weakest category (20% recall on the demo set): injected term errors are misspelled words, and the judge often reports them as `mistranslation` or misses them. `mistranslation` also relies on the judge; there is no deterministic detector for either.
- Number words ("two"), spelled-out dates, and units rendered in the target script are not compared deterministically.
- Back-translation is sentence-level and position-agnostic; heavy paraphrase lowers precision of soft candidates (suppressed unless the judge confirms).
- Repetition is detected from the transcript only (not acoustically); an ASR that "fixes" a loop would hide it.
- Injected `term` errors are heuristic (a misspelling of the word at the source entity's relative position); injected `omission` spans are approximate when sentence counts differ (`span_approx`). Demo labels are all machine-injected (`label_source: injector`); human labels can be added with `python -m bodhan_eval.labels`.
- Noise-injected audio is often missed (no spectral noise check); the held-out set is small (36 items), so its numbers are noisy.

## Results on the demo dataset

Run: `data/demo_injected.jsonl` (111 items: 47 with injected errors, 64 clean; hi/ta/bn), judge `gemini-3.5-flash-lite` (2 runs per item),
Bodhan hosted API. Thresholds were tuned on 75 labelled items; headline numbers are on the **36 held-out items** (17 with errors, 19 clean).
Full report: [`sample_report/`](sample_report/summary.md).

| Target | Goal | Held-out (n=36) | All 111 items | Tuning set (in-sample) |
| --- | --- | --- | --- | --- |
| Detection recall | >= 80% | **94.1%** | 89.4% | 86.7% |
| False alarms on clean items | <= 10% | **0.0%** | 4.7% | 6.7% |
| Span overlaps seeded error | >= 70% | **100%** | 100% | 100% |
| Category accuracy | >= 70% | **100%** | 95.2% | 92.3% |
| Judge consistency (same verdict across 2 runs) | >= 90% | **99.1%** (category 97.3%) | | |
| Determinism (cached rerun) | identical, INR 0 | **identical** (`items.jsonl`, `summary.json`, `calibration.json` byte-equal) | | |

Per category on all 111 items (the held-out set is too small for per-category numbers):

| Category | Recall | Precision | Note |
| --- | --- | --- | --- |
| addition, number, wrong_language, speech_omission | 100% | 100% | deterministic checks + back-translation |
| omission | 100% | 83% | |
| format | 100% | 86% | the one "false alarm" is a real Bodhan error: Indic-Translate dropped a `##` heading |
| mistranslation | 100% | 63% | judge also labels some term errors as mistranslation |
| garbled | 60% | 75% | misses noise-injected audio (no spectral noise check) |
| repetition | 50% | 100% | ASR may not reproduce a loop |
| term | **20%** | 50% | weakest category; see limitations |

**Where targets are missed on this data:** none of the headline targets is missed, but term (20% recall), garbled audio (60%) and repetition (50%) are weak.
The held-out set has only 17 error items, so one miss moves recall by about 6 points; the all-111 column is the steadier estimate.
Without the judge the same data gives 72% recall (mistranslation and term are undetectable by deterministic checks); the judge adds mistranslation, term and some omissions.
`sample_report/diff.md` shows this judge-vs-no-judge comparison (new flags there are the judge's additions, not regressions).

## Cost and runtime

Measured on the 111-item run (uncached equivalent, INR): translation, back-translation and generation 0.14 (305 calls), transcription of 28 audio items 0.48, judge LLM 0 (free tier, 222 calls, ~625 prompt tokens each).
Per 100 items, **without speech checks: about INR 0.13**. **With speech checks on ~25% of items: about INR 2.3** (transcription ~0.43 plus synthesis ~1.75, estimated from the 3,233 characters spoken in this dataset, which dominates).
Reruns from cache cost INR 0. The final judge run took about 8 minutes of wall-clock for 111 items (some calls were already cached; the free LLM tier was paced at one call per 5 s).
The 200-item / 30-minute runtime target was **not measured**: the Bodhan calls are cheap and concurrent, so the judge LLM's speed and rate limits dominate.
