# Quality harness summary

These three Marathi faults were used while developing the current checks, so this is not an
independent estimate of speech quality. The omitted and digitally repeated cases were flagged;
the long-silence case remained uncertain. Original audio is not included in the repository.

- Judge requested: **no**; speech requested: **yes**. See item details and logs for failures or unavailable checks.
- Status 'clean' means no flag was raised; it is not a human-verified clean label.

- Costs and the configured budget cover Bodhan calls only; judge-provider charges are separate and are not included.

- Items scored: **3** (clean 0, flagged 2, uncertain 1, skipped 0)
- Logical model cost (cache-independent): **₹0.039** (actual spend this run is in meta.json; ₹0 on a cached rerun)
- Checker disagreements (routed to review): 0

## Detection vs targets (on labelled items)

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Recall | 0.67 | 0.80 | ❌ |
| False-alarm rate | n/a | 0.10 | — |
| Localization | n/a | 0.70 | — |
| Category accuracy | 1.00 | 0.70 | ✅ |

## Per-language

| Lang | Items | Clean | Flagged | Uncertain | MQM penalty |
| --- | --- | --- | --- | --- | --- |
| mr | 3 | 0 | 2 | 1 | 10.0 |

## Flags by category

| Category | Flags |
| --- | --- |
| speech_omission | 1 |
| speech_repetition | 1 |

## Top failures (with evidence)

- **unseen-mr-0** [mr] — `speech_omission`/major (conf 0.62), span None. cer=0.569, deleted_chars=25, longest_deletion=25
- **unseen-mr-1** [mr] — `speech_repetition`/major (conf 0.62), span None. first_start_s=0.0, repeat_start_s=4.096, matched_duration_s=0.5

## Limitations

- Back-translation and ASR are noisy; lone noisy signals are routed to the review queue, not flagged, which trades recall for precision.
- Number-word (spelled-out) quantities are handled only conservatively.
- Low-support categories/languages fall back to pooled thresholds (see calibration.json).
