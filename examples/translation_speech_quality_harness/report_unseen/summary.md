# Quality report

Items scored: **40** · skipped: **0** · model cost: **₹0.48**

The quality targets were met, as all five metrics exceeded their required thresholds. Among the languages, mr has the higher error rate at 110.9 weighted errors per 1,000 words compared to ur's 88.7. The format category is the worst overall with 4 total errors, followed by number with 3. *(Written by the LLM from the numbers below.)*

## Headline

- Worst language: **mr**, 110.9 weighted errors per 1,000 source words.
- Most frequent error: **format**, 4 flags.

## Per language

| Language | clean | flagged | review | unscored | flags | weighted / 1k words |
| --- | --- | --- | --- | --- | --- | --- |
| mr | 7 | 10 | 3 | 0 | 11 | 110.9 |
| ur | 9 | 10 | 1 | 0 | 10 | 88.7 |

Weights: minor 1, major 5, critical 10. Lower is better.

## Flags per category

| Category | mr | ur | total |
| --- | --- | --- | --- |
| format | 3 | 1 | 4 |
| number | 1 | 2 | 3 |
| addition | 1 | 1 | 2 |
| garbled | 1 | 1 | 2 |
| speech_omission | 1 | 1 | 2 |
| speech_repetition | 1 | 1 | 2 |
| term | 1 | 1 | 2 |
| wrong_language | 1 | 1 | 2 |
| mistranslation | 1 | 0 | 1 |
| omission | 0 | 1 | 1 |

## Top failures

- **ent-price / ur**: number, critical, confidence 0.99, found by judge, numbers, regen_diff
  - source: “18.2”
  - translation: “18.2”
- **wmt-29 / ur**: wrong_language, critical, confidence 0.99, found by judge, regen_diff, script
  - source: “Environment ministers agreed an EU Council negotiating mandate on the proposed Packaging and Packaging Waste Regulation…”
  - translation: “पर्यावरण मंत्र्यांनी प्रस्तावित पॅकेजिंग आणि पॅकेजिंग वेस्ट रेग्युलेशन (पीपीडब्ल्यूआर) वर ख्रिसमसपूर्वी ईयू कौन्सिलच्या…”
- **wmt-95 / mr**: mistranslation, critical, confidence 0.94, found by judge, regen_diff
  - source: “somebody with more knowledge and understanding of the problems”
  - translation: “कमी ज्ञान आणि समस्यांची समज नसलेल्या व्यक्तीने”
- **math-area / mr**: format, major, confidence 0.99, found by format, judge
  - source: “## A”
  - translation: “ा दुप्पट केली तर क्षेत्रफळ चार पट वाढते.⟦⟧”
- **math-percent / ur**: addition, major, confidence 0.99, found by back_translation, judge, length, numbers, regen_diff
  - source: “During a sale the price is reduced by 25%, so the new price is ₹900.”
  - translation: “ایک بالغ شخص کا دل آرام کی حالت میں تقریباً 72 بار فی منٹ دھڑکتا ہے۔”

## Against labels

40 labelled items, 20 of them clean. Matching rule: item detected if it has any flag; a label is located by a flag whose span overlaps it on either side (or by category when the label has no span); primary span side: source.

| Metric | Value | Target | Met |
| --- | --- | --- | --- |
| Detection recall | 100% | >= 80% | yes |
| False alarms on clean items | 0% | <= 10% | yes |
| Localization | 95% | >= 70% | yes |
| Category accuracy | 95% | >= 70% | yes |
| Judge consistency | 100% | >= 90% | yes |

| Category | Recall | Precision | Found / labelled |
| --- | --- | --- | --- |
| addition | 100% | 100% | 2 / 2 |
| format | 100% | 50% | 2 / 2 |
| garbled | 100% | 100% | 2 / 2 |
| mistranslation | 50% | 100% | 1 / 2 |
| number | 100% | 67% | 2 / 2 |
| omission | 50% | 100% | 1 / 2 |
| speech_omission | 100% | 100% | 2 / 2 |
| speech_repetition | 100% | 100% | 2 / 2 |
| term | 100% | 100% | 2 / 2 |
| wrong_language | 100% | 100% | 2 / 2 |

10 uncertain findings are in `review_queue.csv`. Fill `human_label` (a category, or `clean`) and pass the file back with `--calibration` to re-calibrate.
