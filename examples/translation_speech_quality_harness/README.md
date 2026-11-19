# Translation and Speech Quality Harness

Finds, locates and explains errors in Indic-Translate translations and Indic-Speak audio, with evidence for every flag.

**Bodhan models:** Indic-Translate, Indic-Speak, Indic-Transcribe
**Languages shown:** Hindi, Tamil, Bengali (any Indic-Speak production language works; tested on Telugu too)
**Format:** CLI

## What it does

The harness reads a JSONL dataset of English sources with their translations and audio. It runs cheap deterministic checks first, then Bodhan round trips, and asks an LLM judge only about meaning. The report says what is wrong, where, and which checks agree, so a model team can act on it without re-reading the data.

From `report_demo/summary.md`:

> - **wmt-125 / bn**: wrong_language, critical, confidence 0.99, found by judge, regen_diff, script
>   - source: "state prisons"

Links:
- Sample report: [`report_demo/summary.md`](report_demo/summary.md), with every file in [`report_demo/`](report_demo/) and a filterable [`dashboard.html`](report_demo/dashboard.html)
- Baseline diff: [`report_demo_v2/diff.md`](report_demo_v2/diff.md)
- Design notes, [architecture diagram](DESIGN.md#architecture) and [flow chart](DESIGN.md#flow-from-dataset-to-report): [`DESIGN.md`](DESIGN.md)
- Demo data and attribution: [`data/README.md`](data/README.md)
- Demo video: DEMO_VIDEO_LINK

## Setup

Requires Python 3.10+ and `ffmpeg` on `PATH`.

```bash
git clone https://github.com/Bodhan-AI/Bodhan-AI-Cookbook.git
cd Bodhan-AI-Cookbook/examples/translation_speech_quality_harness
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in the keys
```

Or with Docker, which brings its own ffmpeg:

```bash
docker build -t bodhan-eval .
docker run --rm --env-file .env -v "$PWD/report:/app/report" bodhan-eval \
  --dataset data/demo.jsonl --langs hi,ta,bn --out report
```

Settings come from environment variables: `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_TRANSCRIBE_KEY`, and `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` for any OpenAI-compatible endpoint. `--env-file .env` loads them from a file; variables already set win.

## Run

```bash
python -m bodhan_eval \
  --dataset path/to/data.jsonl \
  --langs hi,ta,bn \
  --out ./report \
  [--generate] [--baseline ./previous_report] [--calibration labels.jsonl] [--max-cost-inr 50]
```

The demo, and the baseline diff against it:

```bash
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out report_demo --env-file .env
python -m bodhan_eval --dataset data/demo_v2.jsonl --langs hi,ta,bn --out report_demo_v2 \
  --baseline report_demo --fail-on-regression 5 --env-file .env
```

Extra flags:

| Flag | What it does |
| --- | --- |
| `--fail-on-regression N` | With `--baseline`: exit 1 when more than N items gain a major or critical flag (CI mode) |
| `--skip CHECKS` | Switch off checks for comparison runs: `back_translation`, `regen_diff`, `audio_stats`, `transcript`, `judge` |
| `--cache-dir`, `--concurrency` | Where model outputs are cached (default `.bodhan_eval_cache/`); parallel calls per model (default 4) |
| `--calibration` | Human labels as JSONL, or a filled-in `review_queue.csv` from an earlier report |

The error injector plants labelled errors into clean items:

```bash
python -m bodhan_eval.inject --dataset clean.jsonl --out injected.jsonl --rate 0.5 --seed 7 --env-file .env
```

`tools/holdout.py` calibrates on half the items and scores the other half. `tools/cost_runtime.py report_demo` prints cost and runtime per 100 items.

Run the tests with `pytest` (229 tests, offline, about 12 seconds).

Report files: `summary.md`, `summary.json`, `items.jsonl`, `calibration.json`, `diff.md` (with `--baseline`), plus `review_queue.csv`, `dashboard.html` and `run_log.jsonl`. Every flag in `items.jsonl` lists its category, severity, spans on both sides, quotes, and the evidence from each check. `run_log.jsonl` records every model call with its cache key, so any flag can be traced back to the calls behind it.

## How it works

There are three tiers. Each check emits candidates with a strength: exact, noisy or weak. One combiner decides what becomes a flag. A single noisy signal never flags alone.

| Tier | Check | Catches | Strength |
| --- | --- | --- | --- |
| 1, code | Numbers and units | Changed digits, units, currency; native digits, lakh and crore handled | Exact when changed, noisy when missing |
| 1, code | Format | Markdown headings, lists, tables, LaTeX, code, URLs, HTML | Exact |
| 1, code | Script | Wrong script, foreign words, English left untranslated | Exact |
| 1, code | Length, reference | Length outliers within the language; chrF++ and BLEU when a reference exists | Weak, noisy |
| 2, Bodhan | Back-translation | Source sentences missing from the round trip, extra sentences | Noisy; re-checked sentence by sentence |
| 2, Bodhan | Regeneration diff | Words that differ from a fresh greedy translation | Exact if this run reproduces most items, else noisy |
| 2, code | Audio statistics | Silence, clipping, long pauses, noise, speaking rate, abrupt end | Exact to weak |
| 2, Bodhan | Back-transcription | Missing tail, skipped words, repeated or looped phrases, garbled stretches | By shape of the mismatch, never by error rate |
| 3, LLM | Judge | Mistranslation, omission, addition, term, with quotes | Two passes; counts only if both agree and every quote is found in the text |

The LLM never writes Indian-language text. It quotes, code finds the quote, and Indian-script characters are stripped from its explanations. Thresholds come from statistics of the current run and from the labels you pass in, never from constants tuned on the demo. Details are in [`DESIGN.md`](DESIGN.md).

## Results on the demo set

The demo set has 120 rows: 40 sources in Hindi, Tamil and Bengali, with 60 injected errors (2 per category per language) and 60 clean rows. Judge: `openai/gpt-oss-120b`. The holdout calibrates on half A (42 rows) and scores half B (78 rows), split by a hash of the id.

| Run | Recall (≥ 80%) | False alarms (≤ 10%) | Localization (≥ 70%) | Category acc. (≥ 70%) |
| --- | --- | --- | --- | --- |
| Full demo, all 120 rows | **92%** | 15% | **90%** | **88%** |
| Half B, all checks | **93%** | 19% | **93%** | **88%** |
| Half B, judge off | 69% | **3%** | 70% | **90%** |
| Half B, regeneration diff off\* | **83%** | 19% | **84%** | **97%** |

\* One item's judge call was cut off by the provider's daily quota.

- Judge consistency is 94%, against a 90% target: re-judging gave the same verdict.
- Determinism: a rerun from cache gives byte-identical report files at no cost.
- Per language on the full demo:
  - Recall: Hindi 90%, Tamil 95%, Bengali 90%.
  - False alarms: Hindi 16%, Tamil 20%, Bengali 10%.
- Per category, from `report_demo/summary.md`:
  - 100% on format, omission, speech omission and wrong language.
  - 83% on garbled, mistranslation and number.
  - 67% on addition.
  - 50% on term.
  - 17% on speech repetition.

**The false-alarm target is missed.** Every false alarm comes from a flag the judge raised alone, on a row labelled clean only because no error was injected into it. Several of these are real model errors: "vegan" became शाकाहारी (vegetarian), and "Swimming Pool" became পুকুর (pond). `summary.md` quotes every such flag so a reader can decide. A judge-only finding still flags, because the regeneration diff cannot see an error the model makes every time.

**Speech repetition is weak.** The transcript is the only independent speech signal, and one noisy signal goes to the review queue, not to a flag.

## Cost and runtime

Measured from the cache with `tools/cost_runtime.py`, at hosted prices:

| | Per 100 items |
| --- | --- |
| With speech checks | ₹1.05 |
| Without speech checks | ₹0.22 |
| Generating audio too (`--generate`) | ₹9 to ₹20, depending on text length (measured: ₹4.17 for 45 short items, ₹1.00 for 5 long passages) |

Runtime: 5 Telugu items with `--generate`, full checks and the judge took 3 min 12 s at concurrency 1. Most of that is speech generation and the LLM's per-minute token limit on a free tier. Raise `--concurrency` on a paid endpoint. A rerun from cache takes seconds.

## Limitations

- Recall on injected errors is an upper bound for real errors, which are subtler.
- The regeneration diff finds deviations from what the model produces today, not errors it makes every time. Those rest on the judge.
- Languages that share a script (Hindi and Marathi) are told apart only by the judge.
- Subtle, fluent mistranslations depend on the judge model's skill in that language.
- Speech positions are approximate, because transcription returns no timestamps. Only silences have exact times.
- In-run statistics need at least 8 items per language. Smaller languages are pooled together.
- The cut-off that makes a missing audio tail exact evidence (a third of the text) was picked while looking at the demo data. It is the same for every language, and it caused no false alarm on half B.
- Hand labels cover only checks that need no reading of the language: numbers, format, script and English left in. See [`data/README.md`](data/README.md).

## What I would do next

- Raise the judge's precision per language with a few labelled clean examples from each new language.
- Add a voice-comparison mode for Indic-Speak.
- Get word timestamps, if the API offers them, so speech errors have exact times.

## AI assistance

Claude Code (Claude Opus) was used for research, planning, writing code and tests, and running the evaluations. The design decisions and their reasons are in [`DESIGN.md`](DESIGN.md), and I can explain every line.
