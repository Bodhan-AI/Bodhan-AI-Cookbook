# Quality harness report

Dataset `demo.jsonl`: **138** items evaluated (bn, hi, ta).

## Headline vs targets

Basis: **held-out split** (Wilson 95% intervals; detection definitions at the end).

| metric | target | value [95% CI] (k/n) | status |
|---|---|---|---|
| Detection recall (items) | >= 80.0% | 84.0% [65.3%-93.6%] (21/25) | met (CI spans target) |
| False alarms, strict | <= 10.0% | 5.3% [0.9%-24.6%] (1/19) | met (CI spans target) |
| False alarms, reviewed | <= 10.0% | 5.3% [0.9%-24.6%] (1/19) | met (CI spans target) |
| Localization (span overlap) | >= 70.0% | 100.0% [85.1%-100.0%] (22/22) | met |
| Category accuracy | >= 70.0% | 86.4% [66.7%-95.2%] (19/22) | met (CI spans target) |
| Judge consistency (same verdict) | >= 90.0% | 85.0% [64.0%-94.8%] (17/20) | **MISSED** (CI spans target) |

Why targets were missed:
- Judge consistency: same verdict per language: bn 4/7, hi 7/7, ta 6/6; disagreements are judge-only flags that fusion already sends to the review queue.

With samples this small, the intervals are wide: e.g. about 50 items cannot tell 80% from 70% recall.

## Per language (all labelled items)

| lang | items | recall | precision | false alarms strict | false alarms reviewed | localization | category acc. |
|---|---:|---|---|---|---|---|---|
| bn | 46 | 79.2% [59.5%-90.8%] (19/24) | 100.0% [83.2%-100.0%] (19/19) | 0.0% [0.0%-14.9%] (0/22) | 0.0% [0.0%-14.9%] (0/22) | 95.0% [76.4%-99.1%] (19/20) | 95.0% [76.4%-99.1%] (19/20) |
| hi | 46 | 91.7% [74.2%-97.7%] (22/24) | 100.0% [85.1%-100.0%] (22/22) | 4.3% [0.8%-21.0%] (1/23) | 0.0% [0.0%-14.9%] (0/22) | 91.7% [74.2%-97.7%] (22/24) | 95.8% [79.8%-99.3%] (23/24) |
| ta | 46 | 84.6% [66.5%-93.8%] (22/26) | 95.7% [79.0%-99.2%] (22/23) | 9.5% [2.6%-28.9%] (2/21) | 5.0% [0.9%-23.6%] (1/20) | 95.5% [78.2%-99.2%] (21/22) | 81.8% [61.5%-92.7%] (18/22) |

## Per category (all labelled items)

