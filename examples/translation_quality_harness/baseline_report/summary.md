# Quality report

Dataset `data/demo.jsonl`, languages hi, ta, bn, judge `off` (off).

## Headline

| Items scored | Flagged | Clean | Failed | Skipped | Rows rejected |
|---|---|---|---|---|---|
| 120 | 48 | 72 | 0 | 0 | 0 |

## Quality targets

Measured on dataset labels.

| Check | Target | Value | Met |
|---|---|---|---|
| Detection recall | >= 80% | 70% | **no** |
| False alarms (clean items flagged) | <= 10% | 8% | yes |
| Localization (span overlap) | >= 70% | 90% | yes |
| Category accuracy | >= 70% | 98% | yes |
| Judge consistency | >= 90% | n/a | not measured |
| Determinism | cached rerun identical at INR 0 | see `run.json` (`spent_this_run_inr`) | |

Why targets are missed:

- Recall is lowest for `mistranslation` (0%), `speech_repetition` (33%), `wrong_language` (50%).

Coverage notes:

- The LLM judge did not run; mistranslation is only detectable by the judge.

## Per language

| Lang | Scored | Flagged | Flag rate | MQM quality | Top categories | Speech checked |
|---|---|---|---|---|---|---|
| bn | 40 | 17 | 42% | 89.3 | format 7, addition 4, omission 3 | 6 |
| hi | 40 | 17 | 42% | 90.7 | number 4, format 3, omission 3 | 6 |
| ta | 40 | 14 | 35% | 94.11 | format 9, addition 2, number 2 | 6 |

## Per category

| Category | Area | Flags | Items | Critical | Major | Minor |
|---|---|---|---|---|---|---|
| omission | translation | 7 | 7 | 3 | 4 | 0 |
| addition | translation | 7 | 7 | 0 | 7 | 0 |
| mistranslation | translation | 0 | 0 | 0 | 0 | 0 |
| number | translation | 8 | 8 | 0 | 8 | 0 |
| term | translation | 3 | 3 | 0 | 3 | 0 |
| format | translation | 19 | 16 | 0 | 19 | 0 |
| wrong_language | translation | 4 | 4 | 0 | 4 | 0 |
| speech_omission | speech | 3 | 3 | 3 | 0 | 0 |
| speech_repetition | speech | 1 | 1 | 0 | 1 | 0 |
| speech_garbled | speech | 3 | 3 | 0 | 3 | 0 |

## Detection quality per category

| Category | Labels | Flags | Precision | Recall | F1 |
|---|---|---|---|---|---|
| addition | 8 | 7 | 100% | 88% | 0.9333 |
| format | 7 | 19 | 21% | 100% | 0.3478 |
| mistranslation | 9 | 0 | n/a | 0% | 0.0 |
| number | 8 | 8 | 88% | 88% | 0.875 |
| omission | 8 | 7 | 71% | 75% | 0.7317 |
| speech_garbled | 3 | 3 | 100% | 100% | 1.0 |
| speech_omission | 3 | 3 | 100% | 100% | 1.0 |
| speech_repetition | 3 | 1 | 100% | 33% | 0.5 |
| term | 3 | 3 | 100% | 100% | 1.0 |
| wrong_language | 8 | 4 | 100% | 50% | 0.6667 |

## Top failures

- **fl-dev-0018-sentence** (bn) `speech_omission` critical, confidence 0.88, translation chars [63, 126], audio 2.8-5.7s
  - translation: "এবং আইসল্যান্ড ও পাকিস্তানের সরকারগুলির নেতারা পদত্যাগ করেছিলেন"
  - [audio_signal] speech lasts 5.5s, 48% of the ~11.5s this text needs
  - [back_transcription] the last 8 words of the text (50%) are missing from the transcript (truncated)
- **fl-dev-0167-sentence** (bn) `speech_omission` critical, confidence 0.88, translation chars [35, 68], audio 1.8-2.9s
  - translation: "সম্পর্ককে চরমভাবে ক্ষতিগ্রস্ত করে"
  - [audio_signal] speech lasts 2.8s, 45% of the ~6.2s this text needs
  - [back_transcription] the last 4 words of the text (40%) are missing from the transcript (truncated)
