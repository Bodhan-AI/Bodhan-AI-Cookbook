# Bodhan Quality Harness

Scores Bodhan **Indic-Translate** translations and **Indic-Speak** audio, flags specific
errors with a character span and the evidence behind them, and reports what changed between
two runs — on a dataset it has never seen.

## What it does

Point it at a JSONL dataset of English sources and their Indian-language outputs. It runs
free deterministic checks first, escalates only suspicious items to an LLM judge, sends a
deliberate sample through an Indic-Transcribe round trip, then scores itself against human
labels and writes five report files.

It never writes or corrects Indian-language text. It evaluates and reports.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add BODHAN_API_KEY and an OpenAI-compatible judge
```

No system dependencies. Audio checks read PCM WAV directly, so ffmpeg is only needed if
your audio is in another container. A `Dockerfile` is included:

```bash
docker build -t bodhan-quality-harness .
docker run --rm -v "$PWD:/data" -v "$PWD/.env:/app/.env:ro" bodhan-quality-harness \
  --dataset /data/data/demo.jsonl --langs hi,ta,bn --out /data/report
```

## Run

```bash
python -m bodhan_eval \
  --dataset data/demo.jsonl \
  --langs hi,ta,bn \
  --out ./report \
  [--generate] [--baseline ./previous_report] [--calibration data/calibration.jsonl] \
  [--max-cost-inr 50] [--speech-sample 25] [--judge-sample 0] [--offline] [--inject]
```

| Flag | Meaning |
| --- | --- |
| `--generate` | Create missing translations and audio with Indic-Translate and Indic-Speak |
| `--baseline DIR` | Diff against a previous report (`diff.md`) |
| `--calibration FILE` | Extra labelled items, pooled with in-dataset labels, used to sweep thresholds |
| `--max-cost-inr N` | Hard budget ceiling; the run degrades instead of overspending |
| `--speech-sample N` | Max items sent for paid back-transcription |
| `--judge-sample N` | Also judge N clean items (0 = only suspicious ones) |
| `--offline` | No model calls at all — deterministic checks only |

`--langs` is authoritative: items outside the requested languages are filtered and counted.
`--offline` is how the test suite runs, and how you can verify the deterministic tiers
without spending credit.

Tests:

```bash
pytest -q        # 46 tests, no network required
```

## How it works

Checks are tiered by cost. Tier 0–1 are free and run on every item; the judge and the paid
audio round-trip are reached only when an item justifies them.

```
JSONL dataset -> loader (validate, skip bad items)
              -> tier 0: script, numbers, format
              -> tier 1: length, repeats, entities, chrF
              -> dedupe overlapping flags
              -> tier 2: LLM judge   (only flagged or sampled items)
              -> tier 3: speech      (signal checks always; round-trip on a sample)
              -> score vs labels -> 5 report files
```

| Check | Category | Tier | What it catches |
| --- | --- | --- | --- |
| `script.wrong_script` | language | 0 | Output in the wrong script for the target language |
| `script.untranslated` | language | 0 | Output identical to the English source |
| `numbers.added_or_changed` | number | 0 | A changed digit — localizes exactly |
| `numbers.missing` | number | 0 | A significant quantity dropped |
| `format.*` | format | 0 | Broken tables, headings, LaTeX commands |
| `length.repeat` | addition | 1 | Looped output — a verbatim 6-word shingle repeat |
| `length.*` | omission / addition | 1 | Content appearing or vanishing wholesale |
| `terms.entity_missing` | term | 1 | Acronyms and technical tokens that disappear |
| `reference.chrf` | mistranslation | 1 | Drift from a reference translation |
| `judge.llm` | mistranslation | 2 | Wrong meaning; localizing omissions |
| `speech.signal` | speech_garbled | 3a | Silence, clipping, near-silent or truncated audio |
| `speech.roundtrip` | speech_omission | 3b | Words or endings not spoken |

Three false-alarm controls matter more than any single check. Markdown tables and LaTeX
legitimately contain Latin text, so script and Latin-ratio checks run on **prose only**.
Proper names are transliterated rather than preserved, so the entity check ignores them.
And a threshold sweep refuses to tune on fewer than 8 examples per class, falling back to a
documented default instead of overfitting.

**Where the LLM is used.** Only as the judge, for meaning-level errors no string check can
see. It is given the flags already found so it does not re-derive the easy cases, told to
omit anything it is unsure about, and run at temperature 0 with caching. Consistency is
measured by re-asking with the cache bypassed, so the number reflects the model rather than
the cache.

**Calibration.** `length_lo` and `length_hi` are swept against labelled items and the whole
sweep table is written to `calibration.json`, so the choice is auditable.

**Outputs.** `summary.md`, `summary.json`, `items.jsonl`, `calibration.json`, and `diff.md`
with `--baseline`. `summary.json` carries a deterministic digest computed over everything
except timings, so a cached rerun can prove it reproduced the same result at zero cost.

## Results

On the 103-item demo set with deterministic checks only (`--offline`, no judge, no audio):

| Metric | Value | Target |
| --- | --- | --- |
| Detection recall | 78.3% | ≥ 80% |
| False alarms on clean items | 3.5% | ≤ 10% |
| Precision | 73.5% | — |
| Localization | 100% of detections | ≥ 70% |
| Determinism | identical digest on rerun | required |

Per category: `language` 0.92/1.00, `number` 0.78/0.88, `addition` 0.70/0.89,
`mistranslation` 1.00/0.14. Mistranslation recall is the honest gap — those errors are
semantic and belong to the judge tier, which is disabled offline.

A sample report from this run is in `docs/sample_report/`.

## Cost and runtime

Estimates at list price, per 100 items:

| Stage | Cost |
| --- | --- |
| Indic-Translate only | ~₹0.25 |
| + Indic-Speak (~200 chars/item) | ~₹12 |
| + Indic-Transcribe back-check (~20s/item) | ~₹3.3 |

A full run with speech is roughly **₹15 per 100 items**. Everything is cached by exact
input, so a rerun costs nothing and is byte-identical.

## Limitations

- **Mistranslation recall depends on the judge.** Deterministic checks cannot judge meaning;
  that is why the judge tier exists and why it is reached only when it earns its cost.
- **Omission localization is approximate.** A dropped clause has no position in the output,
  so the flag points at the nearest anchor or the head of the text.
- **Length ratio is noise below 8 source words** and is guarded accordingly.
- **Transliterated entities are not checked.** A wrongly transliterated name currently
  passes every check.
- **Indic-Transcribe accepts about 30 seconds of audio per request.** Longer audio must be
  chunked before it is scored; the harness reports undecodable or over-long audio rather
  than failing silently.
- **chrF is not a semantic metric.** It flags drift, not wrong meaning.

## Demo video

A walkthrough of a full run and the report is linked from the pull request. It is five
minutes or less and hosted somewhere openable without signing in.
