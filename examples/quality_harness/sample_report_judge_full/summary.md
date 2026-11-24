# Quality harness summary

- Judge requested: **yes**; speech requested: **no**. See item details and logs for failures or unavailable checks.
- Status 'clean' means no flag was raised; it is not a human-verified clean label.

- Costs and the configured budget cover Bodhan calls only; judge-provider charges are separate and are not included.

- Judge usage: 108 calls, 81390 input tokens, 38504 output tokens (0 calls without usage data).
- Items scored: **108** (clean 41, flagged 57, uncertain 10, skipped 0)
- Logical model cost (cache-independent): **₹0.038** (actual spend this run is in meta.json; ₹0 on a cached rerun)
- Checker disagreements (routed to review): 1

## Detection vs targets (on labelled items)

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Recall | 0.98 | 0.80 | ✅ |
| False-alarm rate | n/a | 0.10 | — |
| Localization | 0.98 | 0.70 | ✅ |
| Category accuracy | 0.98 | 0.70 | ✅ |

## Per-language

| Lang | Items | Clean | Flagged | Uncertain | MQM penalty |
| --- | --- | --- | --- | --- | --- |
| bn | 36 | 12 | 19 | 5 | 142.0 |
| hi | 36 | 15 | 18 | 3 | 138.0 |
| ta | 36 | 14 | 20 | 2 | 175.0 |

## Flags by category

| Category | Flags |
| --- | --- |
| addition | 19 |
| format | 9 |
| mistranslation | 3 |
| number_unit | 26 |
| omission | 3 |
| term_entity | 1 |
| wrong_language_script | 19 |

## Top failures (with evidence)

- **src-007** [ta] — `wrong_language_script`/critical (conf 0.95), span (0, 50). reason=translation identical to source (untranslated)
- **src-008** [bn] — `wrong_language_script`/critical (conf 0.95), span (0, 41). reason=translation identical to source (untranslated)
- **src-019** [ta] — `wrong_language_script`/critical (conf 0.95), span (0, 49). reason=translation identical to source (untranslated)
- **src-020** [bn] — `wrong_language_script`/critical (conf 0.95), span (0, 57). reason=translation identical to source (untranslated)
- **src-021** [hi] — `wrong_language_script`/critical (conf 0.95), span (0, 33). reason=translation identical to source (untranslated)

## Limitations

- Back-translation and ASR are noisy; lone noisy signals are routed to the review queue, not flagged, which trades recall for precision.
- Number-word (spelled-out) quantities are handled only conservatively.
- Low-support categories/languages fall back to pooled thresholds (see calibration.json).
