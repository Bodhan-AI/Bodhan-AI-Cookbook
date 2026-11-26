# Quality report

Dataset `demo_injected.jsonl`: 111 evaluated of 111 (0 malformed skipped, 0 without input, 0 crashed, 0 filtered by language). **45 items flagged.**

## Targets (measured on labelled / injected items)

**Held-out evaluation.** Thresholds were tuned on 75 labelled items; the numbers below are measured on 36 other items the tuner never saw (17 with errors, 19 clean). The last column is the tuning set, i.e. in-sample and optimistic. With this few items, per-category numbers are very noisy.

| Target | Goal | Held-out | Status | Tuning set (in-sample) |
| --- | --- | --- | --- | --- |
| Detection recall | ≥ 80% | 94.1% | met | 86.7% |
| False alarms on clean items | ≤ 10% | 0.0% | met | 6.7% |
| Span overlaps seeded error | ≥ 70% | 100.0% | met | 100.0% |
| Category accuracy | ≥ 70% | 100.0% | met | 92.3% |
| Judge consistency (same verdict across re-runs) | ≥ 90% | 99.1% | met | - |

Where targets are missed, see the per-category table below: low-recall categories rely on the judge (no deterministic check exists for them) and low-precision ones usually come from back-translation noise.

### Precision / recall per category (held-out items)

| category | TP | FP | FN | precision | recall |
| --- | --- | --- | --- | --- | --- |
| addition | 1 | 0 | 0 | 100.0% | 100.0% |
| format | 4 | 0 | 0 | 100.0% | 100.0% |
| garbled | 1 | 0 | 0 | 100.0% | 100.0% |
| mistranslation | 2 | 0 | 0 | 100.0% | 100.0% |
| number | 2 | 0 | 0 | 100.0% | 100.0% |
| omission | 2 | 1 | 0 | 66.7% | 100.0% |
| repetition | 1 | 0 | 0 | 100.0% | 100.0% |
| speech_omission | 2 | 0 | 0 | 100.0% | 100.0% |
| term | 0 | 0 | 1 | n/a | 0.0% |
| wrong_language | 1 | 0 | 0 | 100.0% | 100.0% |

## By language

| language | items | flagged | MQM score | flags |
| --- | --- | --- | --- | --- |
| bn | 37 | 10 | 91.65 | format:2, garbled:1, mistranslation:2, number:2, omission:1, speech_omission:1, wrong_language:1 |
| hi | 37 | 17 | 89.11 | addition:3, format:3, garbled:1, mistranslation:4, number:2, omission:3, repetition:1, term:1, wrong_language:1 |
| ta | 37 | 18 | 82.47 | addition:2, format:2, garbled:2, mistranslation:2, number:2, omission:2, speech_omission:2, term:1, wrong_language:3 |

## By category and severity

Categories: addition=5, format=7, garbled=4, mistranslation=8, number=6, omission=6, repetition=1, speech_omission=3, term=2, wrong_language=5

Severities: critical=5, major=37, minor=5

## Top failures

### `s06-ta` (ta)
Source: A triangle has three sides. The sum of its interior angles is 180 degrees.
- **wrong_language** (critical, checks: judge, script) translation[0:74]=`A triangle has three sides. The sum of its interior angles is 180 degrees.`  
  translation identical to source (untranslated) || judge: The entire text is in English instead of Tamil. [quote: 'A triangle has three sides. The sum of its interior angles is 180 degrees.', agreement 100%]

### `s09-bn` (bn)
Source: Sound travels at roughly 343 m/s in air. It moves faster through water and solids.
- **wrong_language** (critical, checks: judge, script) translation[0:82]=`Sound travels at roughly 343 m/s in air. It moves faster through water and solid`  
  translation identical to source (untranslated) || judge: The entire English source text was left untranslated and remains in English. [quote: 'Sound travels at roughly 343 m/s in air. It moves faster through water and solids.', agreement 100%]

### `s17-ta` (ta)
Source: The Moon is about 384,000 km from the Earth. Astronauts first landed there in 1969.
- **wrong_language** (critical, checks: judge, script) translation[0:83]=`The Moon is about 384,000 km from the Earth. Astronauts first landed there in 19`  
  translation identical to source (untranslated) || judge: The entire text is left in English instead of being translated into Tamil. [quote: 'The Moon is about 384,000 km from the Earth. Astronauts first landed there in 1969.', agreement 100%]

### `s21-hi` (hi)
Source: Newton's second law states that $F = ma$. A force of 10 N on a 2 kg mass gives an acceleration of 5 m/s².
- **mistranslation** (major, checks: judge) translation[85:96]=`पौधे/सेकंड²`  
  judge: The unit 'm/s²' was mistranslated because 'm' (meter) was incorrectly translated as '[text]' (plants) instead of '[text]'. [quote: 'पौधे/सेकंड²', agreement 50%]
- **term** (major, checks: judge) translation[85:96]=`पौधे/सेकंड²`  
  judge: The scientific unit 'm/s²' (meters per second squared) has been mistranslated as '[text]/[text]²', where '[text]' means 'plants'. [quote: 'पौधे/सेकंड²', agreement 50%]

### `s24-ta` (ta)
Source: The quadratic formula is $$x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$$ It gives the roots of $ax^2 + bx + c = 0$.
- **wrong_language** (critical, checks: judge, script) translation[0:7]=`দ্বিঘাত`  
  only 0% of letters are in the expected script Taml || judge: The translation uses Bengali script instead of Tamil script. [quote: 'দ্বিঘাত', agreement 100%]

