# Quality report

Items scored: **120** · skipped: **0** · model cost: **₹1.27**

The worst language is Bengali (bn), which has the highest weighted error rate at 78.9 errors per 1,000 words. The most problematic error category overall is format errors, with 11 instances across all languages. All five quality targets were met: detection recall 0.902 (≥ 80 %), false alarms on clean items 0.0 (≤ 10 %), localization 0.887 (≥ 70 %), category accuracy 0.891 (≥ 70 %), and judge consistency 0.945 (≥ 90 %). *(Written by the LLM from the numbers below.)*

## Headline

- Worst language: **bn**, 78.9 weighted errors per 1,000 source words.
- Most frequent error: **format**, 11 flags.

## Per language

| Language | clean | flagged | review | unscored | flags | weighted / 1k words |
| --- | --- | --- | --- | --- | --- | --- |
| bn | 20 | 18 | 2 | 0 | 18 | 78.9 |
| hi | 17 | 19 | 4 | 0 | 20 | 63.8 |
| ta | 14 | 18 | 8 | 0 | 19 | 66.7 |

Weights: minor 1, major 5, critical 10. Lower is better.

## Flags per category

| Category | bn | hi | ta | total |
| --- | --- | --- | --- | --- |
| format | 2 | 6 | 3 | 11 |
| mistranslation | 3 | 3 | 2 | 8 |
| omission | 3 | 2 | 2 | 7 |
| speech_omission | 2 | 2 | 2 | 6 |
| wrong_language | 2 | 2 | 2 | 6 |
| addition | 2 | 1 | 2 | 5 |
| garbled | 1 | 2 | 2 | 5 |
| number | 2 | 1 | 2 | 5 |
| term | 0 | 1 | 2 | 3 |
| speech_repetition | 1 | 0 | 0 | 1 |

## Top failures

- **wmt-125 / bn**: wrong_language, critical, confidence 0.99, found by judge, regen_diff, script
  - source: “state prisons”
  - translation: “घर के करीब, वाशिंगटन ने हाल ही में राज्य की जेलों, पुलिस, स्कूलों और अन्य संस्थाओं की स्वतंत्र निगरानी स्थापित या विस्त…”
- **wmt-28 / bn**: omission, critical, confidence 0.99, found by back_translation, judge, regen_diff
  - source: “and not beer”
  - translation: “তবে ঋষি সুনাকের দৃষ্টিভঙ্গিতে সমস্যা রয়েছে।”
- **sci-boiling / ta**: mistranslation, critical, confidence 0.94, found by judge, regen_diff
  - source: “boils”
  - translation: “உறைந்துவிடுகிறது”
- **civics-election / bn**: number, major, confidence 0.99, found by judge, numbers, regen_diff
  - source: “910”
  - translation: “৯৩”
- **geo-rivers / bn**: format, major, confidence 0.99, found by format, judge, regen_diff
  - source: “| River | Length (km) | Ends in | | --- | --- | --- | | Ganga | 2,525 | Bay of Bengal | | Godavari | 1,465 | Bay of Ben…”
  - translation: “| নদী | দৈর্ঘ্য (কিমি) | মোহনা | | --- | --- | --- | | গঙ্গা | ২,৫২৫ | বঙ্গোপসাগর | | গোদাবরী | ১,৪৬৫ | বঙ্গোপসাগর | | …”

## Against labels

120 labelled items, 59 of them clean. Matching rule: item detected if it has any flag; a label is located by a flag whose span overlaps it on either side (or by category when the label has no span); primary span side: source.

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Detection recall | 90% | >= 80% | yes |
| False alarms on clean items | 0% | <= 10% | yes |
| Localization | 89% | >= 70% | yes |
| Category accuracy | 89% | >= 70% | yes |
| Judge consistency | 94% | >= 90% | yes |

| Category | Recall | Precision | Found / labelled |
| --- | --- | --- | --- |
| addition | 67% | 80% | 4 / 6 |
| format | 100% | 73% | 8 / 8 |
| garbled | 83% | 100% | 5 / 6 |
| mistranslation | 83% | 62% | 5 / 6 |
| number | 83% | 100% | 5 / 6 |
| omission | 100% | 86% | 6 / 6 |
| speech_omission | 100% | 100% | 6 / 6 |
| speech_repetition | 17% | 100% | 1 / 6 |
| term | 50% | 100% | 3 / 6 |
| wrong_language | 100% | 100% | 6 / 6 |

22 uncertain findings are in `review_queue.csv`. Fill `human_label` (a category, or `clean`) and pass the file back with `--calibration` to re-calibrate.
