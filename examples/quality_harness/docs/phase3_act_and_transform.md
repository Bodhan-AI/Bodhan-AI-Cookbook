# Act and Transform

Applied November 18, 2026. This is the working brief for Phase 3, not a claim that the submission is ready.

## 1. What a top professional would do

Essential: demonstrate a real, reproducible multilingual run; preserve exact dataset/model provenance; obtain independently attributable labels for clean negatives as well as errors; separate calibration parents from evaluation; audit missing/failed check coverage before claiming accuracy or replay success. Bind human answers to the output actually reviewed, so regenerated text/audio cannot silently inherit stale labels. Distinguish engineering fixtures, injected ground truth and human judgments. Treat unmeasured targets as unmeasured.

Optional: a visual dashboard or voice comparison can wait. They cannot replace the >=100-item Bodhan-generated demo, representative annotation, live cost/runtime measurements, passing validation and <=5-minute public walkthrough.

## 2. Primary material to start from

- Workspace assignment Markdown: exact CLI, submission artifacts, model restrictions and quality targets.
- Cookbook CONTRIBUTING.md, Makefile, model rules and validator source: actual packaging/security gates.
- src/bodhan_eval, tests, raw JSONL, sample_report and cache execution records: implementation and direct evidence, rather than checkboxes alone.
- Private environment: inspect configuration presence only, never expose credentials.
- Direct unauthenticated connection probe on November 18: URLError wrapping PermissionError (errno 13 / WinError 10013). No successful live request was observed. Judge settings remain absent. Docker/make are unavailable locally.

Official API schemas were verified on November 17 and linked in api_verification.md. No new API behavior is inferred from a failed connection. Human annotations, live generated outputs, a public recording and clean-container results remain access/evidence gaps.

## 3. Methods and tools

Preserve the existing standard-library CLI and model adapters. Add a blind, whole-item annotation CSV with exact content/audio hashes; validate decisions and annotator identity on import. Use one-to-one error metrics and parent-group exclusions, with explicit denominators. Harden replay benchmarking so hashes cannot certify a run with no assessed outputs or failed required checks. Add a read-only submission audit to verify dataset coverage, model receipts, report/dataset correspondence, benchmark evidence and required deliverables. A local check runner should capture real exit codes and logs, recording unavailable tools rather than inventing passes.

All these are local reversible implementation steps already authorized by the user's Phase 3 request. Paid generation remains bounded by the harness budget and depends on working connectivity/configuration. Publication, email and video hosting are not implied by a readiness audit.

## 4. Independent validation

Tests must reject stale annotations, duplicate/conflicting decisions, unreviewed parents labelled clean, source-only demos passed as generated demos, mismatched report identities and an empty/failed benchmark passed as complete. Include positive fixture cases as engineering tests, explicitly not live-quality evidence. Run existing tests and recipe/model/lint checks; record the existing repository failures. Compare two report directories byte-for-byte and require zero replay network attempts/reservations, separately from complete live check coverage.

Real quality targets remain recall >=80%, clean false alarms <=10%, localization >=70%, category accuracy >=70%, repeated judge agreement >=90%, and 200 items with speech in <30 minutes. Report sample counts and provenance. A source-only demo, missing judge, failed network request or unexecuted Docker build falsifies submission readiness regardless of report polish. Missing a quality target is disclosed, not hidden or treated as an assignment pass/fail rule.

## 5. Transformed prompt

```text
Continue Phase 3 of the Bodhan translation and speech quality harness under
Bodhan-AI-Cookbook/examples/quality_harness. First inspect the original assignment,
Cookbook contribution/validation code, current implementation/tests, JSONL datasets,
report artifacts and execution logs. Preserve the required CLI and working adapters.

Make submission evidence trustworthy: implement whole-item blind annotation export
and import bound to exact text/audio hashes; reject stale, duplicated or contradictory
answers and retain annotator provenance. Include clean negatives without selecting
only model-flagged items. Keep calibration/evaluation siblings separated by parent.
Audit error-level metric denominators and distinguish item-level detection from
per-error detection; never hide missing coverage behind a score.

Harden cached benchmarks to distinguish byte-identical zero-cost replay from a run
that actually assessed supplied/generated outputs. Add a read-only readiness audit
for >=100 multilingual demo outputs, Bodhan provenance, human review, injected cases,
dataset/report alignment, live coverage, benchmark evidence and submission artifacts.
Capture actual local check exit codes/logs and mark unavailable Docker/make explicitly.

Use only Bodhan for translation, TTS and ASR. The judge may classify and quote exact
existing Indic text but never compose corrections. Keep secrets outside the checkout,
audio out of git and every paid request within configured reservations. Do not bypass
network restrictions, fabricate model outputs/human labels/live benchmarks, or publish
a PR/email/video without authorization. When a prerequisite is unavailable, implement
and validate all independent local work and document the exact remaining dependency.

Validate with focused negative tests, the existing suite, recipe/model/lint checks,
parent-group leakage checks and report hashes. Generate a real demo and report quality
targets only when endpoints and actual human labels are available. Save the transformed
brief, evidence/audit JSON and Markdown, reproducible commands, and updated task status.
Summarize implemented changes, actual validation, limitations and remaining inputs.
```

## Next step: clean installation evidence

The remaining setup claim needs an installed-package test, not another source-tree test. Primary evidence: pyproject.toml, installed backend metadata, the actual built wheel/RECORD manifest, fresh-venv import paths, and CLI/report artifacts. At inspection the pin was setuptools 75.6.0, while the available installed backend was 80.9.0 and no local backend wheel was cached. The pin was updated to the exercised 80.9.0 backend; the installed-wheel check subsequently passed. This does not imply an isolated network build was performed.

Stage only package source and required build metadata. Build with indexes and dependency resolution disabled, inspect wheel paths/RECORD hashes and private-key exclusion, create a fresh venv without inherited site packages, install the wheel offline, and run the exact CLI with isolated Python imports from a directory outside the source checkout. Verify populated reports and a zero-network cached rerun. Reject backend mismatch, unexpected package files, corrupted RECORD entries, source-tree imports or unavailable prerequisites. Preserve logs/hashes and distinguish this evidence from Docker, full upstream checks and live quality.

```text
Continue Phase 3 by proving offline wheel installation into a clean virtual
environment. Inspect the actual build configuration and installed backend first.
Keep the CLI unchanged. Pin and record the backend used, stage only explicit
package source/build inputs, build without indexes/dependency downloads, and
validate wheel members and RECORD checksums. Reject private configuration and
generated artifacts. Create a fresh venv with no inherited site packages, install
that wheel offline, and run the CLI with isolated imports outside the checkout.
Verify the installed module path, complete deterministic fixture coverage,
report hashes and zero-cost replay. Save reproducible commands and direct logs.
Do not claim a network-isolated build-environment download, Docker validation or
live model quality. Preserve the existing blockers and never expose credentials.
```
