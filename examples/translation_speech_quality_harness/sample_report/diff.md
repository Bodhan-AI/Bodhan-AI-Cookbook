# Baseline diff

Baseline: `baseline_no_judge`. Items are matched by `id`; all comparisons use the 138 shared ids.

- Items: current 138, baseline 138, shared 138, new 0, removed 0
- Items with a counted flag (shared): 51 -> 64
- Labelled shared items judged right (flagged == has error): 115 -> 126 of 138
- Improved 12, regressed 1: exact McNemar p = 0.0034 -> **significant**
- Unlabelled items: newly flagged 0, no longer flagged 0; category set changed 0; errored in either run 0

## Counted flags per category (shared items)

| category | before | after | delta |
|---|---:|---:|---:|
| addition | 2 | 7 | +5 |
| format | 13 | 13 | +0 |
| mistranslation | 0 | 5 | +5 |
| number_unit | 16 | 16 | +0 |
| omission | 2 | 6 | +4 |
| speech_garbled | 11 | 11 | +0 |
| speech_omission_truncation | 4 | 4 | +0 |
| speech_repetition | 7 | 7 | +0 |
| term_entity | 0 | 1 | +1 |
| wrong_language_script | 9 | 9 | +0 |

## Label hits per category (shared labelled items)

| category | labels | hits before | hits after | gained | lost | McNemar p | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| addition | 8 | 3 | 7 | 4 | 0 | 0.1250 | within noise |
| format | 13 | 13 | 13 | 0 | 0 | 1.0000 | within noise |
| mistranslation | 4 | 0 | 0 | 0 | 0 | 1.0000 | within noise |
| number_unit | 8 | 8 | 8 | 0 | 0 | 1.0000 | within noise |
| omission | 8 | 2 | 6 | 4 | 0 | 0.1250 | within noise |
| speech_garbled | 8 | 8 | 8 | 0 | 0 | 1.0000 | within noise |
| speech_omission_truncation | 8 | 4 | 4 | 0 | 0 | 1.0000 | within noise |
| speech_repetition | 8 | 4 | 4 | 0 | 0 | 1.0000 | within noise |
| term_entity | 4 | 0 | 4 | 4 | 0 | 0.1250 | within noise |
| wrong_language_script | 8 | 8 | 8 | 0 | 0 | 1.0000 | within noise |

## Improved (first 5 of 12)

- `d005` (ta): flags none -> mistranslation; labels: term_entity; llm_judge (mistranslation): Incorrect word for Thrace; should be "த்ரேசியா" or similar.
- `d013` (ta): flags none -> addition; labels: addition; llm_judge (addition): Extra sentence not in source
- `d042` (ta): flags none -> addition; labels: addition; llm_judge (addition): Extra sentence not present in source.
- `d045` (bn): flags none -> mistranslation; labels: term_entity; llm_judge (mistranslation): Translates to 'per unit in Sweden' instead of 'per unit of GDP'
- `d087` (ta): flags none -> addition; labels: addition; llm_judge (addition): Adds unrelated Italian pronunciation content not present in source.

## Regressed (first 5 of 1)

- `d100` (ta): flags none -> addition; labels: clean; llm_judge (addition): Translation adds 'committee', not present in source.
