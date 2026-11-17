# Quality report

Dataset `data/heldout_mr_te.jsonl`, languages mr, te, judge `off` (off).

## Headline

| Items scored | Flagged | Clean | Failed | Skipped | Rows rejected |
|---|---|---|---|---|---|
| 60 | 23 | 37 | 0 | 0 | 0 |

## Quality targets

Measured on dataset labels.

| Check | Target | Value | Met |
|---|---|---|---|
| Detection recall | >= 80% | 70% | **no** |
| False alarms (clean items flagged) | <= 10% | 3% | yes |
| Localization (span overlap) | >= 70% | 90% | yes |
| Category accuracy | >= 70% | 100% | yes |
| Judge consistency | >= 90% | n/a | not measured |
| Determinism | cached rerun identical at INR 0 | see `run.json` (`spent_this_run_inr`) | |

Why targets are missed:

- Recall is lowest for `mistranslation` (0%), `term` (50%), `omission` (60%).

Coverage notes:

- No audio was transcribed, so speech categories were not measured.
- The LLM judge did not run; mistranslation is only detectable by the judge.

## Per language

| Lang | Scored | Flagged | Flag rate | MQM quality | Top categories | Speech checked |
|---|---|---|---|---|---|---|
| mr | 30 | 9 | 30% | 91.71 | addition 3, format 3, wrong_language 3 | 0 |
| te | 30 | 14 | 47% | 86.29 | format 6, number 3, omission 3 | 0 |

## Per category

| Category | Area | Flags | Items | Critical | Major | Minor |
|---|---|---|---|---|---|---|
| omission | translation | 4 | 4 | 0 | 4 | 0 |
| addition | translation | 5 | 5 | 0 | 5 | 0 |
| mistranslation | translation | 0 | 0 | 0 | 0 | 0 |
| number | translation | 4 | 4 | 0 | 4 | 0 |
| term | translation | 2 | 2 | 0 | 2 | 0 |
| format | translation | 9 | 5 | 0 | 9 | 0 |
| wrong_language | translation | 5 | 5 | 4 | 1 | 0 |
| speech_omission | speech | 0 | 0 | 0 | 0 | 0 |
| speech_repetition | speech | 0 | 0 | 0 | 0 | 0 |
| speech_garbled | speech | 0 | 0 | 0 | 0 | 0 |

## Detection quality per category

| Category | Labels | Flags | Precision | Recall | F1 |
|---|---|---|---|---|---|
| addition | 4 | 5 | 80% | 100% | 0.8889 |
| format | 4 | 9 | 22% | 100% | 0.3636 |
| mistranslation | 4 | 0 | n/a | 0% | 0.0 |
| number | 4 | 4 | 100% | 100% | 1.0 |
| omission | 5 | 4 | 75% | 60% | 0.6667 |
| term | 4 | 2 | 100% | 50% | 0.6667 |
| wrong_language | 5 | 5 | 80% | 80% | 0.8 |

## Top failures

- **fl-devtest-0301-sentence** (te) `wrong_language` critical, confidence 0.95, translation chars [0, 114]
  - translation: "काही वेळेस-हिंसक ठरणारी ही निदर्शने निवडणुका घेण्यातील अपयशामुळे उद्भवतात, यापैकी काही 2011 पासून झालेल्या नाहीत."
  - [script_language] only 0% of letters are in Telu; dominant script is Deva
- **fl-devtest-0344-paragraph** (mr) `wrong_language` critical, confidence 0.95, translation chars [0, 285]
  - translation: "దీనిని రసాయనం యొక్క pH అని అంటారు. ఎర్ర క్యాబేజీ జ్యూస్ ను ఉపయోగించి ఇండికేటర్ తయారు చేసుకోవచ్చు. క్యాబేజ్ జ్యూస్ రసాయనం ఆమ్ల లేదా క్షార (క్షార) ఎలా అనే దాని..."
  - [script_language] only 0% of letters are in Deva; dominant script is Telu
- **fl-devtest-0432-sentence** (te) `wrong_language` critical, confidence 0.95, translation chars [0, 99]
  - translation: "३५ मिमी फॉरमॅट म्हणजे प्रत्यक्षात रुंदीला ३६ मिमी तर उंचीला २४ मिमी असे थोडेसे गोंधळात टाकणारे आहे."
  - [script_language] only 0% of letters are in Telu; dominant script is Deva
- **fl-devtest-0215-sentence** (mr) `wrong_language` critical, confidence 0.75, translation chars [0, 125]
  - translation: "1993 में शो छोड़ने के बाद भी वे एग्जीक्यूटिव प्रोड्यूसर के पद पर बने रहे और हर सीज़न में लाखों-करोड़ों डॉलर की रॉयल्टी लेते रहे."
  - [script_language] 6 Hindi function words vs 0 Marathi ones
- **fl-devtest-0050-sentence** (te) `number` major, confidence 0.90, translation chars [39, 43]
  - source: "12.00"
  - translation: "12.1"
  - [numbers] source number 12.00 appears as 12.1 in the translation
- **fl-devtest-0250-sentence** (te) `number` major, confidence 0.90, translation chars [81, 82]
  - source: "1"
  - translation: "3"
  - [numbers] source number 1 appears as 3 in the translation
- **fl-devtest-0354-table** (te) `format` major, confidence 0.90, translation chars [0, 0]
  - source: "\| # \| ¶ \| \|---\|---\|"
  - [format] structure mismatch: source has table row with 2 cells, table sep with 2 cells; translation has nothing
- **fl-devtest-0354-table** (te) `format` major, confidence 0.90, translation chars [301, 412]
  - translation: "\| 3 \| అంతరిక్షంలో ఉపగ్రహాన్ని లేదా టెలిస్కోప్ ను ఉంచటానికి 100 అడుగుల ఎత్తులో ఒక భారీ రాకెట్ ను తీసుకుంటుంది. \|"
  - [format] structure mismatch: source has nothing; translation has table row with 2 cells
- **fl-devtest-0444-table** (te) `format` major, confidence 0.90, translation chars [20, 25]
  - translation: "\| 1 \|"
  - [format] structure mismatch: source has nothing; translation has table row with 1 cells
- **fl-devtest-0444-table** (te) `format` major, confidence 0.90, translation chars [154, 154]
  - source: "\| 2 \| Since there was limited response to this tactic, Germany expected a similar response to its unrestricted submarine warfare. \|"
  - [format] structure mismatch: source has table row with 2 cells; translation has nothing
- **fl-devtest-0599-latex** (mr) `format` major, confidence 0.90, translation chars [0, 0]
  - source: "$$\frac{d}{t} = v$$"
  - [format] LaTeX segment missing from translation: `\frac{d}{t} = v`
- **fl-devtest-0599-latex** (mr) `format` major, confidence 0.90, translation chars [81, 98]
  - source: "$$\frac{d}{t} = v$$"
  - translation: "$$\frac{d}{t} = v"
  - [format] inline markup mismatch on line (math: source 1, translation 0)

## Cost

Cost is what the model outputs used by this report cost to produce (cold cache). A cached rerun spends INR 0; see `run.json`.

| Model | INR |
|---|---|
| indic-translate | 0.0610 |
| **total** | 0.0610 |

Per 100 items: text checks INR 0.102; speech synthesis + transcription INR 0.000.

Files: `items.jsonl` (every check and flag with evidence), `calibration.json`, `dashboard.html`, `review_queue.csv`.
