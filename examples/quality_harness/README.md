# Translation and speech quality harness

Evaluate supplied or generated translations and audio, localize defects, review uncertain checker evidence, and compare runs. Translation, synthesis and recognition use **Bodhan only**; a configurable OpenAI-compatible LLM judges meaning without producing corrections.

Implemented: deterministic checks, Bodhan adapters, request cache, retries, budget reservations, rubric judge with repeat assessments, human-label calibration, held-out metrics, text/audio injection, baseline diff, CI regression gate and review CSV import/export. [Task status](docs/task.md) distinguishes implementation from live evidence still needed.

Phase 3 now includes [blind, content-bound annotation and the full evidence workflow](docs/phase3_workflow.md), generation receipts reconciled against the model cache, strict benchmark coverage checks, and a [readiness audit](docs/phase3_readiness.md). The [Act and Transform brief](docs/phase3_act_and_transform.md) records the standards and falsification tests guiding this work.

## Setup

Python 3.10+, standard-library runtime and pinned setuptools build backend. No GPU or ffmpeg is needed for PCM16 WAV. Convert other containers to WAV using ffmpeg before evaluation.

```bash
cd examples/quality_harness
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e .
# Copy .env.example to .env; configure keys locally.
```

Local `.env` loads automatically; process environment wins. Override using `--env-file path`. Set `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_TRANSCRIBE_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`. The judge base URL includes the provider's `/v1` prefix. Generic `BODHAN_API_KEY` is an unused Cookbook-validator placeholder. Secret values are excluded from logs/cache identities; cached dataset content remains private local material.

For Cookbook validation, keep secrets outside the checkout: its scanner flags even ignored `.env` files. In this workspace the supplied Bodhan keys are preserved at `../../../.env.quality_harness`; pass `--env-file ../../../.env.quality_harness` on local live commands. Do not put keys in `.env.example`.

## Run

An offline installation check builds the explicit package source with the installed pinned backend, inspects wheel members and RECORD checksums, installs into a fresh virtual environment, and runs the CLI using isolated imports from outside the checkout:

```bash
python -m bodhan_eval.installation --out outputs/installation-check --env-file ../../../.env.quality_harness
```

The build interpreter must have the pinned `setuptools==80.9.0` installed. This check uses `--no-index --no-deps --no-build-isolation`, so it does not test downloading/building the backend in a fresh build environment. Its target runtime venv is fresh and does not inherit site packages. It is separate from Docker and live quality evidence; inspect `installation.json`, recorded logs and the [installation evidence](docs/installation_validation.md).

Credential-free smoke run and tests:

```bash
python -m bodhan_eval --dataset data/smoke.jsonl --langs hi,ta,bn --out outputs/offline --offline
python -m unittest discover -s tests -v
```

The assignment interface is unchanged:

```bash
python -m bodhan_eval --dataset path/to/data.jsonl --langs hi,ta,bn --out outputs/report --max-cost-inr 50
```

For judging, add **both** `--llm-input-inr-per-million RATE` and `--llm-output-inr-per-million RATE` using actual provider prices (0 only for a free endpoint). Missing rates become an explicit judge configuration failure without an LLM call; Bodhan checks can still run.

Use `--generate` for missing translation/audio. `--speech-sample-rate 0.25` samples synthesis by stable ID/language hash; the default synthesizes every missing audio item. Supplied audio is always checked. `--concurrency 3` bounds calls, `--retries 2` bounds transient retries, and `--judge-repeats 2` makes separately cached assessments using identical prompts. Items execute in input order; independent checkers within each item overlap.

```bash
python -m bodhan_eval --dataset path/to/new.jsonl --langs hi,ta,bn --out outputs/new --baseline outputs/report --regression-limit 0
```

The optional CI gate exits 2 when new evidence flags exceed the limit. Changed spans may count as new flags; this is a review signal rather than proof of worse quality. Invalid records and failed checks are counted without stopping valid items.

## How it works

