# Quality report

Dataset: `outputs/demo_inj/injected.jsonl`  

## Headline

| Measure | Value |
|---|---|
| Quality score (0–100) | 87.2 |
| Items evaluated | 108 |
| Items with at least one flag | 64 (59%) |
| Verdicts | clean 44, minor 6, major 41, critical 17 |
| Items skipped at load | 0 |
| Bodhan cost | ₹0.00 |
| LLM tokens (in / out) | 0 / 0 |
| Judge consistency (verdict / flags) | 100% / 91% |

## By language

| Lang | Items | Score | Clean | minor | major | critical | omission | addition | mistranslation | number | term | format | wrong_language | speech_omission | speech_repetition | speech_garbled |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bn | 36 | 86.4 | 14 | 3 | 13 | 6 | 8 | 5 | 2 | 0 | 1 | 1 | 6 | 2 | 1 | 0 |
| hi | 36 | 88.2 | 17 | 1 | 11 | 7 | 0 | 5 | 7 | 2 | 1 | 1 | 4 | 1 | 1 | 0 |
| ta | 36 | 86.9 | 13 | 2 | 17 | 4 | 8 | 3 | 6 | 1 | 2 | 0 | 4 | 5 | 1 | 5 |

## Detection accuracy on labelled items

Mode: calibrate. Clean items flagged: 3% (budget 10%) over 39 clean items. Category accuracy: 94%. Mean span overlap (IoU): 0.88.

| Category | Labels | TP | FP | FN | Precision | Recall | F1 | Threshold |
|---|---|---|---|---|---|---|---|---|
| omission | 8 | 7 | 1 | 1 | 88% | 88% | 0.88 | 0.45 |
| addition | 13 | 13 | 0 | 0 | 100% | 100% | 1.00 | 0.50 |
| mistranslation | 9 | 8 | 0 | 1 | 100% | 89% | 0.94 | 0.45 |
| number | 3 | 3 | 0 | 0 | 100% | 100% | 1.00 | 0.50 |
| term | 8 | 3 | 0 | 5 | 100% | 38% | 0.55 | 0.50 |
| wrong_language | 14 | 14 | 0 | 0 | 100% | 100% | 1.00 | 0.50 |
| speech_omission | 5 | 5 | 0 | 0 | 100% | 100% | 1.00 | 0.50 |
| speech_repetition | 4 | 3 | 0 | 1 | 100% | 75% | 0.86 | 0.50 |
| speech_garbled | 7 | 5 | 0 | 2 | 100% | 71% | 0.83 | 0.50 |

_Thresholds were tuned on these same items, so these numbers are optimistic. Re-run held-out data with `--thresholds calibration.json`._

## Quality targets

| Check | Target | This run | Status | Why missed |
|---|---|---|---|---|
| Detection recall | ≥ 80% of labelled errors | 86% (61/71) | met |  |
| False alarms | ≤ 10% of clean items flagged | 3% | met |  |
| Localization | ≥ 70% of detections overlap the error | 98% | met |  |
| Category accuracy | ≥ 70% of detected errors | 94% | met |  |
| Judge consistency | ≥ 90% same verdict on re-judging | 100% | met |  |

_Determinism (identical report from cache at no cost) is checked by `tests/test_e2e_offline.py`; runtime is in `logs/run.jsonl`, not here, so cached re-runs stay byte-identical._

_Runtime target (200 items in 3 languages with speech in < 30 min): **likely missed** on a cold cache. Back-translation runs per sentence (Bodhan merges batched lines), and Indic-Translate allowed about 25 requests/min in our tests, so ~900 calls take ~35-40 min. Speech (~17 s per request) runs in parallel and adds little. Cached re-runs take seconds._

## Top failures (10 of 64 flagged items)

### `demo-bn-md-008__omission` (bn): major, penalty 15

- Source: | Vaccine | Protects against | Usual age | ⏎ |---|---|---| ⏎ | BCG | Tuberculosis | At birth | ⏎ | MMR | Measles, mumps, rubella | 9 to 12 months |
- **omission** (major, confidence 0.60; judge): The BCG row (vaccine, tuberculosis protection, at-birth age) is missing from the translation after the header ending at token [6]. (source: “BCG | Tuberculosis | At birth”)
  - at: … থেকে রক্ষা করে \| সাধারণ **বয়স** \| ⏎ \|---\|---\|---\| ⏎ \| এমএমআর…
- **omission** (major, confidence 0.60; backtranslation): source sentence “| BCG | Tuberculosis | At birth |” has no counterpart in the back-translation
  - at: …রক্ষা করে \| সাধারণ বয়স \| ⏎ **\|---\|---\|---\|** ⏎ \| এমএমআর \| হাম, মাম্পস, …
