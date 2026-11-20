# Quality report

Items scored: **120** · skipped: **0** · model cost: **₹1.29**

Hindi has the highest weighted error rate (66.7 errors per 1,000 words), making it the worst language, and “format” errors are the most frequent category with nine occurrences across languages. All five quality targets—detection recall (0.85 ≥ 80 %), false alarms on clean items (1.7 % ≤ 10 %), localization (0.817 ≥ 70 %), category accuracy (0.939 ≥ 70 %), and judge consistency (0.933 ≥ 90 %)—are met. *(Written by the LLM from the numbers below.)*

## Headline

- Worst language: **hi**, 66.7 weighted errors per 1,000 source words.
- Most frequent error: **format**, 9 flags.
- Against the baseline: **47** items regressed, **51** improved (see `diff.md`).

## Per language

| Language | clean | flagged | review | unscored | flags | weighted / 1k words |
| --- | --- | --- | --- | --- | --- | --- |
| bn | 19 | 17 | 4 | 0 | 18 | 59.5 |
| hi | 14 | 19 | 7 | 0 | 19 | 66.7 |
| ta | 14 | 16 | 10 | 0 | 16 | 51.6 |

Weights: minor 1, major 5, critical 10. Lower is better.

## Flags per category

| Category | bn | hi | ta | total |
| --- | --- | --- | --- | --- |
| format | 3 | 4 | 2 | 9 |
| addition | 2 | 3 | 2 | 7 |
| mistranslation | 2 | 1 | 3 | 6 |
| number | 2 | 2 | 2 | 6 |
| omission | 2 | 2 | 2 | 6 |
| speech_omission | 2 | 2 | 2 | 6 |
| wrong_language | 2 | 2 | 2 | 6 |
| garbled | 2 | 2 | 0 | 4 |
| term | 1 | 1 | 1 | 3 |

## Top failures

- **wmt-103 / hi**: addition, critical, confidence 0.99, found by back_translation, judge, length, numbers, regen_diff
  - source: “FTSE 250 firm famous for sausage rolls and steak bakes plans to rise branch network but not prices.”
  - translation: “दिल्ली मेट्रो प्रतिदिन लगभग 65 लाख यात्रियों को 393 किमी ट्रैक पर ले जाती है और इसकी पहली लाइन 24 दिसंबर 2002 को खुली थ…”
- **health-dose / bn**: mistranslation, critical, confidence 0.94, found by judge, regen_diff
  - source: “never more than 4 g in one day”
  - translation: “৪ গ্রামের কম গ্রহণ করবেন না”
- **ent-bank / hi**: number, major, confidence 0.99, found by judge, numbers, regen_diff
  - source: “4.5”
  - translation: “4.5”
- **geo-rivers / ta**: omission, major, confidence 0.99, found by back_translation, judge, regen_diff
  - source: “The Narmada is one of the few large rivers that flows west.”
  - translation: “| நர்மதா | 1,312 | அரபிக்கடல் |”
- **sci-boiling / hi**: format, major, confidence 0.99, found by format, judge, numbers, regen_diff
  - source: “| Place | Height (m) | Boiling point (°C) | | --- | --- | --- | | Mumbai | 14 | 100 | | Shimla | 2,276 | 92 |”
  - translation: “| स्थान | ऊँचाई (मीटर) | क्वथनांक (°C) | | --- | --- | --- | | मुंबई | 14 | 100 | | शिमला | 2,276 |”

## Against labels

120 labelled items, 60 of them clean. Matching rule: item detected if it has any flag; a label is located by a flag whose span overlaps it on either side (or by category when the label has no span); primary span side: source.

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Detection recall | 85% | >= 80% | yes |
| False alarms on clean items | 2% | <= 10% | yes |
| Localization | 82% | >= 70% | yes |
| Category accuracy | 94% | >= 70% | yes |
| Judge consistency | 93% | >= 90% | yes |

| Category | Recall | Precision | Found / labelled |
| --- | --- | --- | --- |
| addition | 100% | 86% | 6 / 6 |
| format | 100% | 67% | 6 / 6 |
| garbled | 67% | 100% | 4 / 6 |
| mistranslation | 67% | 67% | 4 / 6 |
| number | 83% | 83% | 5 / 6 |
| omission | 100% | 100% | 6 / 6 |
| speech_omission | 100% | 100% | 6 / 6 |
| speech_repetition | 0% | n/a | 0 / 6 |
| term | 50% | 100% | 3 / 6 |
| wrong_language | 100% | 100% | 6 / 6 |

## Flags on items labelled clean

Counted as false alarms. Labels that only record injected errors leave real model errors marked clean, so read these before trusting the rate.

- **civics-election / hi**: format, minor, confidence 0.99, found by format, judge
  - source: “## E”
  - translation: “की उम्र 21 से घटाकर 18 वर्ष कर दी गई थी।⟦⟧”

27 uncertain findings are in `review_queue.csv`. Fill `human_label` (a category, or `clean`) and pass the file back with `--calibration` to re-calibrate.