1. Validate JSONL independently; require `id`, English `source`, `lang`. Optional fields follow the assignment schema. Additional `terms` maps source terms to expected targets, and `transcript` supplies ASR text. Audio paths are relative to the dataset.
2. Normalize Unicode/digits while retaining original code-point offsets. Check numbers, literal markup, protected terms and expected script. Reference measures are character-bigram F1 and token alignment, not semantic verdicts.
3. Generate missing outputs only on request. Reject truncated translations. Split existing TTS input without rewriting; join compatible WAV chunks. ASR divides WAV into <=25-second chunks.
4. Run Bodhan back-translation and ASR independently. Measure duration, silence and clipping. Exact digital silence with nonempty intended speech is a deterministic garbled-audio finding; other signal measurements require context.
5. Apply the [judge rubric](src/bodhan_eval/judge.py) to original text and checker evidence. Reject invalid schemas, categories, severities, offsets, invented quotes and non-ASCII explanation prose. Indic text is permitted only as an exact existing quote. Omission points to source; speech omission points to intended translation. Uncertainty or repeat disagreement enters review. Proposed speech findings trigger independently repeated Bodhan ASR; inconsistent ASR remains uncertain.
6. Tune category-specific judge-confidence thresholds on human development labels, maximizing F1 with false-positive tie breaking. Exclude all development-parent siblings from held-out evaluation. Report one-to-one precision/recall, detection recall, clean false alarms, localization overlap and category accuracy per language/category/check. Unsupported denominators are null.

Accepts all 13 production languages: `en,hi,bn,mr,te,ta,gu,kn,ml,or,pa,as,ur`, plus hosted preview languages. See [API verification](docs/api_verification.md), [PRD](docs/prd.md), and [architecture/flow](docs/implementation_strategy.md).

## Calibration and review

For the submission dataset, use the whole-item blind workflow in [phase3_workflow.md](docs/phase3_workflow.md): `python -m bodhan_eval.annotations export`, `import`, then `apply`. It includes clean negatives, hides injection IDs and judge verdicts, binds answers to exact source/translation/reference/terms/audio content, and rejects stale answers. ASR transcripts are checkers rather than ground truth; human speech spans use intended translation or audio seconds. `--require-human-clean` on the injector requires current attributable parent reviews. Generated changes invalidate inherited human labels, and mutations never inherit their parents' clean review hash. The candidate-only `review.csv` workflow below remains useful for triage, not unbiased false-alarm measurement.

`--calibration labels.jsonl` accepts `id`, `lang`, `labels`, optional `label_origin`. External annotations without an origin are marked `provided_calibration`; self-authored human labels must use `human:<annotator>`. Inline `split: calibration` records are also eligible with human provenance. Programmatic annotations never tune human thresholds. Parent IDs should be shared across languages and mutations to prevent leakage.

Missing labels means unknown; `[]` means explicitly annotated clean. Labels use `type`, `severity`, `span_field`, and original half-open code-point `span`. Default field is translation. Audio labels may instead use seconds-based `time_span`. Do not automatically mark generated outputs clean.

Fill `human_type`, `human_severity`, `annotator` in review.csv; edit the suggested span if necessary. Use `human_type=clean` only after reviewing the entire item, not just one candidate.

```bash
python -m bodhan_eval.review --csv outputs/report/review.csv --out outputs/human_labels.jsonl
```

## Reports, cache and costs

Outputs: `summary.md`, `summary.json`, `items.jsonl`, `calibration.json`, `checks.jsonl`, `outputs.jsonl`, `review.csv`, optional `diff.md`. `outputs.jsonl` feeds subsequent stages without report metadata. Evidence includes original field/span or audio time interval. Coverage distinguishes skipped/failed/completed checks. Diffs distinguish removed items from resolved flags.

Cache identity includes endpoint, exact request/model/settings, rubric, audio hash and independent-assessment index. Atomic writes and locks coalesce duplicate requests within a process. Separate processes should use separate cache directories. Corrupt entries fail instead of silently triggering regeneration.

Every network attempt reserves a conservative estimate before sending. Failed attempts retain reservations because they may have been billed. Translation uses maximum output tokens, speech input characters, ASR measured duration, and judging caller-supplied prices. Estimates use assignment tariffs and are not invoices or provider-enforced caps; hidden reasoning token accounting can differ.