- **omission** (major, confidence 0.60; format): source table row “| MMR | Measles, mumps, rubella | 9 to 12 months |” has no counterpart in the translation
  - at: …ধারণ বয়স \| ⏎ \|---\|---\|---\| ⏎ **\| এমএমআর \| হাম, মাম্পস, রুবেলা \| ৯ থেকে ১২ মাস \|**

### `demo-ta-para-007__truncate` (ta): critical, penalty 15

- Source: Rabindranath's father bought a large parcel of land in Santiniketan, intending to establish a preparatory school. On November 13, 1913, Tagore won the Nobel Prize in Literature. The Swedish Academy ha…
- **speech_omission** (critical, confidence 0.90; speech): audio stops early: the last 25 words are not in the transcript (“இலக்கியத்திற்கான நோபல் பரிசை வென்றார் ஸ்வீடிஷ் அகாடமி அவரது மொழிபெயர்ப்பு படைப்ப”)
  - at: …் 13, 1913 அன்று, தாகூர் **இலக்கியத்திற்கான நோபல் பரிசை வென்றார். ஸ்வீடிஷ் அகாடமி அவரது மொழிபெயர்ப்பு படைப்புகளின் சிறிய எண்ணிக்கையையும், கீதாஞ்சலி: பாடல் காணிக்கைகள் என்ற பெயரில் 1912 இல் வெளியான கவிதைகளின் தொகுப்பையும் அடிப்படையாகக் கொண்டு அவரைத் தேர்ந்தெடுத்தது**.
  - audio: 5.5–14.2 s
- **speech_omission** (major, confidence 0.50; audio): speech lasts 14.2 s but the text should take about 26.0 s (55%); words are probably missing

### `demo-ta-md-007__term` (ta): major, penalty 11

- Source: ## How to save electricity ⏎  ⏎ 1. Switch off lights when you leave a room. ⏎ 2. Use LED bulbs instead of old bulbs. ⏎ 3. Unplug chargers that are not in use.
- **term** (major, confidence 0.78; backtranslation, judge): Token [13] transliterates 'LED' as an unrelated word ('Rine'), so the bulb type is wrong and the instruction is unclear. (source: “LED”)
  - at: …பழைய பல்புகளுக்கு பதிலாக **ரைன்** பல்புகள். ⏎ 3. பயன்பாட்டில…
- **mistranslation** (major, confidence 0.50; backtranslation): source “Unplug chargers that are not in use.” back-translates as “Remove unused chargers from the plug.”
  - at: …பதிலாக ரைன் பல்புகள். ⏎ 3. **பயன்பாட்டில் இல்லாத சார்ஜர்களை பிளக்கிலிருந்து அகற்றவும்.**
- **omission** (minor, confidence 0.45; judge): The imperative verb 'Use' has no counterpart in tokens [10]-[12], so the instruction to use the bulbs is not stated. (source: “Use”)
  - at: …ு விளக்குகளை அணைக்கவும். ⏎ **2**. பழைய பல்புகளுக்கு பதிலா…

### `demo-ta-para-019__addition` (ta): major, penalty 11

- Source: The Internet is used for many things, such as electronic mail, online chat, file transfer and other documents of the World Wide Web. The most used service on the Internet is the World Wide Web (which …
- **speech_omission** (major, confidence 0.55; speech): 5 words are missing from the transcript (“வலை அல்லது www என்றும் அழைக்கப்படுகிறது”)
  - at: …ர்ல்டு வைடு வெப் ஆகும் ("**வலை" அல்லது "www" என்றும் அழைக்கப்படுகிறது**). வலைத்தளங்கள், சமூக ஊடக…
  - audio: 14.2–17.3 s
- **addition** (major, confidence 0.82; backtranslation, judge): Tokens [27]-[34] add a sentence about a glacier flowing slowly, which has no counterpart in the source.
  - at: …்றும் அழைக்கப்படுகிறது). **பனிப்பாறை நீர் போல கீழே பாய்கிறது, ஆனால் மிக மெதுவாக**. வலைத்தளங்கள், சமூக ஊடகங…
- **omission** (minor, confidence 0.45; judge): The phrase 'other documents of the World Wide Web' is not rendered; token [9] is followed directly by the list of examples without it. (source: “other documents of the World Wide Web”)
  - at: …ம் மற்றும் வேர்ல்டு வைடு **வெப்** போன்ற பல விஷயங்களுக்கு ப…

### `demo-bn-md-005__wrong_language` (bn): critical, penalty 10

- Source: | Month | Rainfall in Mumbai (mm) | ⏎ |---|---| ⏎ | June | 500 | ⏎ | July | 840 | ⏎ | August | 580 | ⏎ | September | 330 |
- **wrong_language** (critical, confidence 0.99; judge, script): translation is identical to the English source (left untranslated)
  - at: **\| Month \| Rainfall in Mumbai (mm) \| ⏎ \|---\|---\| ⏎ \| June \| 500 \| ⏎ \| July \| 840 \| ⏎ \| August \| 580 \| ⏎ \| September \| 330 \|**

