# Voice comparison

Same texts, every voice; speech checks with default thresholds.

## bn

| Voice | Items | Items with a speech flag | Flags | Mean transcript CER | Median chars/s |
|---|---:|---:|---|---:|---:|
| Ishita | 15 | 3 | speech_garbled 2, speech_omission 1 | 28.9% | 13.3 |
| Sourav | 15 | 2 | speech_garbled 1, speech_omission 1 | 31.4% | 13.5 |

Paired (Ishita vs Sourav): flagged only with Ishita 1, flagged only with Sourav 0

## hi

| Voice | Items | Items with a speech flag | Flags | Mean transcript CER | Median chars/s |
|---|---:|---:|---|---:|---:|
| Kavya | 15 | 3 | speech_garbled 1, speech_omission 2 | 27.5% | 13.3 |
| Amit | 15 | 3 | speech_garbled 1, speech_omission 2 | 27.5% | 12.8 |

Paired (Kavya vs Amit): flagged only with Kavya 0, flagged only with Amit 0

## ta

| Voice | Items | Items with a speech flag | Flags | Mean transcript CER | Median chars/s |
|---|---:|---:|---|---:|---:|
| Anitha | 15 | 5 | speech_omission 7, speech_repetition 1 | 30.5% | 13.5 |
| Arun | 15 | 4 | speech_omission 7 | 30.2% | 16.5 |

Paired (Anitha vs Arun): flagged only with Anitha 1, flagged only with Arun 0
