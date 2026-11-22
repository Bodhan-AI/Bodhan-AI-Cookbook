# Phase 3: from generated outputs to reviewable evidence

This workflow implements the [Act and Transform brief](phase3_act_and_transform.md). Run commands from the recipe directory. It preserves the assignment CLI and does not publish anything. The [current readiness audit](phase3_readiness.md) distinguishes implemented tooling from missing live evidence.

## 1. Validate locally

```bash
python -m bodhan_eval.local_checks --out outputs/checks
```

Use a fresh output directory each time. `checks.json` records source hashes, real exit codes, log hashes and tool availability. Exit 2 means a failed/unavailable check remains. Direct upstream checks do not count as a full `make check` or Docker build. In a suitable environment, explicitly run the install-bearing upstream target and container build with:

```bash
python -m bodhan_eval.local_checks --out outputs/checks-container --with-make --with-docker
```

Current blockers include the existing AndroidSpeak structure failures and Unix `cp` requirement in an upstream test. They are not repaired by changing this recipe or marked as passed.

## 2. Configure and pilot live calls

Before live validation, the optional offline packaging check is `python -m bodhan_eval.installation --out outputs/installation-check --env-file ../../../.env.quality_harness`. It requires the pinned setuptools 80.9.0 in the build interpreter, builds without indexes/dependency downloads, verifies the wheel, then installs and smoke-tests it in a fresh isolated runtime venv. It does not replace a Docker build or prove backend downloads from a clean clone.

Keep keys outside the checkout. In this workspace, use `--env-file ../../../.env.quality_harness`. Configure all three Bodhan keys plus `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`. The judge URL includes the provider's `/v1` prefix. Obtain the actual input/output INR prices from that provider; do not assume a free judge.

Replace the two genuine price placeholders below before running. These commands must run where networking to the endpoints is permitted. The current execution environment denies connections; no live success has been observed here.

```bash
python -m bodhan_eval --dataset data/live_smoke.jsonl --langs hi,ta,bn --out outputs/live-pilot --generate --max-cost-inr 2 --cache-dir .cache/live-pilot --env-file ../../../.env.quality_harness --llm-input-inr-per-million <INPUT_INR_PER_MILLION> --llm-output-inr-per-million <OUTPUT_INR_PER_MILLION>
```

Inspect all three items and check coverage before scaling. A completed process with failed generation, missing judge or unassessed items is not a successful pilot. Reservations are conservative estimates, not verified invoices. Adapt the budget from actual pilot evidence; the harness must not exceed its configured reservation cap.

## 3. Generate the 120-source demo

```bash
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out outputs/generated --generate --speech-sample-rate 1 --max-cost-inr 50 --cache-dir .cache/demo-generation --env-file ../../../.env.quality_harness --llm-input-inr-per-million <INPUT_INR_PER_MILLION> --llm-output-inr-per-million <OUTPUT_INR_PER_MILLION>
```

`outputs.jsonl` preserves Bodhan request receipts with exact input/output hashes and cache identities. Successful cache replay retains those receipts. They reconcile local recorded requests; they are not remote provider attestations. Audio remains local and git-ignored. Partial output after budget exhaustion needs further generation/review, not a clean label.

## 4. Review parents blindly, including clean negatives

```bash
python -m bodhan_eval.annotations export --dataset outputs/generated/outputs.jsonl --out outputs/parent-review.csv
```

Every supplied output is included, rather than selecting only flagged examples. Review IDs and parent IDs are opaque and rows are ordered by opaque identity, so mutation names and model verdicts do not disclose expected answers. Original IDs are restored on import. Leave identity/hash/display columns unchanged.

A real reviewer reads source and translation and listens to available audio. Set `decision` to `clean`, `errors` or `defer`; leave undecided rows blank. Record `annotator`. For `errors`, enter a nonempty JSON array in `labels_json`: each label needs type, severity and exactly one original text span or actual audio time interval. Allowed text fields are source, translation and reference. Speech omission can point to intended translation; sound defects can use seconds. Checker transcripts are not human ground truth, and changes to an ASR transcript alone do not invalidate unchanged system text/audio.

```bash
python -m bodhan_eval.annotations import --dataset outputs/generated/outputs.jsonl --csv outputs/parent-review.csv --out outputs/parent-labels.jsonl
python -m bodhan_eval.annotations apply --dataset outputs/generated/outputs.jsonl --labels outputs/parent-labels.jsonl --out outputs/clean.jsonl --clean-only
```