- **fl-dev-0696-sentence** (hi) `speech_omission` critical, confidence 0.80, translation chars [86, 130], audio 5.0-7.7s
  - translation: "इज़राइल में मुख्य पर्यटक स्थलों में से एक है"
  - [back_transcription] the last 9 words of the text (35%) are missing from the transcript (truncated)
- **fl-dev-0734-sentence** (hi) `omission` critical, confidence 0.67, source chars [37, 88]
  - source: "You must have a permit to stay overnight at Sirena."
  - [length_ratio] translation is unusually short for Hindi (z=-8.8)
  - [length_ratio] source has 2 sentences, translation 1
  - [back_translation] source clause barely appears in the back-translation (coverage 0%)
- **fl-dev-0898-latex** (hi) `omission` critical, confidence 0.67, source chars [36, 127]
  - source: "gravel or salt (calcium chloride) is often scattered on roads or paths to improve traction."
  - [length_ratio] translation is unusually short for Hindi (z=-10.2)
  - [length_ratio] source has 3 sentences, translation 2
  - [back_translation] source clause barely appears in the back-translation (coverage 0%)
- **fl-dev-0066-sentence** (bn) `omission` critical, confidence 0.61, source chars [92, 196]
  - source: "a meeting was held on March 20 at the East Bay Community Law Center for the victims of the housing scam."
  - [length_ratio] translation is unusually short for Bengali (z=-6.6)
  - [back_translation] source clause barely appears in the back-translation (coverage 0%)
- **fl-dev-0091-list** (bn) `format` major, confidence 0.90, translation chars [478, 478]
  - source: "- This contradicts earlier reports, which said that cancelling the runoff would have been against the constitution."
  - [format] structure mismatch: source has list item; translation has nothing
- **fl-dev-0175-paragraph** (hi) `number` major, confidence 0.90, translation chars [119, 123]
  - source: "2009"
  - translation: "2090"
  - [numbers] source number 2009 appears as 2090 in the translation
- **fl-dev-0187-sentence** (ta) `number` major, confidence 0.90, translation chars [19, 23]
  - source: "1"
  - translation: "1.15"
  - [numbers] source number 1 appears as 1.15 in the translation
- **fl-dev-0301-numbered** (ta) `format` major, confidence 0.90, translation chars [0, 338]
  - source: "1. Vautier's achievements outside of directing include a hunger strike in 1973 against what he viewed as political censorship. 2. French law was changed. His..."
  - translation: "வௌட்டியரின் சாதனைகள், அவர் அரசியல் தணிக்கை என்று கருதியதற்கு எதிராக 1973 இல் ஒரு உண்ணாவிரதப் போராட்டத்தில் ஈடுபட்டது உட்பட பல உள்ளன. பிரெஞ்சு சட்டம் மாற்றப்ப..."
  - [format] structure mismatch: source has list item, list item, list item; translation has text, text, text
- **fl-dev-0351-table** (bn) `format` major, confidence 0.90, translation chars [20, 195]
  - source: "\| 1 \| The steel needle floats on top of the water because of surface tension. \| \| 2 \| Surface tension happens because the water molecules at the surface of t..."
  - translation: "\| ১ \| পৃষ্ঠটানের কারণে স্টিলের সূঁচটি জলের উপরে ভেসে থাকে। \| ২ \| পৃষ্ঠটান ঘটে কারণ জলের উপরিভাগের অণুগুলো তাদের উপরের বাতাসের অণুগুলোর চেয়ে একে অপরের প্রতি ..."
  - [format] structure mismatch: source has table row with 2 cells, table row with 2 cells; translation has table row with 4 cells
- **fl-dev-0363-numbered** (bn) `number` major, confidence 0.90, translation chars [88, 89]
  - source: "2"
  - translation: "৭"
  - [numbers] source number 2 appears as 7 in the translation

## Regressions against baseline

0 items regressed, 0 improved, 120 unchanged. Details in `diff.md`.


## Cost

Cost is what the model outputs used by this report cost to produce (cold cache). A cached rerun spends INR 0; see `run.json`.

| Model | INR |
|---|---|
| indic-transcribe | 0.3222 |
| indic-translate | 0.2089 |
| **total** | 0.5311 |

Per 100 items: text checks INR 0.100; speech synthesis + transcription INR 1.790.

Files: `items.jsonl` (every check and flag with evidence), `calibration.json`, `dashboard.html`, `review_queue.csv`, `diff.md`.
