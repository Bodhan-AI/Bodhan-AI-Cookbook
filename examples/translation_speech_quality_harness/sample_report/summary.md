# Bodhan Quality Evaluation

- Items: 80
- Flags: 7
- Quarantined: 0
- Skipped: 6
- Coverage: 0.925000 (74/80)
- Failed checks: 6
- Cost: INR 0.0
- Judge: 0 complete, 0 unavailable, 6 disabled

## Targets

Held-out metrics are reported below. Thresholds remain at documented defaults because detector score distributions are not yet exposed for tuning.

## Coverage by language

- bn: 20/20 (1.000000)
- hi: 37/40 (0.925000)
- ta: 17/20 (0.850000)

## Unchecked items

- in22-gen-31-ta (ta): failed_checks — ['judge_disabled_for_candidates']
- in22-gen-45-hi (hi): failed_checks — ['judge_disabled_for_candidates']
- in22-gen-46-ta (ta): failed_checks — ['judge_disabled_for_candidates']
- in22-gen-68-hi (hi): failed_checks — ['judge_disabled_for_candidates']
- in22-gen-73-hi (hi): failed_checks — ['judge_disabled_for_candidates']
- in22-gen-96-ta (ta): failed_checks — ['judge_disabled_for_candidates']

## Held-out label metrics

- Evaluation status: ok
- Source groups: dev=33, test=33
- Test items: 40

### Annotator: self

| Language | Category | Gold support | Predicted | Precision (95% Wilson CI) | Recall (95% Wilson CI) | Span overlap | Category accuracy |
|---|---|---:|---:|---|---|---:|---:|
| bn | mistranslation | 3 | 0 | 0.000000 (0.000000, 0.000000) | 0.000000 (0.000000, 0.561506) | 0.000000 | — |
| bn | number | 0 | 1 | 0.000000 (0.000000, 0.793457) | 0.000000 (0.000000, 0.000000) | 0.000000 | — |
| bn | term | 1 | 0 | 0.000000 (0.000000, 0.000000) | 0.000000 (0.000000, 0.793457) | 0.000000 | — |
| bn | all categories (span-matched) | 0 | 0 | — | — | — | 0.000000 (0.000000, 0.000000) |
| bn | clean false alarms | — | 1 / 4 | 0.250000 (0.045586, 0.699364) | — | — | — |
| hi | addition | 1 | 0 | 0.000000 (0.000000, 0.000000) | 0.000000 (0.000000, 0.793457) | 0.000000 | — |
| hi | mistranslation | 1 | 0 | 0.000000 (0.000000, 0.000000) | 0.000000 (0.000000, 0.793457) | 0.000000 | — |
| hi | omission | 1 | 0 | 0.000000 (0.000000, 0.000000) | 0.000000 (0.000000, 0.793457) | 0.000000 | — |
| hi | term | 3 | 0 | 0.000000 (0.000000, 0.000000) | 0.000000 (0.000000, 0.561506) | 0.000000 | — |
| hi | all categories (span-matched) | 0 | 0 | — | — | — | 0.000000 (0.000000, 0.000000) |
| hi | clean false alarms | — | 0 / 15 | 0.000000 (0.000000, 0.203889) | — | — | — |
| ta | all categories (span-matched) | 0 | 0 | — | — | — | 0.000000 (0.000000, 0.000000) |
| ta | clean false alarms | — | 0 / 11 | 0.000000 (0.000000, 0.258840) | — | — | — |
