# Quality harness summary

This cached rerun covers three development audio faults. A segmented-transcription fallback
recovered usable transcripts and produced one final flag; two cases remain uncertain. These cases
were used to develop the fallback, so they are not an independent benchmark.

- Judge requested: **no**; speech requested: **yes**. See item details and logs for failures or unavailable checks.
- Status 'clean' means no flag was raised; it is not a human-verified clean label.

- Costs and the configured budget cover Bodhan calls only; judge-provider charges are separate and are not included.

- Items scored: **3** (clean 0, flagged 1, uncertain 2, skipped 0)
- Logical model cost (cache-independent): **₹0.0658** (actual spend this run is in meta.json; ₹0 on a cached rerun)
- Checker disagreements (routed to review): 0

## Detection vs targets (on labelled items)

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Recall | 0.33 | 0.80 | ❌ |
| False-alarm rate | n/a | 0.10 | — |
| Localization | n/a | 0.70 | — |
| Category accuracy | 1.00 | 0.70 | ✅ |

## Per-language

| Lang | Items | Clean | Flagged | Uncertain | MQM penalty |
| --- | --- | --- | --- | --- | --- |
| bn | 1 | 0 | 1 | 0 | 5.0 |
| hi | 1 | 0 | 0 | 1 | 0.0 |
| ta | 1 | 0 | 0 | 1 | 0.0 |

## Flags by category

| Category | Flags |
| --- | --- |
| speech_garbled | 1 |

## Top failures (with evidence)

- **src-005** [bn] — `speech_garbled`/major (conf 0.62), span None. max_internal_silence_s=4.0, threshold=1.5

## Limitations

- Back-translation and ASR are noisy; lone noisy signals are routed to the review queue, not flagged, which trades recall for precision.
- Number-word (spelled-out) quantities are handled only conservatively.
- Low-support categories/languages fall back to pooled thresholds (see calibration.json).
