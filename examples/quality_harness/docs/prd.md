# Translation and speech quality harness PRD

## Problem and users
Model engineers need reproducible, localized evidence of translation and speech errors on unseen multilingual JSONL datasets. Scores alone cannot explain failures. The assignment brief dated November 13, 2026 is the source of requirements.

## Required behavior
Accept the exact `python -m bodhan_eval --dataset ... --langs hi,ta,bn --out ...` interface, including optional generation, baseline, calibration and budget flags. Require only id, English source and language per item. Missing outputs are unassessed unless generation is requested; malformed items must not stop other items.

Evaluate supplied translations using references when available, normalized number preservation, protected terms, format integrity, language/script checks and Bodhan back-translation. Evaluate audio with signal checks and Bodhan transcription; separate uncertain checker findings from confirmed system errors. An endpoint-independent LLM rubric must return categories, severities and exact spans without producing or correcting Indic text.

Use only Bodhan for translation, TTS and ASR. Read separate model keys and configurable LLM settings from the specified environment variables. Cache exact model inputs, limit concurrency, retry transient failures and reserve cost before paid requests. Never commit secrets or generated audio.

Produce summary.md, summary.json, items.jsonl and calibration.json; add diff.md with a baseline. Include language/category breakdowns, check coverage, errors, estimated costs and actionable examples. Distinguish unknown quality from clean outputs.

## Acceptance targets
On a held-out injected set: recall >=80%, clean false alarm rate <=10%, localization overlap >=70%, category accuracy >=70%, repeated judge agreement >=90%. Cached reruns must produce identical reports and incur no model cost. Target 200 items with speech in <30 minutes. These are measured targets, never assumed results.

## Delivery requirements
Self-contained Cookbook recipe, pinned setup, unit tests for deterministic checks and spans, ten-item end-to-end test, >=100 demo items across Hindi/Tamil/Bengali, documented provenance, sample report, architecture and flow diagrams, human calibration and <=5 minute public demo video. Follow Cookbook validation before a submission PR.

## Scope and release stages
First deliver an offline deterministic foundation with honest coverage and tests. Then add cached Bodhan adapters, judge and checker corroboration. Finally calibrate on independent labels, generate the licensed demo with Bodhan, benchmark and prepare submission. Publishing a PR or sending email is outside this implementation request.

## Risks
Shared scripts do not prove language identity. Different numeric formatting and translated units can cause false positives. ASR/back-translation disagreement is evidence of uncertainty, not proof. Human labels must identify annotator provenance; injected labels are not human labels. Live performance and costs cannot be validated without credentials and actual calls.
