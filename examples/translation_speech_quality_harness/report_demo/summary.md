# Quality report

Items scored: **120** · skipped: **0** · model cost: **₹1.27**

Bengali (bn) shows the highest weighted error rate (89.6 per 1,000 words), making it the weakest language, and the “omission” category has the most errors overall (12). All quality targets are satisfied except the false‑alarm rate on clean items, which is 15.3%—above the ≤10% threshold. *(Written by the LLM from the numbers below.)*

## Headline

- Worst language: **bn**, 89.6 weighted errors per 1,000 source words.
- Most frequent error: **omission**, 12 flags.

## Per language

| Language | clean | flagged | review | unscored | flags | weighted / 1k words |
| --- | --- | --- | --- | --- | --- | --- |
| bn | 20 | 20 | 0 | 0 | 21 | 89.6 |
| hi | 17 | 22 | 1 | 0 | 24 | 78.1 |
| ta | 14 | 23 | 3 | 0 | 24 | 84.6 |

Weights: minor 1, major 5, critical 10. Lower is better.

## Flags per category

| Category | bn | hi | ta | total |
| --- | --- | --- | --- | --- |
| omission | 4 | 4 | 4 | 12 |
| format | 2 | 6 | 3 | 11 |
| mistranslation | 4 | 3 | 3 | 10 |
| term | 1 | 3 | 4 | 8 |
| speech_omission | 2 | 2 | 2 | 6 |
| wrong_language | 2 | 2 | 2 | 6 |
| addition | 2 | 1 | 2 | 5 |
| garbled | 1 | 2 | 2 | 5 |
| number | 2 | 1 | 2 | 5 |
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
| Detection recall | 92% | >= 80% | yes |
| False alarms on clean items | 15% | <= 10% | **no** |
| Localization | 90% | >= 70% | yes |
| Category accuracy | 88% | >= 70% | yes |
| Judge consistency | 94% | >= 90% | yes |

| Category | Recall | Precision | Found / labelled |
| --- | --- | --- | --- |
| addition | 67% | 80% | 4 / 6 |
| format | 100% | 73% | 8 / 8 |
| garbled | 83% | 100% | 5 / 6 |
| mistranslation | 83% | 50% | 5 / 6 |
| number | 83% | 100% | 5 / 6 |
| omission | 100% | 50% | 6 / 6 |
| speech_omission | 100% | 100% | 6 / 6 |
| speech_repetition | 17% | 100% | 1 / 6 |
| term | 50% | 38% | 3 / 6 |
| wrong_language | 100% | 100% | 6 / 6 |

## Flags on items labelled clean

Counted as false alarms. Labels that only record injected errors leave real model errors marked clean, so read these before trusting the rate.

- **wmt-109 / hi**: term, major, confidence 0.85, found by judge
  - source: “vegan”
  - translation: “शाकाहारी”
- **wmt-125 / hi**: term, major, confidence 0.85, found by judge
  - source: “state prisons”
  - translation: “राज्य की जेलों”
- **wmt-2 / bn**: mistranslation, major, confidence 0.85, found by judge
  - source: “"People Swimming in the Swimming Pool"”
  - translation: “পুকুরে সাঁতার কাটা মানুষ”
- **wmt-54 / ta**: mistranslation, major, confidence 0.85, found by judge
  - source: “brinkmanship”
  - translation: “அபாயகரமான சூழ்நிலை”
- **wmt-58 / hi**: omission, major, confidence 0.85, found by judge
  - source: “Wednesday”
- **wmt-71 / bn**: omission, major, confidence 0.85, found by judge
  - source: “allegedly 'protect' the country”
- **wmt-71 / ta**: omission, major, confidence 0.85, found by judge
  - source: “allegedly 'protect'”
- **wmt-87 / ta**: term, major, confidence 0.85, found by judge
  - source: “Gartshore Works”
  - translation: “கார்ட்ஷோர் ஒர்க்ஸ்”
- **wmt-92 / ta**: term, major, confidence 0.85, found by judge
  - source: “new devolved aggregates tax”
  - translation: “புதிய அதிகாரப் பகிர்வு திரட்டல் வரி”

10 uncertain findings are in `review_queue.csv`. Fill `human_label` (a category, or `clean`) and pass the file back with `--calibration` to re-calibrate.
