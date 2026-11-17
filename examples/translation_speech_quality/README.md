# Translation and speech quality harness

`bodhan_eval` finds and explains quality problems in Bodhan Indic-Translate output and Indic-Speak audio. For every row of a JSONL dataset, it reports specific defects, each with:

- a category and a severity
- the exact span, or the audio interval
- the evidence behind it

It also measures itself against human and injected labels, and compares two runs.

Cheap deterministic checks run first. Bodhan round trips (back-translation and back-transcription) supply independent evidence. An LLM judge reads the source and candidate as numbered words, so it can name meaning errors without writing any Indian-language text. Calibrated thresholds turn the combined evidence into flags.

- [Harness design](DESIGN.md): architecture diagram, flow chart, every check, the judge rubric, calibration and how flags combine.
- [Decision log](DECISIONS.md): each design choice, the evidence behind it, and the test that verifies it.
- [Sample report](sample_report/summary.md): a real run on the demo dataset, with [items.jsonl](sample_report/items.jsonl), [calibration.json](sample_report/calibration.json) and [diff.md](sample_report/diff.md).
- [Demo video](VIDEO_URL) (under 5 minutes): a full run on the demo dataset and a walk through the report.

## Setup

You need Python 3.11+ (3.12 is tested) and FFmpeg on your `PATH`. FFmpeg decodes supplied audio in formats other than WAV. Install from this recipe directory:

```sh
python -m pip install -e ".[dev]"     # runtime dependencies are pinned in requirements.txt
cp .env.example .env                   # then fill in the keys
```

