# Translation and speech quality harness

Evaluate Bodhan translations and speech from a JSONL file. The harness reports each defect with evidence, measures agreement with labels, and compares two runs. It accepts supplied outputs and can generate missing translations and audio.

## Setup

Use Python 3.12 and install FFmpeg on your PATH. From this recipe directory, install the package with its pinned direct dependencies.

```sh
python -m pip install -e .
```

Copy `.env.example` to `.env`. Set the three model keys from your Bodhan account. Use an OpenAI-compatible Chat Completions service for `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY`. Environment variables take precedence over the local file. Run the commands from this directory when using `.env`. [Bodhan console](https://console.bodhan.ai/) · [Hosted API reference](https://console.bodhan.ai/api-docs/)

To run in Linux with Docker, build the recipe image. Mount the directory containing your data and reports at `/work` on invocation. Pass credentials with `--env-file`.

```sh
docker build -t bodhan-quality .
docker run --rm --env-file .env -v "$(pwd):/work" bodhan-quality --dataset data/demo.jsonl --langs hi,ta,bn --out outputs/pilot --generate --limit 10 --max-cost-inr 5
```

## Run

To evaluate supplied outputs, run the assignment command.

```sh
python -m bodhan_eval --dataset path/to/data.jsonl --langs hi,ta,bn --out outputs/report
```

Write one JSON object per line. Use `id`, `source`, and `lang` on every row. Include `translation`, `reference`, `audio`, or `labels` as available. Resolve a relative audio path from the dataset directory. `--langs` accepts `as,bn,en,gu,hi,kn,ml,mr,or,pa,ta,te,ur`.

- Add `--generate` to populate missing outputs. An empty supplied translation is an omission. With `en` and English source text, generation uses the existing source as the English script.
- Add `--calibration labels.jsonl` to use human labels from another JSONL file. Each record has `id`, `lang`, and `labels`. Use `labels: []` for a reviewed clean row. Omit labels on unreviewed rows. Add `annotator` to attribute human review.
- Label each error with `type`, `severity`, and, when known, `field` and `span`. A span is a half-open `[start, end]` interval. The fields `source`, `translation`, `reference`, and `transcript` use original Unicode code-point offsets. `audio_ms` uses milliseconds. The harness also accepts a bare span from the assignment input. It uses its category for scoring and marks span comparability unknown.
- Set `--max-cost-inr` to cap Bodhan spend. The default is ₹50. The judge provider bills independently. Its token usage appears in the report when provided.
- Reuse `--cache` for a replay. Provide a new cache path when measuring cold runtime. Use `--no-speech` to measure text-only runs.

### Run the pilot

`data/demo.jsonl` contains 216 original English educational cases across Hindi, Tamil, and Bengali. Generate and evaluate the first ten cases with real API calls.

```sh
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out outputs/pilot --generate --limit 10 --max-cost-inr 5
```

Open `outputs/pilot/summary.md` and inspect `items.jsonl` for the translation, audio path, back-transcript, and evidence. A run with absent credentials reports skipped provider checks and can still run eligible local checks.

### Generate and compare the demo

1. Generate at least 108 control rows from the checked-in corpus. Use the pilot to size your available credit before starting the batch.

   ```sh
   python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out outputs/controls --generate --limit 108 --max-cost-inr 50
   python -m bodhan_eval.demo --report outputs/controls --out outputs/demo --max-cost-inr 10
   ```

2. Review a subset of `outputs/demo/review_candidates.jsonl` without the injected labels. Save the human annotations separately, with attribution, and use `--calibration` to include them. Unassigned sources in the external file enter calibration. Assign reviewed test sources to `"split": "holdout"`. All variants of a source share its split.

3. Score the original controls and the injected candidates. The injector preserves the same item IDs and supplied modalities across the comparison.

   ```sh
   python -m bodhan_eval --dataset outputs/demo/comparison_controls.jsonl --langs hi,ta,bn --out outputs/comparison
   python -m bodhan_eval --dataset outputs/demo/demo.jsonl --langs hi,ta,bn --out outputs/scored --baseline outputs/comparison
   ```

4. Re-run the latter command into another output directory with the same cache and options. Compare the four report files and the diff byte for byte. Use `run.jsonl` to confirm zero requests. On a separate run, add `--judge-repeat 30` to measure agreement with fresh judge calls.

5. Prepare a benchmark dataset with at least 200 supplied clips and their scripts. To measure hosted throughput, request `--limit 200`, keep speech enabled, and use a fresh cache. Record `elapsed_seconds`, provider coverage, and dispatch counts. For a text-only comparison, use the same rows with `--no-speech` and another fresh cache.

The checked-in dataset contains source text. A generated audio bundle, human labels, measured demo report, and recording require a live run.

## How it works

Inspect [the architecture, workflow, and quality design](DESIGN.md) for how checks become evidence. See [the decision log](DECISIONS.md) for the choices and validation steps behind the implementation.

| Output | What to inspect |
| --- | --- |
| `summary.md` | Coverage, top findings, quality targets, and estimated cost |
| `summary.json` | Metrics by language and category, invalid rows, and judge token usage |
| `items.jsonl` | Per-item check outcomes, candidate text, back-transcript, evidence spans, and unconfirmed findings |
| `calibration.json` | Thresholds, reviewed support per category, held-out metrics, and per-check precision and recall |
| `diff.md` | New, resolved, persistent, and severity changes for comparable rows when you pass `--baseline` |
| `run.jsonl` | Actual requests, cache hits, error codes, timing, and reserved or spent Bodhan credit |

Run the recipe tests with `python -m pytest tests` from the recipe directory after installing the dev dependencies. The tests measure real local audio signals. To run the ten-item test with hosted models, set `BODHAN_LIVE_TEST=1` and provide the credentials first. This test can spend up to ₹5 on Bodhan requests plus judge usage. Run `make check` from the Cookbook root.
