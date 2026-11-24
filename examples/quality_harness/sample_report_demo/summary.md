# Quality harness summary

- Judge requested: **no**; speech requested: **no**. See item details and logs for failures or unavailable checks.
- Status 'clean' means no flag was raised; it is not a human-verified clean label.

- Items scored: **108** (clean 43, flagged 47, uncertain 18, skipped 0)
- Logical model cost (cache-independent): **₹0.038** (actual spend this run is in meta.json; ₹0 on a cached rerun)
- Checker disagreements (routed to review): 0

## Detection vs targets (on labelled items)

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Recall | 0.83 | 0.80 | ✅ |
| False-alarm rate | n/a | 0.10 | — |
| Localization | 0.71 | 0.70 | ✅ |
| Category accuracy | 0.71 | 0.70 | ✅ |

## Per-language

| Lang | Items | Clean | Flagged | Uncertain | MQM penalty |
| --- | --- | --- | --- | --- | --- |
| bn | 36 | 13 | 17 | 6 | 125.0 |
| hi | 36 | 16 | 14 | 6 | 115.0 |
| ta | 36 | 14 | 16 | 6 | 132.0 |

## Flags by category

| Category | Flags |
| --- | --- |
| addition | 2 |
| format | 9 |
| number_unit | 26 |
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