| category | labels | recall | precision | localization | mean IoU | category acc. |
|---|---:|---|---|---|---:|---|
| addition | 8 | 87.5% [52.9%-97.8%] (7/8) | 85.7% [48.7%-97.4%] (6/7) | 87.5% [52.9%-97.8%] (7/8) | 0.62 | 75.0% [40.9%-92.8%] (6/8) |
| format | 13 | 100.0% [77.2%-100.0%] (13/13) | 100.0% [77.2%-100.0%] (13/13) | 100.0% [77.2%-100.0%] (13/13) | 1.00 | 100.0% [77.2%-100.0%] (13/13) |
| mistranslation | 4 | 0.0% [0.0%-49.0%] (0/4) | 0.0% [0.0%-43.5%] (0/5) | n/a (n=0) | n/a | n/a (n=0) |
| number_unit | 8 | 100.0% [67.6%-100.0%] (8/8) | 87.5% [64.0%-96.5%] (14/16) | 100.0% [67.6%-100.0%] (8/8) | 1.00 | 100.0% [67.6%-100.0%] (8/8) |
| omission | 8 | 75.0% [40.9%-92.8%] (6/8) | 100.0% [61.0%-100.0%] (6/6) | 100.0% [61.0%-100.0%] (6/6) | 0.00 | 100.0% [61.0%-100.0%] (6/6) |
| speech_garbled | 8 | 100.0% [67.6%-100.0%] (8/8) | 100.0% [74.1%-100.0%] (11/11) | 100.0% [67.6%-100.0%] (8/8) | 0.98 | 100.0% [67.6%-100.0%] (8/8) |
| speech_omission_truncation | 8 | 50.0% [21.5%-78.5%] (4/8) | 100.0% [51.0%-100.0%] (4/4) | 80.0% [37.5%-96.4%] (4/5) | 0.00 | 80.0% [37.5%-96.4%] (4/5) |
| speech_repetition | 8 | 50.0% [21.5%-78.5%] (4/8) | 85.7% [48.7%-97.4%] (6/7) | 66.7% [30.0%-90.3%] (4/6) | 0.23 | 100.0% [61.0%-100.0%] (6/6) |
| term_entity | 4 | 100.0% [51.0%-100.0%] (4/4) | 100.0% [20.6%-100.0%] (1/1) | 100.0% [51.0%-100.0%] (4/4) | 0.58 | 25.0% [4.6%-69.9%] (1/4) |
| wrong_language_script | 8 | 100.0% [67.6%-100.0%] (8/8) | 88.9% [56.5%-98.0%] (8/9) | 100.0% [67.6%-100.0%] (8/8) | 1.00 | 100.0% [67.6%-100.0%] (8/8) |

These tables include the calibration train split (in-sample for the judge cutoff); the headline uses held-out items only.
Recall counts only strong (corroborated) flags; weak or review-queue signals are never counted as recall.

## Counted flags

| category | flags |
|---|---:|
| addition | 7 |
| format | 13 |
| mistranslation | 5 |
| number_unit | 16 |
| omission | 6 |
| speech_garbled | 11 |
| speech_omission_truncation | 4 |
| speech_repetition | 7 |
| term_entity | 1 |
| wrong_language_script | 9 |

| lang | items | items flagged |
|---|---:|---:|
| bn | 46 | 19 |
| hi | 46 | 22 |
| ta | 46 | 23 |

## Top failures

### `d106` (hi), MQM penalty 26, labels: term_entity (injector)

- Source: During his 2 hour speech he stated that "Today Apple is going to reinvent the phone, We are going to make history today".
- **term_entity** / critical by `llm_judge` at chars 46-52 (confidence 0.99; corroborated by back_translation): Wrong entity, Apple misrepresented as Konami
  - Translation: अपने 2 घंटे के भाषण के दौरान उन्होंने कहा कि "[[कोनामी]] फोन को फिर से बनाने जा रहा है, हम आज इतिहास रचने जा रहे हैं...
- **mistranslation** / minor by `llm_judge` at chars 60-72 (confidence 0.80; corroborated by back_translation): Meaning differs from 'reinvent'
  - Translation: अपने 2 घंटे के भाषण के दौरान उन्होंने कहा कि "कोनामी फोन को [[फिर से बनाने]] जा रहा है, हम आज इतिहास रचने जा रहे हैं"।

### `d077` (bn), MQM penalty 25, labels: wrong_language_script (injector)

