# Quality report

Unflagged does not mean clean. Inspect skipped and failed check coverage before interpreting quality.

Items: 10. Invalid: 0. Filtered: 0. Failed checks: 0.
Conservative uncached request cost: INR 0.000000. Current execution billing is logged separately in the cache directory.

| Language | Items | Flagged | Unassessed |
|---|---:|---:|---:|
| bn | 3 | 2 | 0 |
| hi | 4 | 1 | 1 |
| ta | 3 | 2 | 0 |

## Held-out quality targets

| Metric | Measured | Target | Status |
|---|---:|---:|---|
| detection_recall | 1.0 | 0.8 | met |
| false_alarm_rate | 0.0 | 0.1 | met |
| localization_overlap | 1.0 | 0.7 | met |
| category_accuracy | 1.0 | 0.7 | met |
| judge_consistency | not measured | 0.9 | missed or unsupported |

Metrics reflect the annotation origins listed in calibration.json; synthetic labels are not human validation.
Missed targets require category-level review; checker failures, absent references and insufficient labels limit coverage.

## Findings

- smoke-04 (hi): number, major, translation[0, 2]; {"unexpected": "२६", "source_numbers": ["25"], "missing_source_number": "25", "source_span": [0, 2]}
- smoke-05 (ta): number, major, translation[0, 2]; {"unexpected": "௨௬", "source_numbers": ["25"], "missing_source_number": "25", "source_span": [0, 2]}
- smoke-06 (bn): number, major, translation[0, 2]; {"unexpected": "২৬", "source_numbers": ["25"], "missing_source_number": "25", "source_span": [0, 2]}
- smoke-08 (ta): wrong_language, major, translation[0, 19]; {"expected_script": "TAMIL", "matching_letters": 0, "note": "Script mismatch only; cannot identify language within a shared script."}
- smoke-09 (bn): format, major, source[4, 5]; {"structure": "inline_math", "expected_count": 2, "actual_count": 0}
