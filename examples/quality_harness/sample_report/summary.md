# Quality harness summary

- Items scored: **10** (clean 5, flagged 4, uncertain 1, skipped 0)
- Logical model cost (cache-independent): **₹0.0018** (actual spend this run is in meta.json; ₹0 on a cached rerun)
- Checker disagreements (routed to review): 1
- Judge consistency: verdict 100%, category 95% (target ≥ 90%)

## Detection vs targets (on labelled items)

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Recall | 0.75 | 0.80 | ❌ |
| False-alarm rate | 0.00 | 0.10 | ✅ |
| Localization | 1.00 | 0.70 | ✅ |
| Category accuracy | 1.00 | 0.70 | ✅ |

## Per-language

| Lang | Items | Clean | Flagged | Uncertain | MQM penalty |
| --- | --- | --- | --- | --- | --- |
| bn | 3 | 1 | 1 | 1 | 10.0 |
| hi | 4 | 3 | 1 | 0 | 5.0 |
| ta | 3 | 1 | 2 | 0 | 15.0 |

## Flags by category

| Category | Flags |
| --- | --- |
| addition | 1 |
| format | 1 |
| number_unit | 2 |
| wrong_language_script | 1 |

## Top failures (with evidence)

- **t06** [bn] — `wrong_language_script`/critical (conf 0.95), span (0, 33). reason=translation identical to source (untranslated)
- **t02** [hi] — `number_unit`/major (conf 0.95), span (16, 18). issue=missing_or_changed, value=100, unit=mg
- **t04** [ta] — `format`/major (conf 0.95), span (0, 29). issue=unbalanced_markup, dollar=1, braces=(0, 0)
- **t08** [ta] — `addition`/major (conf 0.85), span (26, 49). explanation=The translation adds information about the tower's width (99 metres) that is not present in the source sentence., located=True, verdict=error
- **t08** [ta] — `number_unit`/major (conf 0.78), span (33, 35). issue=extra_in_translation, value=99, unit=None

## Limitations

- Back-translation and ASR are noisy; lone noisy signals are routed to the review queue, not flagged, which trades recall for precision.
- Number-word (spelled-out) quantities are handled only conservatively.
- Low-support categories/languages fall back to pooled thresholds (see calibration.json).
