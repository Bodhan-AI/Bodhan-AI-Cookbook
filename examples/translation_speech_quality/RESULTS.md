# Results — 18 November 2026

The harness completed all requested checks for 108 supplied translations, including Bodhan back-translation and a Gemini judge, with six generated speech samples across Hindi, Tamil and Bengali. These results are development measurements on controlled injections, not independent human validation.

**The main remaining quality problem is reference-free false alarms.** Do not infer production reliability from the reference-assisted numbers.

## Measurements

| Measurement | Reference supplied | References removed | Target |
|---|---:|---:|---:|
| Seeded-error detection | 100.0% | 100.0% | ≥80% |
| Assumed-clean item false alarms | 0.0% | 46.3% | ≤10% |
| Span overlap | 75.9% | 75.9% | ≥70% |
| Category accuracy among detections | 98.1% | 98.1% | ≥70% |
| Independent judge repeats agreeing | 100.0% on six items | 100.0% on six items | ≥90% |

The same model responses are used in both settings. References are not sent to the judge. Removing them removes reference-based arbitration and reference metrics, without buying new model calls. The 46.3% reference-free false-alarm result misses the target substantially. Many additional flags are semantic disagreements from the judge; back-translation also makes mistakes. Human-labelled calibration and a more reliable semantic judge should be evaluated before treating those findings as confirmed errors.

The reference-assisted setting defers findings that contradict an exactly matching reference to review. Since clean controls are themselves copies of those references, its 0% false-alarm result is deliberately easy and cannot measure independent translation quality. In addition, labels record known injections, not every possible natural defect in the original translations. Extra-category flags are counted as false positives against that incomplete controlled truth.

## Per-category controlled measurements

| Category | Reference-assisted precision | Recall | Reference-free precision | Recall |
|---|---:|---:|---:|---:|
| addition | 90.0% | 100.0% | 90.0% | 100.0% |
| entity | 100.0% | 100.0% | 100.0% | 100.0% |
| format | 75.0% | 100.0% | 60.0% | 100.0% |
| garbled_audio | unmeasured | unmeasured | unmeasured | unmeasured |
| mistranslation | 0.0% | unmeasured | 0.0% | unmeasured |
| number | 100.0% | 100.0% | 81.8% | 100.0% |
| omission | 72.7% | 88.9% | 61.5% | 88.9% |
| speech_omission | unmeasured | unmeasured | unmeasured | unmeasured |
| speech_repetition | unmeasured | unmeasured | unmeasured | unmeasured |
| wrong_language | 100.0% | 100.0% | 100.0% | 100.0% |

There are nine seeded cases per injected category. There are no seeded mistranslation or speech-error positives; recall for those categories is unmeasured. A known omission case was detected under another category, so item detection is higher than omission-category recall. Broad format spans can overlap a seed without pinpointing it, and omission source labels are approximate. Both limitations weaken the localization headline.

## Per-language results

| Language | Items | Detection recall | Reference-assisted false alarms | Reference-free false alarms |
|---|---:|---:|---:|---:|
| bn | 36 | 100.0% | 0.0% | 44.4% |
| hi | 36 | 100.0% | 0.0% | 55.6% |
| ta | 36 | 100.0% | 0.0% | 38.9% |

## Reports and reproducibility

- [Reference-assisted sample report](sample_report/final/summary.md), [dashboard](sample_report/final/dashboard.html), [calibration output](sample_report/final/calibration.json).
- [Reference-free report](sample_report/reference-free/summary.md) and [dashboard](sample_report/reference-free/dashboard.html).
- [Clean versus injected deterministic findings](sample_report/comparison-injected/diff.md). Both sides use the same deterministic checks. This illustrates the diff interface, not a model-quality improvement.
- [Cached replay verification](sample_report/cache-verification.json): six assessment artifacts are byte-identical, with 235 cache hits and zero API calls. Invocation cost and latency are intentionally separate.

