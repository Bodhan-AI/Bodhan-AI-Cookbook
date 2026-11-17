# Translation and speech quality harness

> Finds, localizes and explains errors in Indic-Translate translations and Indic-Speak audio, calibrates itself against labelled items, and diffs two runs.

**Bodhan models:** indic-translate, indic-speak, indic-transcribe
**Languages shown:** Hindi, Tamil, Bengali (demo); Marathi, Telugu (held out). Any language in [`languages.json`](src/bodhan_eval/languages.json) works, including all 13 Indic-Speak production-tier languages.
**Format:** CLI (`python -m bodhan_eval`)

## What it does

Give it a JSONL file of English sources with translations, audio or neither. It scores each item with cheap deterministic checks first, then Bodhan round trips (back-translation with `indic-translate`, back-transcription with `indic-transcribe`), then an LLM judge that reads the evidence and confirms or rejects each finding. Every flag names its category, severity, the exact character span (and audio time range for speech), and the evidence behind it. With `--generate` it creates missing translations and audio with Bodhan models first. With `--calibration` it measures itself against labelled items and tunes its thresholds. With `--baseline` it reports what improved and what regressed.

Design notes: [docs/DESIGN.md](docs/DESIGN.md) (architecture diagram, flow chart, every check, judge rubric, calibration). Decisions and trade-offs: [docs/DECISIONS.md](docs/DECISIONS.md). Sample report: [sample_report/summary.md](sample_report/summary.md) (open [sample_report/dashboard.html](sample_report/dashboard.html) locally for the filterable view).

## Setup

Python 3.11+ and `libsndfile` (bundled in the `soundfile` wheels on Windows, macOS and Linux x86_64). `ffmpeg` is optional and only needed for audio formats other than WAV, FLAC and OGG.

```bash
cd examples/translation_quality_harness
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt                      # pinned deps + this package
cp .env.example .env                                 # then fill in the keys
```

| Variable | Used for |
|---|---|
| `BODHAN_TRANSLATE_KEY` | indic-translate: generation and back-translation |
| `BODHAN_SPEAK_KEY` | indic-speak: generating missing audio (only with `--generate`) |
| `BODHAN_TRANSCRIBE_KEY` | indic-transcribe: back-transcription |
| `BODHAN_API_KEY` | optional single key used for any model without its own |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | any OpenAI-compatible chat-completions endpoint for the judge |
| `LLM_REASONING_EFFORT` | optional, sent as `reasoning_effort` to reasoning models; dropped automatically if unsupported |

Every key is optional: without a Bodhan key the dependent checks are skipped and reported; without an LLM the deterministic and round-trip checks still run.

Docker: `docker build -t bodhan-eval . && docker run --rm -v "$PWD:/work" bodhan-eval --dataset data/demo.jsonl --langs hi,ta,bn --out /work/report --offline --judge off --max-cost-inr 0 --cache-dir .demo_cache`

## Run

```bash
python -m bodhan_eval \
  --dataset data/demo.jsonl \
  --langs hi,ta,bn \
  --out ./report \
  [--generate] [--baseline ./previous_report] [--calibration data/calibration.jsonl] [--max-cost-inr 50]
```

Download the generated audio and recorded Bodhan outputs from the public release (no model calls). Audio stays outside Git, as required by the assignment:

```bash
python scripts/fetch_demo_assets.py
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out sample_report --calibration data/calibration.jsonl --judge off --judge-repeats 0 --offline --max-cost-inr 0 --cache-dir .demo_cache
```

Extra flags (all optional):

