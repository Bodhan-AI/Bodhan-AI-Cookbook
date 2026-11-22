# Five-minute demo recording plan

Record only after live generation and human review. No public video or live results exist yet.

1. 0:00–0:30: State the dataset provenance, language coverage, human-label origins and model settings. Show environment variable names without displaying values.
2. 0:30–1:30: Run the exact CLI against the prepared 120+ record dataset with a deliberate speech sample and INR budget. Explain the deterministic checks and Bodhan-only model calls.
3. 1:30–2:30: Open summary.md and items.jsonl. Show a localized number/markup defect, a semantic judge finding, a speech example, and a checker disagreement in review.csv.
4. 2:30–3:30: Open calibration.json. Explain one-to-one matching, threshold selection and exclusion of all calibration-parent siblings. State measured misses without hiding unsupported categories.
5. 3:30–4:30: Run the cache benchmark and a baseline comparison. Show zero network attempts, zero new budget reservation, identical artifact hashes and one actionable changed finding.
6. 4:30–5:00: State the actual model versions, cold-run runtime/cost and remaining limitations. Host the video at a public link that opens without sign-in; insert that link in README and the PR draft.