The labelled subset is programmatic. There are **zero newly human-reviewed quality annotations**. Human threshold fitting, grouped holdout evaluation and per-check precision/recall are implemented and covered by tests, but no calibrated-human performance is claimed here. `review.csv` contains every item, including uncertain and conflicting evidence, for actual annotation.

## Tested models and API behavior

- Hosted Bodhan `indic-translate`, using `/v1/chat/completions`, `target_language_code`, bounded output tokens and no sampled generation parameters.
- Hosted Bodhan `indic-speak`, named production voices and language in `instructions`; generated WAVs carry an AI-generated label.
- Hosted Bodhan `indic-transcribe`, explicit language and 28-second audio windows.
- Google `gemini-3.1-flash-lite` at its OpenAI-compatible endpoint; temperature 0, 1,500-token output cap. The model only judges and quotes supplied text; it never generates translations.

On 18 November, the documented `/translate` path returned HTTP 404 during discovery, while the verified `/v1/chat/completions` route worked. Both Bodhan translation calls and Gemini judge calls encountered HTTP 429 during the first run. Bounded retries and cached continuation completed the dataset. The initial run's incomplete results are retained locally for audit and are not presented as the final run.

Six generated speech samples total approximately 80 seconds. Their observed durations were 9.216, 12.544, 25.344, 8.448, 14.080 and 10.581 seconds. A separate missing-output smoke test successfully generated a Hindi translation and speech, back-translated it and transcribed its audio. The 108-item dataset tests supplied outputs; the smoke test verifies generation.

## Cost and runtime

Recorded live runs and the generation smoke test consumed **₹9.020591 in estimated charges or retained failure reservations**, including the separately priced judge. This is a conservative ledger, not a provider invoice; preliminary discovery probes are outside those run ledgers. The run budgets were ₹7, ₹2.70 and ₹0.20. No top-up was made.

| Operation | Estimated charges or retained reservations |
|---|---:|
| judge_0 | ₹7.320625 |
| judge_1 | ₹0.173875 |
| speak | ₹0.567600 |
| transcribe | ₹0.141511 |
| translate | ₹0.816980 |

The two live passes took 257.3 and 173.1 seconds respectively, including retries and cached work. Together, that is about 6.64 minutes per 100 items for this development run. A final cache replay took about 0.2 seconds. These measurements do not establish the target runtime on 200 unseen items.

Unique successful responses in the final cached trace represent ₹4.375209, or about ₹4.051 per 100 items including the sampled speech checks. Excluding synthesis and transcription gives about ₹3.430 per 100; judge inputs in that estimate still contain available ASR evidence, so it is not an independently measured no-speech run. This excludes failed-call reservations. To reproduce the estimate, sum unique `original_cost_inr` values in the final cached trace. Speech synthesis and transcription costs can be separated from translation and judging there. A clean cold run without speech has not been timed, so no measured no-speech runtime is claimed. Prices used for Gemini were conservative INR estimates of 25 input and 150 output per million tokens; actual free-tier or invoice charges may differ.

## Demo and validation status

A captioned local recording of the complete cached run and report walkthrough is prepared with the review bundle. It is not publicly hosted yet. The README and PR need its public, sign-in-free link after applicant review. Generated audio is excluded from git and the sample report's audio paths refer to private local artifacts.

The recipe's 40 tests pass. Cookbook-wide `make check`, a clean installation, the local demo recording and the final secret scan are recorded in the applicant briefing when completed. No PR or submission email has been sent.

## Next quality work

1. Obtain actual human-reviewed Hindi/Tamil/Bengali labels, with enough positive and negative examples per category and independent source groups.
2. Fit on the training groups and report untouched holdout results; do not tune against injected demo labels.
3. Compare a stronger semantic judge on reference-free material and inspect each false alarm, including checker-induced errors.
4. Add seeded speech omissions, repetitions and noisy audio before claiming speech detection precision or recall.
5. Replace whole-output format spans and approximate omission labels with tighter localization.