| Flag | Default | Meaning |
|---|---|---|
| `--judge all\|suspicious\|off` | `all` | which items the judge reads; `suspicious` skips items with no signal at all |
| `--judge-votes K` | 1 | majority vote over K judge samples per item |
| `--judge-repeats N`, `--judge-repeat-sample M` | 1, 30 | re-judge M items N times to measure consistency |
| `--speech auto\|off`, `--speech-sample F`, `--voice NAME` | auto, 1.0 | speech checks; share of items synthesized with `--generate`; voice override |
| `--thresholds FILE` | shipped defaults | reuse a previous report's `thresholds.json` |
| `--fail-on-regression N` | off | with `--baseline`, exit code 3 when more than N items regress (CI mode) |
| `--offline` | off | cached model outputs only; a cache miss is recorded without an API request |
| `--cache-dir DIR`, `--no-cache` | `.bodhan_cache` | cache location; `--no-cache` ignores cached outputs |
| `--max-items N`, `--concurrency N`, `--verbose` | | smoke runs and tuning |

Other commands:

```bash
pytest                                                    # unit tests + 10-item end-to-end test (no network)
python -m bodhan_eval.inject --input clean.jsonl --output injected.jsonl --rate 0.5 [--align-terms]
python -c "from bodhan_eval.reporting.review import import_answers as f; f('report/review_queue.csv', 'data/demo.jsonl', 'my_labels.jsonl')"
python scripts/build_flores_dataset.py --flores-dir flores101_dataset --out outputs/flores-fixtures
python scripts/build_bodhan_demo.py --input data/flores_inputs.jsonl --out data/demo.jsonl --audio-dir data/audio --max-cost-inr 3
```

## Output

```
report/
  summary.md          headline numbers, quality targets (met or missed, and why), per language, per category, top failures, regressions, bad items, cost
  summary.json        the same, machine-readable
  items.jsonl         per item: every check with status and metrics, flags (category, severity, confidence, span, source_span, audio_span, excerpt, evidence, checks, judge verdict), below-threshold candidates, MQM score
  calibration.json    precision and recall per category, per language and per check, judge statistics, tuning log, thresholds before and after
  diff.md, diff.json  only with --baseline: regressions and improvements with examples, deltas per language and category
  dashboard.html      self-contained static view with filters by language, category, severity and status
  review_queue.csv    uncertain decisions for human labelling; import the answers back as calibration labels
  thresholds.json     the thresholds used, reusable with --thresholds
  run.json            wall time, cache hits, INR spent in this run, projected minutes for 200 items
  run.log.jsonl       structured log: run id, stage, item, model, cache hit or miss, latency, cost
```

## How it works

1. **Load.** Malformed rows, missing fields and duplicate ids are recorded and skipped; the run never stops on a bad item.
2. **Normalize.** NFC with grapheme-aware clustering, zero-width characters removed, native digits folded to ASCII, danda and quotes normalized; every span maps back to the original text.
3. **Cheap checks.** Script and language (including Marathi in Hindi and Assamese in Bengali), numbers and units, Markdown and LaTeX structure, Latin-script terms, chrF++ against a reference when present, audio signal (silence, clipping, noise, duration).
4. **Round trips.** Back-translation to English, compared clause by clause with the source, with a reliability factor so a noisy round trip does not raise flags. Back-transcription, aligned word by word with the spoken text, to find omissions, truncation, repetition and garbled stretches while ignoring ASR spelling noise.
5. **Judge.** An LLM reads numbered tokens, the back-translation and all findings, confirms or rejects each finding, and adds errors only a reader can see. It answers with token indices, never with Indian-language text.
6. **Fuse and calibrate.** Signals combine by noisy-OR, adjusted by the judge's verdicts, and are compared with per-category thresholds tuned on the calibration set.
7. **Report.** Per-item evidence, per-language and per-category summaries, MQM quality scores, cost, and a baseline diff.

Error categories and the layers that catch them:

| Category | Deterministic | Round trip | Judge |
|---|---|---|---|
| Omission | length outlier, sentence count | missing source clause in back-translation | yes |
| Addition | length outlier | back-translated sentence with no source support | yes |
| Mistranslation | | low clause coverage (weak) | primary |
| Number or unit | digit multiset, symbols, LaTeX digits | units and numbers in the round trip | yes |
| Term or entity | Latin term spelling | name missing from the round trip | yes |
| Format | Markdown and LaTeX structure | | yes |
| Wrong language or script | script shares, English runs, sibling-language markers | | yes |
| Speech omission or truncation | silent clip, short duration | deletion runs in the transcript | |
| Speech repetition | long duration | duplicated runs in the transcript | |
| Garbled audio | silence, clipping, noise | unintelligible stretches | |

