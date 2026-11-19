# Quality report

Items scored: **120** · skipped: **0** · model cost: **₹1.29**

Hindi has the highest weighted error rate (91.8 errors per 1,000 words), making it the worst language, and the omission category is the most problematic with 12 total flags. All quality targets are met except the false‑alarm rate on clean items, which is 0.2 (20 %) and exceeds the ≤ 10 % limit. Consequently, the overall target set is not fully satisfied. *(Written by the LLM from the numbers below.)*

## Headline

- Worst language: **hi**, 91.8 weighted errors per 1,000 source words.
- Most frequent error: **omission**, 12 flags.
- Against the baseline: **49** items regressed, **51** improved (see `diff.md`).

## Per language

| Language | clean | flagged | review | unscored | flags | weighted / 1k words |
| --- | --- | --- | --- | --- | --- | --- |
| bn | 19 | 19 | 2 | 0 | 21 | 70.3 |
| hi | 14 | 24 | 2 | 0 | 25 | 91.8 |
| ta | 14 | 22 | 4 | 0 | 22 | 73.1 |

Weights: minor 1, major 5, critical 10. Lower is better.

## Flags per category

| Category | bn | hi | ta | total |
| --- | --- | --- | --- | --- |
| omission | 3 | 5 | 4 | 12 |
| term | 2 | 4 | 4 | 10 |
| format | 3 | 4 | 2 | 9 |
| mistranslation | 3 | 1 | 4 | 8 |
| addition | 2 | 3 | 2 | 7 |
| number | 2 | 2 | 2 | 6 |
| speech_omission | 2 | 2 | 2 | 6 |
| wrong_language | 2 | 2 | 2 | 6 |
| garbled | 2 | 2 | 0 | 4 |

## Top failures

- **wmt-103 / hi**: addition, critical, confidence 0.99, found by back_translation, judge, length, numbers, regen_diff
  - source: “FTSE 250 firm famous for sausage rolls and steak bakes plans to rise branch network but not prices.”
  - translation: “दिल्ली मेट्रो प्रतिदिन लगभग 65 लाख यात्रियों को 393 किमी ट्रैक पर ले जाती है और इसकी पहली लाइन 24 दिसंबर 2002 को खुली थ…”
- **health-dose / bn**: mistranslation, critical, confidence 0.94, found by judge, regen_diff
  - source: “never more than 4 g in one day”
  - translation: “৪ গ্রামের কম গ্রহণ করবেন না”
- **wmt-28 / hi**: omission, critical, confidence 0.85, found by judge
  - source: “and not beer”
- **ent-bank / hi**: number, major, confidence 0.99, found by judge, numbers, regen_diff
  - source: “4.5”
  - translation: “4.5”
- **sci-boiling / hi**: format, major, confidence 0.99, found by format, judge, numbers, regen_diff
  - source: “| Place | Height (m) | Boiling point (°C) | | --- | --- | --- | | Mumbai | 14 | 100 | | Shimla | 2,276 | 92 |”
  - translation: “| स्थान | ऊँचाई (मीटर) | क्वथनांक (°C) | | --- | --- | --- | | मुंबई | 14 | 100 | | शिमला | 2,276 |”

## Against labels

120 labelled items, 60 of them clean. Matching rule: item detected if it has any flag; a label is located by a flag whose span overlaps it on either side (or by category when the label has no span); primary span side: source.

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Detection recall | 88% | >= 80% | yes |
| False alarms on clean items | 20% | <= 10% | **no** |
| Localization | 82% | >= 70% | yes |
| Category accuracy | 94% | >= 70% | yes |
| Judge consistency | 93% | >= 90% | yes |

| Category | Recall | Precision | Found / labelled |
| --- | --- | --- | --- |
| addition | 100% | 86% | 6 / 6 |
| format | 100% | 67% | 6 / 6 |
| garbled | 67% | 100% | 4 / 6 |
| mistranslation | 67% | 50% | 4 / 6 |
| number | 83% | 83% | 5 / 6 |
| omission | 100% | 50% | 6 / 6 |
| speech_omission | 100% | 100% | 6 / 6 |
| speech_repetition | 0% | n/a | 0 / 6 |
| term | 50% | 30% | 3 / 6 |
| wrong_language | 100% | 100% | 6 / 6 |

## Flags on items labelled clean

Counted as false alarms. Labels that only record injected errors leave real model errors marked clean, so read these before trusting the rate.

- **civics-election / hi**: format, minor, confidence 0.99, found by format, judge
  - source: “## E”
  - translation: “की उम्र 21 से घटाकर 18 वर्ष कर दी गई थी।⟦⟧”
- **wmt-109 / hi**: term, major, confidence 0.85, found by judge
  - source: “vegan”
  - translation: “शाकाहारी”
- **wmt-109 / ta**: omission, major, confidence 0.85, found by judge
  - source: “having recently brought back the vegan steak bake”
- **wmt-125 / hi**: term, major, confidence 0.85, found by judge
  - source: “state prisons”
  - translation: “राज्य की जेलों”
- **wmt-28 / hi**: term, major, confidence 0.85, found by judge
  - source: “European brewers”
  - translation: “यूरोपीय शराब बनाने वाली कंपनियों”
- **wmt-28 / hi**: omission, critical, confidence 0.85, found by judge
  - source: “and not beer”
- **wmt-28 / ta**: term, major, confidence 0.85, found by judge
  - source: “European brewers”
  - translation: “ஐரோப்பிய மதுபான உற்பத்தியாளர்கள்”
- **wmt-54 / ta**: mistranslation, major, confidence 0.85, found by judge
  - source: “brinkmanship”
  - translation: “அபாயகரமான சூழ்நிலை”
- **wmt-58 / hi**: omission, major, confidence 0.85, found by judge
  - source: “Wednesday”
- **wmt-71 / bn**: omission, major, confidence 0.85, found by judge
  - source: “allegedly 'protect' the country”
- **wmt-79 / hi**: omission, major, confidence 0.85, found by judge
  - source: “more”
- **wmt-87 / ta**: term, major, confidence 0.85, found by judge
  - source: “Gartshore Works”
  - translation: “கார்ட்ஷோர் ஒர்க்ஸ்”
- **wmt-92 / bn**: term, major, confidence 0.85, found by judge
  - source: “new devolved aggregates tax”
  - translation: “নতুন বিকেন্দ্রীভূত সামগ্রী কর”

12 uncertain findings are in `review_queue.csv`. Fill `human_label` (a category, or `clean`) and pass the file back with `--calibration` to re-calibrate.