### `demo-bn-para-020__addition` (bn): major, penalty 10

- Source: In 1866, a national Olympic Games in Great Britain was organized by Dr. The Panathinaiko Stadium hosted Olympics in 1870 and 1875.
- **omission** (major, confidence 0.60; judge): The organizer 'Dr.' named in the source is missing after token [10]. (source: “organized by Dr.”)
  - at: …িম্পিক গেমসের আয়োজন করা **হয়** এবং ১৮৭০ ও ১৮৭৫ সালে প্য…
- **addition** (major, confidence 0.82; backtranslation, judge): Tokens [21]-[27] add a claim about three Olympic Games not being held due to war, which has no counterpart in the source.
  - at: …ম অলিম্পিকের আয়োজন করে। **যুদ্ধের কারণে তিনটি অলিম্পিক গেমস অনুষ্ঠিত হয়নি**।

### `demo-bn-para-035__addition` (bn): major, penalty 10

- Source: One of the biggest and most well-known water fights is over the Colorado River. This river flows through seven states: Colorado, Wyoming, Utah, New Mexico, Arizona, Nevada, and California.
- **format** (major, confidence 0.70; format): math expression count differs: source 0, translation 1
  - at: **সবচেয়ে বড় এবং সবচেয়ে পরিচিত জলযুদ্ধগুলির মধ্যে একটি হল কলোরাডো নদী নিয়ে। এই নদীটি সাতটি রাজ্যের মধ্য দিয়ে প্রবাহিত হয়: কলোরাডো, ওয়াইওমিং, ইউটা, নিউ মেক্সিকো, অ্যারিজোনা, নেভাডা এবং ক্যালিফোর্নিয়া। বৃত্তের ক্ষেত্রফল হল $A = \pi r^2$।**
- **addition** (major, confidence 0.82; backtranslation, judge): Tokens [29]-[35] add a sentence about the area of a circle with a formula, which has no counterpart in the source.
  - at: …াডা এবং ক্যালিফোর্নিয়া। **বৃত্তের ক্ষেত্রফল হল $A = \pi r^2**$।

### `demo-bn-para-041__truncate` (bn): critical, penalty 10

- Source: Organised research about space in India was led by two scientists: Vikram Sarabhai and Homi Bhaba. Bhaba was the secretary of the Department of Atomic Energy when it was founded in 1950.
- **speech_omission** (critical, confidence 0.80; speech): audio stops early: the last 9 words are not in the transcript (“শক্তি বিভাগ প্রতিষ্ঠিত হয়েছিল তখন ভাবা ছিলেন এর সচিব”)
  - at: … ১৯৫০ সালে যখন পারমাণবিক **শক্তি বিভাগ প্রতিষ্ঠিত হয়েছিল তখন ভাবা ছিলেন এর সচিব**।
  - audio: 8.8–12.6 s

### `demo-bn-short-002__addition` (bn): critical, penalty 10

- Source: Blood is a liquid in humans and many animals.
- **addition** (critical, confidence 0.86; backtranslation, judge): Tokens [8]-[15] add an unrelated sentence claiming more than 90% of people can read this language, which is not in the source.
  - at: …ং অনেক প্রাণীর একটি তরল। **৯০% এরও বেশি মানুষ এই ভাষাটি পড়তে পারে**।

### `demo-bn-short-005__addition` (bn): critical, penalty 10

- Source: Diwali (also: Deepawali) is one of India's biggest festivals.
- **addition** (critical, confidence 0.86; backtranslation, judge): Tokens [7]-[33] describe a space mission landing at coordinates with dates and craters, which has no basis in the source sentence about Diwali and replaces the intended content.
  - at: …র অন্যতম বৃহত্তম উৎসব। - **এই মিশনটি সেখানে অবতরণে ব্যর্থ হয়, তবে ২০২৩ সালের ২৩ আগস্ট ৬৯.৩৬৭৬২১°দক্ষিণ ৩২.৩৪৮১২৬°পূর্ব অক্ষাংশে অবতরণ করে যা ম্যানজিনাস সি এবং সিম্পেলিয়াস এন জ্বালামুখের মধ্যে অবস্থিত**।

## Cost

| Service | Paid calls | Cache hits | Units | ₹ |
|---|---|---|---|---|
| llm | 0 | 141 | 0.0 | 0.0000 |
| transcribe | 0 | 58 | 0.0 | 0.0000 |
| translate | 0 | 256 | 0.0 | 0.0000 |

Units: translate = output tokens, speak = characters, transcribe = seconds, llm = tokens (priced only if LLM_PRICE_INR_PER_M_* is set).

## Files

- `items.jsonl`: per item, final flags, all candidates and every check's evidence.
- `summary.json`: the numbers above, machine-readable.
- `calibration.json`, `judge_consistency.json`, `diff.md`: when applicable.
- `logs/run.jsonl`: one JSON event per step, including skipped steps and API timings.