| Variable | Used for |
| --- | --- |
| `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_TRANSCRIBE_KEY` | Indic-Translate, Indic-Speak and Indic-Transcribe at `https://api.bodhan.ai` ([console](https://console.bodhan.ai/), [API docs](https://console.bodhan.ai/api-docs/)) |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Any OpenAI-compatible Chat Completions endpoint, used for the judge |

Environment variables take precedence over `.env`. To run in Docker instead:

```sh
docker build -t bodhan-quality .
docker run --rm --env-file .env -v "$(pwd):/work" bodhan-quality --dataset data/demo_scored.jsonl --langs hi,ta,bn --out outputs/report
```

## Run

```sh
python -m bodhan_eval --dataset path/to/data.jsonl --langs hi,ta,bn --out ./report \
  [--generate] [--baseline ./previous_report] [--calibration labels.jsonl] [--max-cost-inr 50]
```

- **Dataset rows.** Each row needs `id`, `source` and `lang`. The optional fields are `reference`, `translation`, `audio` (a path relative to the dataset file) and `labels`.
- **Languages.** `--langs` accepts any of `as,bn,en,gu,hi,kn,ml,mr,or,pa,ta,te,ur`. No check is specific to a language.
- **`--generate`** fills in missing translations (Indic-Translate) and audio (Indic-Speak). Without it, the harness scores only what is supplied. An empty supplied translation counts as an omission.
- **Labels.** Each label has `type`, `severity`, and optionally `field` and `span` (half-open `[start, end]` in Unicode code points, or milliseconds for `audio_ms`). A bare span from the assignment format is accepted too. Small spelling differences are normalized. A row whose labels can't be parsed is still evaluated, and the problem is reported.
- **`--calibration labels.jsonl`** adds human labels keyed by `id` and `lang`, or by the opaque `review_id` from a blind review packet. Add `annotator` for attribution and `"split": "holdout"` for held-out rows.
- **`--max-cost-inr`** caps Bodhan spend. The default is ₹50. The judge provider bills separately; its token usage is reported.
- **Other flags:**
  - `--cache` is a reusable SQLite response cache.
  - `--concurrency` and `--judge-concurrency` set the parallel limits.
  - `--judge-repeat N` judges N rows a second time to measure consistency. The default is 10, and 0 turns it off.
  - `--no-speech` runs text-only.
  - `--limit` takes the first N rows.

Bad JSON lines and rows with missing fields are logged, skipped and counted. A failure in one check, or an unexpected error in one row, is recorded on that row and the run continues.

### Outputs

| File | Contents |
| --- | --- |
| `summary.md` | Headline counts; a language-by-category table; change against the baseline; quality targets with what was observed; checks that did not complete; the top findings per category |
| `summary.json` | The same, machine-readable, plus cost and the effective configuration (judge model, rubric hash, thresholds) |
| `items.jsonl` | One row per item: every check's status and metrics, the candidate, the back-transcript, flags with category, severity, span, evidence, rationale and check, plus `unconfirmed` weaker evidence |
| `calibration.json` | Thresholds and the label support behind them; precision and recall per category and per language, localization, category accuracy, and judge agreement; a per-check breakdown |
| `diff.md` | Only with `--baseline`: new, resolved and persistent flags matched by evidence position, severity changes, and coverage changes |
| `run.jsonl` | Structured log of every provider call (status, attempt, latency) and every check per item, so any flag can be traced |

## The demo

| Path | What it is |
| --- | --- |
| `data/demo.jsonl` | 72 original English educational passages × hi, ta, bn (216 rows, source only). Includes Markdown lists, tables, LaTeX, units and named entities. |
| `data/demo_scored.jsonl` | The demo dataset as scored. Its 231 rows are 108 Bodhan-generated controls plus 123 labelled injected variants: 4 per category per language, plus 3 human-reviewed rows. |
| `data/demo_baseline.jsonl` | The same rows as the injector found them, before mutation. Scoring it gives the `--baseline` report behind `sample_report/diff.md`. |
| `data/human_labels.jsonl` | 6 Hindi rows labelled by hand by Aafi Malek from a blind review packet, where opaque IDs hide which rows were mutated. One label was revised on a second look. Each row has a `note` explaining the verdict. |
| `sample_report/` | The reports from scoring `data/demo_scored.jsonl` against the baseline: `summary.md`, `summary.json`, `items.jsonl`, `calibration.json`, `diff.md`, `dashboard.html`, `review_queue.csv` and `run.jsonl`, plus a three-voice Hindi comparison. |

The generated audio is not committed. Download the [audio bundle](AUDIO_BUNDLE_URL) and unzip it next to `data/demo_scored.jsonl`, so its `audio/` paths resolve. To rebuild everything from the source passages with your own keys (about ₹11 of Bodhan credit, most of it Indic-Speak):

```sh
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out outputs/controls --generate --limit 108
python -m bodhan_eval.demo --report outputs/controls --out outputs/demo --per-category-language 4
python -m bodhan_eval --dataset outputs/demo/comparison_controls.jsonl --langs hi,ta,bn --out outputs/baseline
python -m bodhan_eval --dataset outputs/demo/demo.jsonl --langs hi,ta,bn --out outputs/report \
  --baseline outputs/baseline --calibration data/human_labels.jsonl
```

The injector never uses an LLM. Text errors are made in one of two ways:

- programmatic edits: digits, units, Markdown and LaTeX breaks, pasted English, deleted or spliced Bodhan sentences
- perturbing the English source and re-translating with Indic-Translate: dropped sentences, added sentences, antonym flips, renamed entities

Audio errors are real signal edits: truncation, a cut, a repeated phrase, inserted silence, inserted noise. Each label carries the exact span of the edit.

## Results on the demo

These are measured on `data/demo_scored.jsonl` (231 rows) with Cerebras `qwen-3.8-27b` as the judge. Scores use the held-out split: rows whose English source was never used for threshold fitting. The full report is in [sample_report/summary.md](sample_report/summary.md), and [sample_report/dashboard.html](sample_report/dashboard.html) shows every flag with filters.

| Brief target | Target | Observed on the demo |
| --- | ---: | --- |
| Detection recall on seeded errors | ≥80% | **95.6%** (87 of 91) |
| Localization (flag overlaps the seeded span) | ≥70% | **98.8%** (79 of 80 with declared coordinates) |
| Category accuracy | ≥70% | **100%** (87 of 87) |
| Judge consistency (second independent verdict) | ≥90% | **100%** (10 of 10) |
| False alarms on clean items | ≤10% | **9.6%** of unmutated controls flagged (7 of 73). This is an upper bound, because several of these flags are real Bodhan defects (see below). On 200 clean human FLEURS recordings, 1 clip was flagged: it has real clipping. |
| Determinism | rerun from cache is identical at no model cost | Verified by the end-to-end test (byte-identical reports, zero requests). |
| Runtime | 200 items with speech in under 30 min | Measured on the final code with a fresh cache, on 200 human-read [FLEURS](https://huggingface.co/datasets/google/fleurs) clips (CC BY 4.0, hi/ta/bn): **14.1 min** for Bodhan back-transcription, back-translation and every local check, with 281 Bodhan 429 retries absorbed. The judge is added on top and depends on the endpoint. On Cerebras `qwen-3.8-27b` at concurrency 4, the demo's 147 judge calls completed inside a 9.6-minute run, which puts a full run at roughly 25 minutes. That total is an estimate, not a single measured run. FLEURS was used only for runtime and false-alarm checks. Its audio is not committed. |

| Category | Precision | Recall | | Language | Recall | Localization |
| --- | ---: | ---: | --- | --- | ---: | ---: |
| omission | 69% | 100% | | hi | 96.8% | 100% |
| addition | 100% | 100% | | ta | 93.3% | 100% |
| mistranslation | 67% | 89% | | bn | 96.7% | 96.2% |
| number | 100% | 100% | | | | |
| entity | 71% | 56% | | | | |
| format | 60% | 100% | | | | |
| wrong_language | 100% | 100% | | | | |
| speech_omission | 100% | 100% | | | | |
| speech_repetition | 100% | 100% | | | | |
| garbled_audio | 100% | 100% | | | | |

**Languages and lengths not in the demo.** Two live checks covered languages and passage lengths outside the demo:

- **Long passages:** 12 Markdown passages of about 550 characters, each with a heading or list, a table or LaTeX, and prose, in Urdu and Telugu. Bodhan generated the translations and audio (48–69 s per clip, so several ASR chunks per clip), and the Groq `openai/gpt-oss-20b` judge scored them. No format, number, script or speech flags were raised, even though spoken numbers and markup pushed transcript error to 0.13–0.49. The judge raised 2 meaning flags. One is a dropped comparative, "more", in Telugu, which matches a real Bodhan pattern seen in the demo.
- **Short sentences:** 12 rows in Telugu and Marathi, with no flags.

**Reading the precision column.** Precision counts any flag that does not match the *injected* label as a false positive. Many of those flags point at real defects in the Bodhan output that the injector did not plant:

- a LaTeX equation dropped from a Bengali translation
- a table whose separator row Bodhan left out
- "right container" rendered as "correct container"
- "speed" rendered as "velocity"

The same is true of the flagged controls. The judge-side precision against human labels is reported separately in `calibration.json` → `human_heldout_by_check`.

**Entity recall** is low for a known reason. The demo ran out of judge credit before re-judging the 6 regenerated entity rows, so those rows show `judge: http_402` in "Checks that did not complete". The other 12 rows with that error are 5 controls and 7 speech variants whose new speech evidence needed a fresh verdict. This is also the graceful-failure path working as intended: the deterministic checks still scored all 18 rows, and the report counts the gap.

## Where the LLM is used, and why

- **Judge, one call per row.** It covers the defects that need meaning: omission, addition, mistranslation, entity rendering, and same-script language confusion. It also confirms or rejects the deterministic suspicions it is shown. The rubric is in [judge.py](src/bodhan_eval/judge.py). It asks for one finding per defect with category, severity, strength and word range, and a short English rationale.
- **Speech routing.** The judge can set `verify_speech` to ask for a second ASR segmentation when the transcript suggests a skip or loop that cheaper evidence could not settle.
- **Measuring the judge.** Agreement between two independent verdicts is reported on every run. Its per-check precision and recall against human labels are in `calibration.json` under `human_heldout_by_check`.

The sample report used Cerebras `qwen-3.8-27b`. The harness was also run with Groq `openai/gpt-oss-120b` on the first 34-row demo (100% recall there) and with Cerebras `gpt-oss-120b`. The request uses JSON mode at temperature 0. Endpoints that reject those options get a plain request. Replies wrapped in reasoning blocks or prose are parsed.

For a low-quota endpoint, `--judge-concurrency`, `--judge-rpm` and `--max-retry-wait` pace the judge. A daily-quota error is recorded on each affected row immediately, with the provider's reason in `run.jsonl`, rather than stalling the batch.

## Bodhan models

| Model | Use | Access | Settings |
| --- | --- | --- | --- |
| Indic-Translate | Generation, back-translation to English, injector re-translation | Hosted `/v1/chat/completions` | Explicit `source_language_code` and `target_language_code`; `max_output_tokens` sized to the input; truncated outputs rejected |
| Indic-Speak | Generation of missing audio | Hosted `/v1/audio/speech` | The documented default voice per language; scripts split at 280 characters |
| Indic-Transcribe | Back-transcription and the second segmentation | Hosted `/v1/audio/transcriptions` | Language set explicitly; 28 s chunks cut at low-energy points |

## Cost and runtime

| Per 100 rows (measured on the demo) | Bodhan cost | Judge tokens |
| --- | ---: | --- |
| Score supplied text and audio: back-translation and ASR of about 9.7 minutes of audio | about ₹1.1 | about 87k input / 115k output |
| Score supplied text only (`--no-speech`) | under ₹0.05 | the same |
| Generate translations and speech first (`--generate`) | about ₹7.2 more, mostly Indic-Speak | — |

Spend is capped by `--max-cost-inr` before any request is sent. The judge bills separately. Reasoning models spend most of their tokens on reasoning, so a non-reasoning judge or a provider with prompt caching is cheaper. The three-voice Hindi comparison in [sample_report/voice_comparison.md](sample_report/voice_comparison.md) cost ₹1.34.

Every model response is cached by its exact request. A rerun from the cache produces byte-identical reports with zero requests. The end-to-end test checks this.

## Tests

```sh
python -m pytest tests          # 138 tests, about 20 s, no network
```

The suite covers:

- normalization (digits in all 13 scripts, grouping, scale words)
- span alignment
- every deterministic check, including clean cases that must not flag
- judge parsing
- provider retries and fallbacks
- the injector
- calibration
- baseline matching
- an end-to-end run of the CLI on ten Hindi, Tamil and Bengali rows against simulated Bodhan and judge endpoints, followed by a zero-request cached replay

`BODHAN_LIVE_TEST=1` adds a ten-row run against the hosted APIs, costing up to ₹5. From a clean Cookbook checkout, `make check` runs the repository gate.

## Limitations

- **Subtle meaning errors depend on the judge model.** Mistranslation recall was 89% here. The one miss is a Tamil "equal" changed to "unequal" (சமமற்ற). The back-translation turned it back into "equal", and the judge believed it. That is exactly the checker noise the rubric warns about. Per-language numbers in `calibration.json` show where a judge is weaker.
- **Same-script language confusion** (Hindi vs Marathi, Bengali vs Assamese) is left to the judge, and the judge can be wrong about it. One clean Bengali row was called Assamese.
- **Speech times are estimated.** Hosted ASR returns no word timestamps, so repetition intervals on the audio axis are estimated from the transcript position. The transcript excerpt is attached as the exact anchor. Omissions are located on the script.
- **Noise detection uses the zero-crossing rate.** It catches broadband noise of 1 s or more. Low-level hiss or tonal interference is caught only through the transcript.
- **Thresholds stayed at their defaults in this demo.** Fitting needs 3 positive and 3 negative human-reviewed items per category, and the 6 hand labels (`data/human_labels.jsonl`) are too few. With the panel's labelled subset, or answers from `review_queue.csv`, thresholds move automatically, and `calibration.json` shows the support behind each.
- **Injected errors come from a fixed set of edit types.** Entity renames re-translate a perturbed English source, so Indic-Translate can also change nearby words. The label covers the largest changed region.
- **Not covered:** errors that need world knowledge beyond the source (for example, a wrong but self-consistent fact in the source itself), and a separate prosody or naturalness score for speech.

## AI assistance

This harness was built with AI coding assistants:

- **OpenAI Codex:** the first implementation and its audit.
- **Claude Code:**
  - the detection-quality pass recorded in the [decision log](DECISIONS.md)
  - review against the brief
  - test writing
  - documentation drafts

I reviewed every change, labelled the human review set myself, and can explain each line.