### `s29-hi` (hi)
Source: The train from Chennai to Madurai leaves at 6:30 in the morning. The journey takes about 8 hours.
- **wrong_language** (critical, checks: judge, script) translation[0:97]=`The train from Chennai to Madurai leaves at 6:30 in the morning. The journey tak`  
  translation identical to source (untranslated) || judge: The entire source text was left untranslated and remains in English instead of being translated into Hindi. [quote: 'The train from Chennai to Madurai leaves at 6:30 in the morning. The journey takes about 8 hours.', agreement 100%]

### `s12-hi` (hi)
Source: The school library opens at 9 a.m. and closes at 5 p.m. Students may borrow 3 books for 14 days.
- **format** (major, checks: format, judge) translation[0:123]=`स्कूल का पुस्तकालय सुबह 9 बजे खुलता **है और शाम को 5 बजे बंद हो जाता है। छात्र 1`  
  unclosed ** emphasis || judge: Unmatched markdown bold asterisks are left in the text. [quote: '**', agreement 50%]
- **omission** (minor, checks: judge) source[76:83]=`3 books`  
  judge: The information about borrowing 3 books is entirely missing from the translation. [quote: '3 books', agreement 50%]

### `s01-ta` (ta)
Source: Water boils at 100 °C at sea level. At higher altitude it boils at a lower temperature.
- **mistranslation** (major, checks: judge) translation[69:78]=`வைக்கிறது`  
  judge: The source phrase 'At higher altitude' was mistranslated as '[text]' (keeps), which changes the meaning of the sentence. [quote: 'வைக்கிறது', agreement 100%]

### `s03-bn` (bn)
Source: Photosynthesis lets plants turn sunlight, water and carbon dioxide into food. Oxygen is released as a by-product.
- **format** (major, checks: format) translation[0:128]=`**সালোকসংশ্লেষের মাধ্যমে উদ্ভিদ সূর্যের আলো, জল এবং কার্বন ডাই অক্সাইডকে খাদ্যে `  
  unclosed ** emphasis

### `s03-ta` (ta)
Source: Photosynthesis lets plants turn sunlight, water and carbon dioxide into food. Oxygen is released as a by-product.
- **omission** (major, checks: back_translation, judge, length_prior) source[0:77]=`Photosynthesis lets plants turn sunlight, water and carbon dioxide into food.`  
  translation is 0.34x the typical length for this language | judge: The entire first sentence regarding photosynthesis and how plants turn sunlight, water, and carbon dioxide into food is missing from the translation. || only 0% of source content words survive back-translation | judge: The entire first sentence regarding photosynthesis and how plants turn sunlight, water, and carbon dioxide into fo

### `s04-hi` (hi)
Source: The human heart beats about 72 times per minute at rest. It pumps nearly 5 litres of blood each minute.
- **number** (major, checks: judge, numbers) source[73:74]=`5`; translation[89:90]=`9`  
  source has 5, translation has 9 || judge: The source states '5 litres', but the translation incorrectly states '9 litres'. [quote: '9', agreement 100%]

### `s04-ta` (ta)
Source: The human heart beats about 72 times per minute at rest. It pumps nearly 5 litres of blood each minute.
- **addition** (major, checks: back_translation, judge) translation[65:120]=`இது சபர்மதியிலிருந்து டண்டி வரை 390 கிமீ தூரம் சென்றது.`  
  100% of back-translated content (['went', 'Sabarmati', 'Dandi', 'distance', '390']) has no counterpart in source | judge: The translation adds an entire sentence about traveling from Sabarmati to Dandi (referencing the Salt March) which is completely absent from the English source text. || judge: The translation adds an entire sentence about traveling from Sabarmati to Dandi (referencing the Salt 

### `s05-hi` (hi)
Source: India gained independence on 15 August 1947. The Constitution came into force on 26 January 1950.
- **garbled** (major, checks: transcribe)   
  character error rate 82% with few deletions: speech unintelligible

### `s05-ta` (ta)
Source: India gained independence on 15 August 1947. The Constitution came into force on 26 January 1950.
- **garbled** (major, checks: audio)   
  3.78s of silence mid-utterance

### `s08-ta` (ta)
Source: The Ganga flows for about 2,525 km before reaching the Bay of Bengal. Millions of people live along its banks.
- **mistranslation** (major, checks: back_translation, judge) source[4:41]=`Ganga flows for about 2,525 km before`; translation[34:51]=`டிகிரி விரிகுடாவை`  
  source words ['Ganga', 'before'] lost in back-translation | judge: The term 'Bay of Bengal' was mistranslated as 'Degree Bay' ([text] [text]) instead of 'Bengal Bay' ([text] [text]). || judge: The term 'Bay of Bengal' was mistranslated as 'Degree Bay' ([text] [text]) instead of 'Bengal Bay' ([text] [text]). [quote: 'டிகிரி விரிகுடாவை', agreement 100%]

## Regressions vs baseline

11 new flags across 10 items; 0 flags resolved. See `diff.md`.

## Cost

Uncached cost of this run: **₹0.6179** llm: ₹0.0, transcribe: ₹0.4785, translate: ₹0.1394. A rerun from cache spends ₹0 and reproduces this report byte-for-byte.

## Judge reliability

111 items judged x 2 runs. Verdict consistency 99.1%, category consistency 97.3%. 0 findings dropped because the quoted span was not found verbatim; 0 unparseable responses.

## Thresholds

Tuned from labels: {"cer_garbled": 0.8}
