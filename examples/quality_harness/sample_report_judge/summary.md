# Quality harness summary

This selected 48-item benchmark includes 24 injector-labelled examples; unperturbed examples were
not human-reviewed. Judge-provider charges are separate from the Bodhan cost shown below.

- Judge requested: **yes**; speech requested: **no**. See item details and logs for failures or unavailable checks.
- Status 'clean' means no flag was raised; it is not a human-verified clean label.

- Items scored: **48** (clean 19, flagged 26, uncertain 3, skipped 0)
- Logical model cost (cache-independent): **₹0.0183** (actual spend this run is in meta.json; ₹0 on a cached rerun)
- Checker disagreements (routed to review): 1

## Detection vs targets (on labelled items)

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Recall | 1.00 | 0.80 | ✅ |
| False-alarm rate | n/a | 0.10 | — |
| Localization | 1.00 | 0.70 | ✅ |
| Category accuracy | 1.00 | 0.70 | ✅ |

## Per-language

| Lang | Items | Clean | Flagged | Uncertain | MQM penalty |
| --- | --- | --- | --- | --- | --- |
| bn | 16 | 6 | 9 | 1 | 71.0 |
| hi | 16 | 7 | 8 | 1 | 61.0 |
| ta | 16 | 6 | 9 | 1 | 63.0 |

## Flags by category

| Category | Flags |
| --- | --- |
| addition | 12 |
| format | 2 |
| mistranslation | 2 |
| number_unit | 11 |
| wrong_language_script | 8 |

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
