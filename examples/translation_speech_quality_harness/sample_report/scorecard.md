# Scorecard

125 items scored (101 with errors, 24 clean), 157 labels. Missing from the report: 0; not checked (status not ok): 0; excluded (calibration): 40; unlabelled, skipped: 15.

| metric | value |
|---|---|
| recall | 87.3 % |
| item recall | 90.1 % |
| false alarm rate | 0.0 % |
| precision | 88.3 % |
| localization | 83.9 % |
| category accuracy | 96.4 % |

Review queue: 12 of 20 missed labels are in it.

## Per category

| category | labels | recall | precision | localisation |
|---|---:|---:|---:|---:|
| addition | 3 | 100.0 % | 100.0 % | 100.0 % |
| format | 9 | 100.0 % | 80.0 % | 88.9 % |
| mistranslation | 25 | 84.0 % | 94.1 % | 85.7 % |
| number | 11 | 100.0 % | 83.3 % | 100.0 % |
| omission | 24 | 87.5 % | 90.0 % | 71.4 % |
| speech_garbled | 10 | 90.0 % | 60.0 % | 77.8 % |
| speech_omission | 41 | 80.5 % | 96.5 % | 87.9 % |
| speech_repetition | 16 | 100.0 % | 93.8 % | 81.2 % |
| term | 12 | 66.7 % | 87.5 % | 62.5 % |
| wrong_language | 6 | 100.0 % | 100.0 % | 100.0 % |

## Per language

| language | labels | recall | false alarms |
|---|---:|---:|---:|
| bn | 45 | 91.1 % | 0.0 % |
| hi | 41 | 82.9 % | 0.0 % |
| ta | 71 | 87.3 % | 0.0 % |

## Per label source (injector method or annotator)

| | labels | detected | recall |
|---|---:|---:|---:|
| ai-review | 96 | 77 | 80.2 % |
| injector:clip_region | 2 | 2 | 100.0 % |
| injector:cut_middle | 5 | 4 | 80.0 % |
| injector:drop_phrase | 2 | 2 | 100.0 % |
| injector:drop_sentence | 1 | 1 | 100.0 % |
| injector:entity_swap | 1 | 1 | 100.0 % |
| injector:extra_clause | 1 | 1 | 100.0 % |
| injector:first_digit | 3 | 3 | 100.0 % |
| injector:foreign_sentence | 1 | 1 | 100.0 % |
| injector:garble_entity | 1 | 1 | 100.0 % |
| injector:heading_marker | 1 | 1 | 100.0 % |
| injector:insert_silence | 3 | 3 | 100.0 % |
| injector:last_digit | 1 | 1 | 100.0 % |
| injector:month_swap | 1 | 1 | 100.0 % |
| injector:negation | 2 | 2 | 100.0 % |
| injector:noise_burst | 4 | 4 | 100.0 % |
| injector:other_language | 4 | 4 | 100.0 % |
| injector:quantifier_swap | 2 | 2 | 100.0 % |
| injector:repeat_sentence | 1 | 1 | 100.0 % |
| injector:repeat_stretch | 15 | 15 | 100.0 % |
| injector:source_english | 1 | 1 | 100.0 % |
| injector:table_pipe | 1 | 1 | 100.0 % |
| injector:table_separator | 1 | 1 | 100.0 % |
| injector:truncate_tail | 1 | 1 | 100.0 % |
| injector:word_swap | 6 | 6 | 100.0 % |

## Per severity

| | labels | detected | recall |
|---|---:|---:|---:|
| critical | 15 | 15 | 100.0 % |
| major | 119 | 100 | 84.0 % |
| minor | 23 | 22 | 95.7 % |