Blank/deferred items remain unlabelled. Contradictory decisions, invented spans, duplicate IDs and stale text/audio hashes are rejected before output is written. Regeneration that changes reviewed system outputs invalidates their human labels; review the new outputs again.

## 5. Inject, then independently review a subset

```bash
python -m bodhan_eval.inject --dataset outputs/clean.jsonl --out outputs/injected.jsonl --require-human-clean
python -m bodhan_eval.annotations export --dataset outputs/injected.jsonl --out outputs/injected-review.csv
```

All language siblings and mutations share the same parent split. A mutation does not inherit its parent's clean review hash. The injector never writes Indic prose with an LLM. Its synthetic labels remain distinct from human confirmations. Review clean negatives and positive examples from both splits; do not copy seeded answers and call them human review.

```bash
python -m bodhan_eval.annotations import --dataset outputs/injected.jsonl --csv outputs/injected-review.csv --out outputs/injected-human.jsonl
python -m bodhan_eval.annotations apply --dataset outputs/injected.jsonl --labels outputs/injected-human.jsonl --out outputs/reviewed.jsonl
```

The apply step preserves unreviewed synthetic labels and existing clean-parent reviews. Human-checked variants can have their own exact localization. Calibration uses only attributable human development labels; all siblings of development parents are excluded from held-out evaluation. Obtain sufficient human positives and clean negatives in both calibration and held-out groups before interpreting fitted thresholds.

## 6. Evaluate and measure

```bash
python -m bodhan_eval --dataset outputs/reviewed.jsonl --langs hi,ta,bn --out outputs/evaluation --cache-dir .cache/demo-evaluation --max-cost-inr 50 --env-file ../../../.env.quality_harness --llm-input-inr-per-million <INPUT_INR_PER_MILLION> --llm-output-inr-per-million <OUTPUT_INR_PER_MILLION>
python -m bodhan_eval.benchmark --dataset outputs/reviewed.jsonl --langs hi,ta,bn --out outputs/live-benchmark --cache-dir .cache/cold-benchmark -- --max-cost-inr 50 --env-file ../../../.env.quality_harness --llm-input-inr-per-million <INPUT_INR_PER_MILLION> --llm-output-inr-per-million <OUTPUT_INR_PER_MILLION>
```

The benchmark checks assessed population, required checker coverage, frozen input hashes, two report hash sets and zero replay network/reservations. An empty/failed run cannot pass through matching hashes alone. A warm initial cache cannot establish cold timing. Use fresh directories. Quality targets remain targets, not pass/fail gates; show misses, coverage gaps and sample counts.

Detection recall now counts distinct errors, separately from item-level detection. Category and localization metrics are separate. `calibration.json` separates human and injected evaluation groups. A single flag on a two-error item cannot claim 100% error recall. Human annotations with no positive or no clean support leave the corresponding metrics unsupported.

For cost/runtime comparisons, use the same source population with and without synthesis/speech checks, separate fresh caches, and report what outputs were supplied versus generated. Sampling synthesis does not disable checking supplied audio. Normalize first-run observed time and retry-inclusive reservations as `value / items * 100`; identify reservations as estimates. Measure the 200-item hosted runtime target directly, not by extrapolating the nine-record offline fixture.

## 7. Audit the evidence and prepare the recording

```bash
python -m bodhan_eval.readiness --dataset outputs/reviewed.jsonl --report outputs/evaluation --benchmark outputs/live-benchmark --validation outputs/checks-container/checks.json --cache-dir .cache/demo-generation --out outputs/readiness
```

Exit 2 means required evidence is missing or inconsistent. The audit reconciles original generation receipts against the generation cache, reproduces injected changes, checks parent isolation and human negatives, matches dataset/report contents, checks implementation hashes and benchmark coverage, and reconciles validation logs. The audit does not prove remote provider authenticity, human judgment correctness or submission authorization.

Record the actual run/report following [demo_recording.md](demo_recording.md). A public URL alone is unverified. After independent verification, `--video-evidence path.json` accepts a manually authored attestation containing `url`, `dataset_sha256`, `reviewer`, `verified_at`, `duration_seconds` (0 < duration <= 300), `public_access_verified: true` and `reviewed_full_run_and_report: true`. This is explicitly a human attestation, not an automated media/network verification; do not fill it in before checking the recording. Insert the actual link in README and the PR description.

Create `assignment/quality-harness-<github-handle>` once the correct handle is established. Review the exact final commit, run final validation and use the assignment PR template. Do not publish, email or host artifacts merely because a readiness check passes.