Stable report costs show recorded uncached reservations for unique successful requests. Current execution's network attempts, retry-inclusive reservations and wall time are in `CACHE_DIR/last_execution.json`, outside deterministic artifacts. Successful cached replay makes zero calls and reserves INR 0. Use identical dataset/settings/model configuration and the same cache.

```bash
python -m bodhan_eval.benchmark --dataset data/benchmark_smoke.jsonl --langs hi,ta,bn --out outputs/benchmark -- --offline
# For live benchmarks use --generate and judge-price flags after -- instead of --offline.
```

The benchmark replays at zero budget and checks artifact hashes, frozen dataset, zero-network execution and assessed population. A source-only/failed run cannot pass simply because hashes match. The nine-record benchmark fixture contains only supplied outputs; the separate ten-record smoke fixture intentionally includes an unassessed item and is rejected by the stricter benchmark. Choose a fresh output directory and cache for cold hosted timing; the manifest records whether the cache was initially empty.

## Demo and Docker

The [measured demo report](sample_report/live_demo/summary.md) covers 116 completed Bodhan items and 537 held-out synthetic errors. Deterministic recall was 59.4%. A separate speech comparison using supplied baseline transcripts reached **87.7% recall**, with **65.4% localization overlap** against the 70% target. Human calibration and clean-item false alarms remain unmeasured. The report links the per-language and per-category counts and the paired scoring command.

Run the [116 supplied translations](data/demo_translations.jsonl) without API credit:

```bash
python -m bodhan_eval --dataset data/demo_translations.jsonl --langs hi,ta,bn --out outputs/demo-text --offline
```

The [4:38 captioned walkthrough](https://drive.google.com/file/d/1vW_w1wZhsWNULyqV7Zbl13k1oqTPtNjG/view?usp=sharing) shows the cached replay of all 120 demo inputs, the report, and the separate paired-ASR comparison. A public link to the generated audio is pending. Next, complete human review of a small representative subset, improve speech time localization, measure judge coverage on injected examples and run the full Cookbook and container gates.

[120 demo sources](data/demo.jsonl) cover Hindi, Tamil and Bengali; [provenance/preparation](data/README.md) explains generation and injection. Generate Bodhan outputs, review clean parents, then run `python -m bodhan_eval.inject`. No generated audio belongs in git. [Sample report](sample_report/summary.md) and [offline benchmark](docs/offline_benchmark.json) demonstrate local plumbing, not semantic quality.

```bash
docker build -t bodhan-quality-harness .
docker run --rm bodhan-quality-harness --dataset data/smoke.jsonl --langs hi,ta,bn --out /tmp/report --offline
```

For live Docker use bind mounts for dataset/output/cache and `--env-file .env`. Docker is unavailable in this development environment; container validation remains pending. [PR draft](docs/pr_description.md) and [recording plan](docs/demo_recording.md) identify remaining submission evidence.

## Limitations

Live compatibility, human-calibrated detection quality, hosted runtime/cost, a completed generated-and-labelled demo and a public video remain unverified. The credentialed smoke attempt was blocked by execution-environment network permissions; see [live validation evidence](docs/live_validation.md). Repeated ASR can repeat systematic errors; a text judge cannot hear audio. Consistency does not prove accuracy. Numeric checks can misread number words, localized grouping and valid unit conversions. Terms need explicit correspondences, shared scripts cannot identify languages, and markup checks are not full parsers. Character-bigram F1 is not BLEU/chrF++ or learned QE. Inspect support counts before trusting thresholds.

The injector covers numeric, addition, wrong-language, literal-markup, protected-entity, paragraph-omission and three audio mutations. Arbitrary semantic mistranslations need reviewed correspondences; they are not fabricated. Paragraph omission assumes aligned structure. Unreviewed parents remain unlabelled; synthetic labels remain distinguishable from human labels. The hosted <30-minute target must be measured.

## AI assistance

Codex implemented code, docs, tests and original synthetic English sources. Only Bodhan may generate Indic demo translations/speech. Human annotations, live quality measurements and the video are not fabricated.