- Source: The building housed a number of pilgrims who came to visit the holy city at the eve of hajj pilgrimage. The hostel's guests were mostly citizens of the United Arab Emirates.
- **wrong_language_script** / critical by `script` at chars 0-173 (confidence 0.99): Translation is identical to the English source (untranslated; expected Bengali).
  - Translation: [[The building housed a number of pilgrims who came to visit the holy city at the eve of hajj pilgrimage. The hostel's guests were mostly citizens of the United Arab Emirates.]]

### `d123` (bn), MQM penalty 25, labels: wrong_language_script (injector)

- Source: European Turkey (eastern Thrace or Rumelia in the Balkan peninsula) includes 3% of the country. The territory of Turkey is more than 1,600 kilometres (1,000 mi) long and 800 km (500 mi) wide, with a roughly rectangular shape.
- **wrong_language_script** / critical by `script` at chars 0-225 (confidence 0.99): Translation is identical to the English source (untranslated; expected Bengali).
  - Translation: [[European Turkey (eastern Thrace or Rumelia in the Balkan peninsula) includes 3% of the country. The territory of Turkey is more than 1,600 kilometres (1,000 mi) long and 800 km (500 mi) wide, with a roughly rectangular shape.]]

### `d023` (hi), MQM penalty 25, labels: wrong_language_script (injector)

- Source: ## Daily water intake  - Children need about 1.5 litres of water each day. - Adults need between 2 and 3 litres. - People who exercise in hot weather need more.  Drink water before you feel thirsty.
- **wrong_language_script** / critical by `script` at chars 0-198 (confidence 0.99): Translation is identical to the English source (untranslated; expected Hindi).
  - Translation: [[## Daily water intake  - Children need about 1.5 litres of water each day. - Adults need between 2 and 3 litres. - People who exercise in hot weather need more.  Drink water before you feel thirsty.]]

### `d059` (hi), MQM penalty 25, labels: wrong_language_script (injector)

- Source: Singapore is generally an extremely safe place to be and very easy to navigate, and you can buy almost anything after arriving.
- **wrong_language_script** / critical by `script` at chars 0-127 (confidence 0.99): Translation is identical to the English source (untranslated; expected Hindi).
  - Translation: [[Singapore is generally an extremely safe place to be and very easy to navigate, and you can buy almost anything after arriving.]]

### `d061` (hi), MQM penalty 25, labels: wrong_language_script (injector)

- Source: The building housed a number of pilgrims who came to visit the holy city at the eve of hajj pilgrimage. The hostel's guests were mostly citizens of the United Arab Emirates.
- **wrong_language_script** / critical by `script` at chars 0-173 (confidence 0.99): Translation is identical to the English source (untranslated; expected Hindi).
  - Translation: [[The building housed a number of pilgrims who came to visit the holy city at the eve of hajj pilgrimage. The hostel's guests were mostly citizens of the United Arab Emirates.]]

### `d114` (hi), MQM penalty 25, labels: wrong_language_script (injector)

- Source: European Turkey (eastern Thrace or Rumelia in the Balkan peninsula) includes 3% of the country. The territory of Turkey is more than 1,600 kilometres (1,000 mi) long and 800 km (500 mi) wide, with a roughly rectangular shape.
- **wrong_language_script** / critical by `script` at chars 0-225 (confidence 0.99): Translation is identical to the English source (untranslated; expected Hindi).
  - Translation: [[European Turkey (eastern Thrace or Rumelia in the Balkan peninsula) includes 3% of the country. The territory of Turkey is more than 1,600 kilometres (1,000 mi) long and 800 km (500 mi) wide, with a roughly rectangular shape.]]

### `d031` (ta), MQM penalty 25, labels: wrong_language_script (injector)

- Source: ## Checking a file  Run the command below to count the lines in a file. It prints a single number.  ```bash wc -l notes.txt ```  If the file is empty, the command prints 0.
- **wrong_language_script** / critical by `script` at chars 0-174 (confidence 0.95): Output is mostly Bengali script; only 0% of letters are Tamil (expected Tamil).
  - Translation: [[## একটি ফাইল পরীক্ষা করা  একটি ফাইলের লাইন গণনা করতে নিচের কমান্ডটি চালান। এটি একটি সংখ্যা প্রিন্ট করে।  ```bash wc -l notes.txt ```  ফাইলটি খালি হলে, কমান্ডটি 0 প্রিন্ট করে।]]

### `d119` (ta), MQM penalty 25, labels: wrong_language_script (injector)

- Source: U.S. President George W. Bush arrived in Singapore the morning of November 16, beginning a week-long tour of Asia. The amount of inner peace a person possesses correlates oppositely to the amount of tension in one’s body and spirit.
- **wrong_language_script** / critical by `script` at chars 0-232 (confidence 0.99): Translation is identical to the English source (untranslated; expected Tamil).
  - Translation: [[U.S. President George W. Bush arrived in Singapore the morning of November 16, beginning a week-long tour of Asia. The amount of inner peace a person possesses correlates oppositely to the amount of tension in one’s body and spirit.]]

### `d093` (ta), MQM penalty 15, labels: format (injector)

- Source: ## Kinetic energy  The kinetic energy of a moving object is $E = \frac{1}{2} m v^2$, where $m$ is its mass and $v$ is its speed. Doubling the speed makes the energy four times larger.
- **format** / major by `format` at chars 17-193 (confidence 0.90): Broken structure on line 'நகரும் பொருளின் இயக்க ஆற்றல் E = \frac{1}{2} m v^2$ ஆகும், இ': dollar: source 6 vs translation 5; math_braces: source (0, 0, 0) vs translation (0, 0).
  - Translation: ## இயக்க ஆற்றல்  [[நகரும் பொருளின் இயக்க ஆற்றல் E = \frac{1}{2} m v^2$ ஆகும், இதில் $m$ என்பது அதன் நிறை மற்றும் $v$ என்பது அதன் வேகம். வேகத்தை இரட்டிப்பாக்கினால் ஆற்றல் நான்கு மடங்கு அதிகமாகும்.]]
- **wrong_language_script** / major by `script` at chars 46-65 (confidence 0.85): 4 consecutive English words left untranslated: 'E = \frac{1}{2} m v'.
  - Translation: ## இயக்க ஆற்றல்  நகரும் பொருளின் இயக்க ஆற்றல் [[E = \frac{1}{2} m v]]^2$ ஆகும், இதில் $m$ என்பது அதன் நிறை மற்றும் $v$ என்பது அதன...
- **number_unit** / major by `numbers` at chars 66-68 (confidence 0.80): Unit '$' after 2 in the translation is not used in the source (source units: none).
  - Translation: ...்க ஆற்றல்  நகரும் பொருளின் இயக்க ஆற்றல் E = \frac{1}{2} m v^[[2$]] ஆகும், இதில் $m$ என்பது அதன் நிறை மற்றும் $v$ என்பது அதன் வ...

## Missed errors (labelled, no counted flag)

- `d010` (bn): speech_omission_truncation: The audio was cut off here; the last 0.69 s of speech are missing.. Weak signals: asr_roundtrip:speech_garbled, asr_roundtrip:speech_omission_truncation; judge: ok
- `d035` (bn): omission: A clause was deleted from the translation at this point (32 characters).. Weak signals: back_translation:omission; judge: ok
- `d036` (bn): speech_omission_truncation: The audio was cut off here; the last 1.29 s of speech are missing.. Weak signals: asr_roundtrip:speech_garbled, asr_roundtrip:speech_omission_truncation; judge: ok
- `d067` (bn): speech_repetition: A 1.24 s stretch of speech is repeated here.. Weak signals: asr_roundtrip:speech_garbled, asr_roundtrip:speech_repetition; judge: ok
- `d134` (bn): speech_omission_truncation: The audio was cut off here; the last 1.84 s of speech are missing.. Weak signals: asr_roundtrip:speech_garbled, asr_roundtrip:speech_omission_truncation; judge: ok
- `d068` (hi): mistranslation: Meaning changed: the output says 'Before' where the source says 'After'.. Weak signals: llm_judge:mistranslation, llm_judge:omission; judge: errors
- `d128` (hi): mistranslation: Meaning changed: the output says 'above' where the source says 'below'.. Weak signals: llm_judge:mistranslation; judge: errors
- `d055` (ta): mistranslation: Meaning changed: the output says 'higher' where the source says 'lower'.. Weak signals: llm_judge:mistranslation; judge: errors

## Findings for the Bodhan team

Counted flags on outputs that carry no seeded error (candidate genuine model defects):

- **addition / llm_judge**: 1 item(s) (d100). Example `d100` (ta) at chars 0-13: Translation adds 'committee', not present in source.
  - Translation: [[விசாரணை நடத்த]] ஒரு விசாரணைக்குழு அமைக்கப்பட்டது.
- **format / format**: 2 item(s) (d034, d126). Example `d034` (hi) at chars 0-10: Broken structure on line 'गतिज ऊर्जा': heading: source 2 vs translation 0.
  - Translation: [[गतिज ऊर्जा]]  किसी गतिमान वस्तु की गतिज ऊर्जा $E = \frac{1}{2} m v^2$ है...

Defects in un-seeded Bodhan outputs confirmed by reading the text (labels with source `reviewed_preexisting`):

- format: Bodhan dropped the '###' heading marker; the heading renders as plain text. (d053, d083, d126)
- format: Bodhan dropped the '##' heading marker; the heading renders as plain text. (d034)
- format: Bodhan transliterated the inline code `brake`; code must be kept unchanged. (d026)

## Judge (tier-3)

- The LLM judge was available for every item it was asked to read.
- Status counts: ran 106, skipped:tier1_unambiguous 32
- Valid JSON: 100.0% [96.5%-100.0%] (106/106); answers needing the repair retry: 0
- Quotes found verbatim in the translation: 100.0% [92.1%-100.0%] (45/45)
- Tokens (judge pass): 132010; review-queue flags (judge-only or quote not found): 30
- Consistency re-run on 20 items: same verdict 85.0% [64.0%-94.8%] (17/20), same category set 75.0% [53.1%-88.8%] (15/20), failed 0, tokens 25137
- Same counted judge flags after fusion (what the report actually counts): 100.0% [83.9%-100.0%] (20/20)

## Cost and cache

| service | calls | units | INR (cached-equivalent) |
|---|---:|---:|---:|
| llm | 126 | 157147.0 tokens | 0.0000 |
| speak | 0 | 0.0 input_chars | 0.0000 |
| transcribe | 66 | 367.9 audio_seconds | 0.6700 |
| translate | 138 | 4104.0 output_tokens | 0.0821 |

Total model cost of this evaluation if nothing were cached: **INR 0.7521** (the tier-3 judge is priced at 0).
The new-vs-cached split, cache hit counts and runtime are in `run_meta.json`; they are kept out of the report so a cached rerun is byte-identical.

## Bad, skipped and failed items

- Bad rows skipped: 0; filtered out by `--langs`: 0
- Items whose processing failed: 0
- Items whose audio file is missing (speech checks skipped): 0
- Labels dropped on their own (row kept): 0; calibration ids not in this run: 0
- Check `asr_roundtrip: skipped`: 72
- Check `audio_duration: skipped`: 72
- Check `audio_sanity: skipped`: 72
- Check `reference_chrf: skipped`: 24

## Caveats

- **No human labels on seeded errors.** 5 `reviewed_preexisting` labels were added by the recipe author after reading the output (real defects in un-seeded items) and are tagged with that source. Labels used: injector 72, reviewed_preexisting 5.
- Synthetic labels are programmatic edits (deletions, splices, digit and name swaps, audio cuts, loops and noise). They are likely **easier** to detect than real model errors, so recall here is an optimistic estimate for real errors.
- Thresholds are tuned on the train split only; the headline is held-out. Run statistics (length, speaking rate, noise floor) are relative to this run.

## Definitions

- Detection recall: error items with at least one counted flag / error items. Counted = not `needs_corroboration` (review-queue flags never count).
- False alarms, strict: flagged items / items without seeded or human labels (items with only `reviewed_preexisting` labels count as clean).
- False alarms, reviewed: flagged items / items with no labels at all.
- Localization: labels with a span on detected items where a counted flag overlaps the span (same unit: chars or seconds).
- Category accuracy: labels on detected items where a counted flag has the label's category.
- Judge consistency: same verdict when the identical judge request is sampled a second time.