Bodhan model usage and prices (hosted, from the brief and the [API docs](https://console.bodhan.ai/api-docs/)):

| Model | Endpoint | Used for | Price | Limits respected |
|---|---|---|---|---|
| `indic-translate` | `POST /translate`, with `/v1/chat/completions` fallback on 404 | generation, back-translation | INR 0.20 per 10,000 output tokens | inputs over 3,000 characters split by paragraph; `max_output_tokens` set from input length |
| `indic-speak` | `POST /v1/audio/speech` | generation of missing audio | INR 6 per 10,000 input characters | one sentence-sized chunk (at most 220 characters) per request; first listed voice per language unless `--voice` |
| `indic-transcribe` | `POST /v1/audio/transcriptions` | back-transcription | INR 0.10 per audio minute | 16 kHz mono WAV, windows of at most 25 s cut at quiet points |

## Results

Measured on 18 November 2026 by replaying real, previously recorded Bodhan outputs with **`--offline --judge off --max-cost-inr 0 --cache-dir .demo_cache`**. No translation, speech, transcription or judge requests were made during this final evaluation. The general LLM judge remains implemented for the evaluators' own OpenAI-compatible endpoint; it is intentionally disabled in the submitted demo run.

[The text-only demo](data/demo.jsonl) contains 120 real Indic-Translate outputs across Hindi, Tamil and Bengali (40 each): 60 untouched items, 51 text injections and nine speech injections. The [external asset archive](https://github.com/Purv-Kabaria/Bodhan-AI-Cookbook/releases/download/quality-harness-demo-v1/quality-harness-demo-assets.zip) supplies 18 short Indic-Speak audio clips (six per language, nine untouched controls). The download installs a separate `.demo_cache` and leaves any existing live model cache untouched. Its SHA-256 checksum and individual audio checksums are pinned in [data/demo_assets.json](data/demo_assets.json). Source provenance and clean-output hashes are retained in [data/demo.build.json](data/demo.build.json).

Thresholds were tuned only on 90 FLORES calibration fixtures with programmatically injected labels (45 errors, 45 untouched). The Marathi/Telugu holdout reuses those thresholds without tuning. Four held-out English sources also occur in calibration; this is a language holdout, not a fully unseen-content benchmark. These synthetic labels are not human adjudications.

| Metric | Assignment target | Hosted demo, judge off | Language holdout, judge off |
|---|---|---|---|
| Detection recall | >=80% | 70.0% (42/60), missed | 70.0% (21/30), missed |
| Untouched-item false-alarm rate | <=10% | 8.3% (5/60), met | 3.3% (1/30), met |
| Localization among detected errors with spans | >=70% | 90.5% (38/42), met | 90.5% (19/21), met |
| Category accuracy among detections | >=70% | 97.6% (41/42), met | 100% (21/21), met |
| Localization recall over all seeded spans (additional metric) | Not an assignment target | 63.3% (38/60) | 63.3% (19/30) |
| Judge consistency | >=90% | Unmeasured; judge disabled | Unmeasured; judge disabled |

| Language | Items | Injected errors | Detection recall | False-alarm rate | Localization among detections |
|---|---|---|---|---|---|
| Hindi | 40 | 20 | 80.0% | 5.0% | 93.8% |
| Tamil | 40 | 16 | 56.3% | 16.7% | 77.8% |
| Bengali | 40 | 24 | 70.8% | 0% | 94.1% |
| Marathi (held out) | 30 | 12 | 58.3% | 5.6% | 100% |
| Telugu (held out) | 30 | 18 | 77.8% | 0% | 85.7% |

All 120 demo back-translations and all 18 back-transcriptions were replayed successfully; no items failed or were skipped. Three speech omissions and three garbling injections were detected, but only one of three repetitions. None of the nine seeded mistranslations was detected with the judge disabled. Format precision is low because structural findings often point at a different span from the injected corruption. Per-category precision/recall, spans and evidence are in [sample_report/summary.md](sample_report/summary.md) and [sample_report/calibration.json](sample_report/calibration.json). The [held-out report](heldout_report/summary.md) and [baseline diff](baseline_report/diff.md) are included.

Recorded Bodhan spending across the earlier generation and evaluation runs was **INR 2.0379**, within the INR 5 Bodhan allowance. Generation cost INR 1.4458 (INR 0.1594 translations, INR 1.2864 speech). The final offline evaluations cost INR 0. The [cost ledger](sample_report/cost_ledger.json) preserves historical accounting. Judge billing is separate from that Bodhan tracker; earlier experiments exhausted the judge credit. No more judge requests were made for this submission, and a monetary total for those experiments is not claimed.

| Cost normalization | Recorded output cost, INR per 100 |
|---|---|
| Text checks, normalized over 210 demo + calibration items | 0.0995 |
| Additional back-transcription, normalized over 100 audio items | 1.7900 |
| Both, if every item had speech | 1.8895 |

These attributed costs count recorded outputs even when replayed from cache; they exclude generation and the optional LLM judge. The actual demo uses speech for 18/120 items. Cached runtime is recorded in `run.json`; earlier hosted checks completed in 704 seconds for 210 demo/calibration items, but complete judge coverage and a cold 200-item runtime remain unverified. [Offline verification](docs/OFFLINE_VERIFICATION.json): 121 tests pass, and two cached runs produce byte-identical deterministic reports; `run.json` and the event log intentionally retain run-specific timing. The video demonstrates the replay and baseline comparison.

## Demo video

[Watch the recorded demo (under five minutes)](https://github.com/Purv-Kabaria/Bodhan-AI-Cookbook/releases/download/quality-harness-demo-v1/quality-harness-demo.mp4). It shows the complete cached CLI run, quality targets, per-language/category results, a localized flag and the baseline diff. It is explicitly an offline replay, with no new model generation.

Held-out replay:

```bash
python -m bodhan_eval --dataset data/heldout_mr_te.jsonl --langs mr,te --out heldout_report --thresholds sample_report/thresholds.json --judge off --judge-repeats 0 --offline --max-cost-inr 0 --cache-dir .demo_cache
```

The assignment's standard command remains supported unchanged. For a live evaluation, supply the Bodhan and judge settings in the table above and omit `--offline` and `--judge off`. That is a separate, paid run; the demo needs neither credentials nor live models.

## Limitations

- The final demo deliberately disables the LLM. Judge consistency and full LLM-assisted accuracy are unmeasured, not passing. The hidden evaluator can use its own provider with the same rubric and CLI.
- Labels identify injected changes, not all natural model errors. Untouched outputs are not human-certified correct. All labels are attributed to the programmatic injector; no human review is claimed. Supplied human labels are supported for calibration.
- Recall misses the target, especially for mistranslation and speech repetition. Tamil's false-alarm rate also misses the target. These results have not been improved by changing thresholds for particular demo items.
- Speech has only 18 short clips and three seeded examples per category. It does not establish paragraph, Markdown or LaTeX speech accuracy. Within-chunk audio span mappings are approximate.
- Human FLORES calibration/held-out fixtures differ from hosted demo output. Source overlap and synthetic corruption limit generalization. Reference-based chrF is supported and tested, but no reference fields were supplied in this evaluation.
- Back-translation and ASR introduce their own errors. Same-script language identification is limited. The global Cookbook gate has an existing Android recipe validation failure; the portability fix is submitted separately in [PR #25](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/pull/25), keeping this recipe PR self-contained.

## AI assistance

Claude Code (Anthropic) and Codex (OpenAI) assisted with implementation, regression tests, demo generation, self-evaluation and documentation. Demo translations and speech come from the hosted Bodhan models. The configurable LLM judge returns findings and token indices; it does not generate the demo translations or audio.
