# Quality harness summary

These are three programmatically altered audio cases, not a representative speech benchmark.
The recognizer returned degenerate character loops for all three cases; the harness routed them
to review rather than treating those transcripts as reliable evidence.

- Judge requested: **no**; speech requested: **yes**. See item details and logs for failures or unavailable checks.
- Status 'clean' means no flag was raised; it is not a human-verified clean label.

- Items scored: **3** (clean 0, flagged 0, uncertain 3, skipped 0)
- Logical model cost (cache-independent): **₹0.0333** (actual spend this run is in meta.json; ₹0 on a cached rerun)
- Checker disagreements (routed to review): 0

## Detection vs targets (on labelled items)

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Recall | 0.00 | 0.80 | ❌ |
| False-alarm rate | n/a | 0.10 | — |
| Localization | n/a | 0.70 | — |
| Category accuracy | n/a | 0.70 | — |

## Per-language

| Lang | Items | Clean | Flagged | Uncertain | MQM penalty |
| --- | --- | --- | --- | --- | --- |
| bn | 1 | 0 | 0 | 1 | 0.0 |
| hi | 1 | 0 | 0 | 1 | 0.0 |
| ta | 1 | 0 | 0 | 1 | 0.0 |

## Flags by category

_No flags raised._

## Top failures (with evidence)

_No flagged items._

## Limitations

- Back-translation and ASR are noisy; lone noisy signals are routed to the review queue, not flagged, which trades recall for precision.
- Number-word (spelled-out) quantities are handled only conservatively.
- Low-support categories/languages fall back to pooled thresholds (see calibration.json).
