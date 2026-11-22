# Implementation tasks

## Phase 1: offline foundation
- [x] Read the assignment and Cookbook contribution rules; write PRD, strategy and diagrams.
- [x] Stable CLI, independent item validation and Unicode-aware original-span checks.
- [x] Numbers, terms, script, markup/table/math, reference metrics and PCM16 signal checks.
- [x] Required reports, structured logs, baseline diff and ten-item end-to-end tests.

## Phase 2: model-backed evaluation
- [x] Verify official hosted schemas and all 13 production languages; [sources](api_verification.md).
- [x] Bodhan translation, synthesis, back-translation and chunked ASR adapters.
- [x] Exact-request cache, atomic writes, duplicate coalescing, bounded retries/concurrency and budget reservations.
- [x] Configurable rubric judge with strict schema, exact quotation and span validation.
- [x] Same-prompt judge repeats, speech-triggered repeated ASR and uncertainty review.
- [x] Human-label confidence-threshold fitting and one-to-one metrics per language/category/check.
- [x] Exclude development-parent siblings from held-out metrics; keep synthetic provenance distinct.
- [ ] Validate adapters/rubric against actual Bodhan and judge endpoints (Bodhan credentials now local; execution network access denied; judge settings absent).
- [ ] Collect real human labels, fit thresholds and measure held-out quality and judge consistency.

## Phase 3: evidence and submission readiness
- [x] Package 116 live Bodhan translations and the measured synthetic and paired-ASR results in [the demo report](../sample_report/live_demo/summary.md).
- [x] Run the text-only demo offline with all 116 supplied translations.
- [x] Programmatic text/audio injector with parent lineage, deterministic splits and text/time labels.
- [x] 120 original CC0 English source records across Hindi, Tamil and Bengali with provenance.
- [x] Held-out detection, false-alarm, localization and category metric reporting.
- [x] Offline [sample report](../sample_report/summary.md), clearly labelled as smoke evidence.
- [x] Benchmark command verifies report hashes, zero-network replay and execution cost/runtime.
- [x] Dockerfile, pinned build backend, standard-library runtime and Docker ignore rules.
- [x] Review queue export/import and CI regression gate.
- [x] Prepare [PR draft](pr_description.md) and [five-minute recording plan](demo_recording.md).
- [x] Apply Act and Transform to Phase 3; save the [transformed brief](phase3_act_and_transform.md) and persistent workspace instructions.
- [x] Add whole-item blind annotation with opaque IDs, exact content/audio binding and safe label application.
- [x] Require human-clean parent receipts in the strict injector mode; invalidate labels after generated changes.
- [x] Preserve generation/cache receipts and fix error-level recall independently of item-level recall/category/localization.
- [x] Reject source-only/failed benchmarks; record cold-cache status and assessed populations.
- [x] Add read-only readiness auditing and a real-exit-code local validation runner.
- [x] Document the complete [Phase 3 evidence workflow](phase3_workflow.md).
- [x] Build and inspect a wheel, then install and run it offline in a fresh isolated runtime venv; [direct evidence](installation_validation.md).
- [ ] Generate demo translations/audio with Bodhan; review clean parents and create the labelled injected set.
- [ ] Measure actual held-out quality by category/language, with human annotations and honest target misses.
- [ ] Publish live per-100-item cost/runtime with/without speech and a cold-cache hosted benchmark.
- [ ] Build Docker and pass full Cookbook `make check` in a suitable environment.
- [ ] Create the personalized assignment branch and review the final submission commit.
- [ ] Record/host the <=5-minute live demo and insert its public link.
- [ ] Publish a PR or send submission email only when explicitly requested.

## Local validation

- Harness: **54 tests pass**, including wheel RECORD/source verification, private/generated-file rejection, backend pin validation and the earlier annotation/cache/model/calibration/injection/CI checks. See [captured validation](phase3_validation.md).
- Installed-wheel check passed: pinned setuptools 80.9.0, exact wheel source/RECORD verification, fresh runtime venv with no inherited site packages, isolated imports outside the checkout, nine assessed fixture items, and zero-network byte-identical replay. This uses an installed build backend, not a downloaded fresh build environment, and does not replace Docker or live validation.
- Mocked generation/ASR/judge replay: **zero network calls**, **INR 0 new reservations**, **byte-identical artifacts**. These are engineering checks, not model-quality claims.
- Recipe validation, official-model rules validation and repository lint pass.
- The all-recipe Cookbook validator also fails on the existing AndroidSpeak structure errors. Actual check-runner logs retain those failures; they are not treated as passes.
- Cookbook suite: **30 pass, 2 existing failures** (AndroidSpeak structure and a scaffold test requiring Unix `cp` on Windows).
- GNU make and Docker are unavailable; no full `make check` or container build is claimed.
- Bodhan keys supplied during development were preserved outside the Cookbook checkout in workspace `.env.quality_harness`; `.env.example` was restored to placeholders. Use `--env-file ../../../.env.quality_harness` from the recipe. The Cookbook validator scans local `.env` files even when git-ignored, so external configuration keeps its gate usable. Judge endpoint/model/key and tariffs remain unset.
- A one-item INR-1-capped live smoke run failed before any API response with network access denied (`PermissionError`, WinError 10013), confirmed by an unauthenticated connectivity probe. The failed run reserved INR 0.24576 across three attempts; that is a conservative reservation, not verified billing. Permission failures now stop immediately rather than retrying. No successful hosted response or live-quality result is claimed.

## Submission limits

Arbitrary semantic mistranslation injection requires reviewed term correspondences and is not fabricated. Paragraph omission assumes matching structure and needs review. Unreviewed generated outputs never become automatically clean. The source-only demo and offline smoke report do not satisfy the final generated-and-human-labelled demo requirement.

## Phase 3 continuation

See [readiness evidence](phase3_readiness.md) and [validation summary](phase3_validation.md). On November 22 the unauthenticated connection probe still returned URLError wrapping PermissionError, errno 13 / WinError 10013. Bodhan credentials are present locally, judge settings remain absent, and make/Docker are unavailable. No paid requests were made during this continuation. The local evidence workflow is implemented; live quality, human labels, runtime comparisons and recording remain unverified. The assignment branch still needs the correct GitHub handle.
