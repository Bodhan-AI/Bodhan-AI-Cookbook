# Quality report

- Items evaluated: **126** (bad lines skipped: 0, other languages filtered: 0, item errors: 0)
- Items flagged: **69**
- Model cost of all outputs evaluated: ₹0.72 (see run.json for money spent in this run)

## Per language

| lang | items | flagged | MQM | ref chrF++ | BT chrF++ | speech checked | speech CER | top categories |
|---|---|---|---|---|---|---|---|---|
| bn | 42 | 20 | 83.05 | – | 75.22 | 10 | 0.1903 | number 5, addition 3, format 3 |
| hi | 42 | 25 | 78.81 | – | 76.13 | 13 | 0.4869 | number 9, speech_repetition 4, omission 3 |
| ta | 42 | 24 | 78.21 | – | 73.43 | 15 | 0.4777 | number 6, format 5, addition 4 |

## Flags per category

| category | bn | hi | ta |
|---|---|---|---|
| omission | 0 | 3 | 2 |
| addition | 3 | 1 | 4 |
| mistranslation | 2 | 2 | 1 |
| number | 5 | 9 | 6 |
| term | 2 | 2 | 4 |
| format | 3 | 1 | 5 |
| wrong_language | 3 | 3 | 2 |
| speech_omission | 2 | 3 | 3 |
| speech_repetition | 1 | 4 | 2 |
| speech_garbled | 2 | 2 | 4 |

## Quality targets

Measured on held-out half (thresholds tuned on the other half).

| metric | value | target | met | why missed |
|---|---|---|---|---|
| detection_recall | 1.0 | >= 0.8 | yes |  |
| false_alarm_rate | 0.056 | <= 0.1 | yes |  |
| localization | 1.0 | >= 0.7 | yes |  |
| category_accuracy | 0.933 | >= 0.7 | yes |  |
| judge_consistency | 1.0 | >= 0.9 | yes |  |

Per language (all labelled items):

| lang | seeded errors | clean | recall | false alarms | localization | category accuracy |
|---|---|---|---|---|---|---|
| bn | 7 | 11 | 1.0 | 0.0 | 1.0 | 1.0 |
| hi | 19 | 6 | 1.0 | 0.0 | 0.947 | 1.0 |
| ta | 10 | 12 | 0.9 | 0.083 | 1.0 | 1.0 |

Per category (item level, all labelled items):

| category | precision | recall | threshold |
|---|---|---|---|
| omission | 1.0 | 1.0 | 0.95 |
| addition | 0.667 | 1.0 | 0.6 |
| mistranslation | 1.0 | 0.8 | 0.875 |
| number | 0.286 | 1.0 | 0.6 |
| term | 0.8 | 1.0 | 0.45 |
| format | 0.667 | 1.0 | 0.6 |
| wrong_language | 1.0 | 1.0 | 0.6 |
| speech_omission | 1.0 | 1.0 | 0.708 |
| speech_repetition | 1.0 | 1.0 | 0.5 |
| speech_garbled | 1.0 | 1.0 | 0.36 |

## Human-reviewed flags

22 flags were judged by a person (human:deep-karia (22)): 14 real errors, 8 false alarms. These come from the review queue, i.e. flags near their threshold, so this sample is harder than average.

Harness flag precision **0.625**, recall **0.714**, agreement with the person **0.545**.
Of the 4 real errors not flagged in their own category, 1 were flagged under another category (for example a dropped number caught as an omission).

| category | real errors flagged | false alarms | missed | cleared correctly |
|---|---|---|---|---|
| addition | 0 | 2 | 1 | 1 |
| format | 2 | 0 | 0 | 1 |
| mistranslation | 3 | 0 | 1 | 0 |
| number | 2 | 2 | 0 | 0 |
| omission | 2 | 0 | 2 | 0 |
| term | 1 | 1 | 0 | 0 |
| wrong_language | 0 | 1 | 0 | 0 |

Each check on its own (raw candidates, before merging and thresholds):

| check | category | precision | recall |
|---|---|---|---|
| audio_signal | speech_garbled | 1.0 | 1.0 |
| back_transcription | speech_omission | 0.5 | 1.0 |
| back_transcription | speech_repetition | 1.0 | 1.0 |
| backtranslation | addition | 0.667 | 1.0 |
| backtranslation | omission | 0.5 | 1.0 |
| entities | term | 0.5 | 0.25 |
| format | format | 0.667 | 1.0 |
| judge | addition | 1.0 | 1.0 |
| judge | format | 1.0 | 1.0 |
| judge | mistranslation | 0.625 | 1.0 |
| judge | number | 0.667 | 1.0 |
| judge | omission | 0.667 | 1.0 |
| judge | term | 1.0 | 0.75 |
| judge | wrong_language | 1.0 | 1.0 |
| length_ratio | addition | 1.0 | 1.0 |
| length_ratio | omission | 1.0 | 1.0 |
| numbers | number | 0.286 | 1.0 |
| script | wrong_language | 1.0 | 1.0 |
| speech_rate | speech_omission | 1.0 | 0.25 |

## Findings by category

What to look at first: count, languages affected, and two examples each.

| category | flags | languages | examples |
|---|---|---|---|
| number | 20 | hi 9, ta 6, bn 5 | `hea-004.hi`: [judge] explanation=The source says 1 litre of water, but the translation says 8 litres. This is a dangerous error for an ORS recipe.<br>`civ-003.bn`: [numbers] translation_number=542; source_numbers=['250', '552']; missing_from_translation=['552'] |
| format | 9 | ta 5, bn 3, hi 1 | `math-001.bn`: [format] problem=LaTeX $ delimiter count differs (8 in source, 0 in translation)<br>`md-001.bn`: [format] problem=table rows or columns differ; source_rows=5; translation_rows=5; translation_pipes=[3, 3, 3, 3, 2] |
| addition | 8 | ta 4, bn 3, hi 1 | `hea-005.hi`: [judge] explanation=The translation adds an unrelated sentence about ice melting and floating on water that is not in the source. It is also<br>`edu-003.ta`: [judge] explanation=The final sentence about the area of a circle (radius, 154 square centimeters) does not appear in the source. It is extr |
| speech_garbled | 8 | ta 4, hi 2, bn 2 | `math-001.hi`: [audio_signal] problem=2.28s silence inside speech; at_s=5.74<br>`md-004.ta`: [audio_signal] problem=0.70s of noise-like audio; at_s=10.56 |
| speech_omission | 8 | hi 3, ta 3, bn 2 | `geo-002.bn`: [back_transcription] not_heard=যা বছরে প্রায় ১১,৮০০ মিলিমিটার; missing_chars=20; truncated_end=True<br>`hea-002.bn`: [back_transcription] not_heard=াসের মধ্যে দ্বিতীয় ডোজ দেওয়া উচিত।; heard_instead=চোখ; missing_chars=29; truncated_end=True |
| term | 8 | ta 4, bn 2, hi 2 | `civ-001.bn`: [judge] explanation="The Constitution of India" is rendered as "‹…›" ("Prize of India"), which is wrong. It should be "‹…›".<br>`fin-003.hi`: [judge] explanation="Reserve Bank" is rendered as "‹…›" (Desert Bank). The correct Hindi is "‹…›", so the institution's name is wrong. |
| wrong_language | 8 | bn 3, hi 3, ta 2 | `fin-003.bn`: [script] expected_script=BENGALI; found_script=TAMIL; foreign_word_share=1.0<br>`geo-005.bn`: [script] expected_script=BENGALI; found_script=TAMIL; foreign_word_share=1.0 |
| speech_repetition | 7 | hi 4, ta 2, bn 1 | `fin-002.bn`: [back_transcription] repeated_text=করবেন না এমনকি যদি ফোনকারী বলে যে তারা ব্যাঙ্কে কাজ করে; heard=করবেন না এমনকি যদি ফোনকারী বলে যে তারা ব্য<br>`agr-003.hi`: [back_transcription] repeated_text=उर्वरक डालने में मदद मिलती है जिसकी फसल को आवश्यकता होती है; heard=उर्वरक डालने में मदद मिलती है जिसकी फस |
| mistranslation | 5 | bn 2, hi 2, ta 1 | `math-001.bn`: [judge] explanation=The source says the angle sum is always 180°. The translation says it is never 180° ("‹…› ... ‹…›"), which reverses the <br>`sci-010.bn`: [judge] explanation="strong" is translated as "‹…›" (weak/poor). This reverses the meaning: copper is a strong conductor, and the translatio |
| omission | 5 | hi 3, ta 2 | `fin-001.hi`: [judge] explanation=The translation stops after the deposit amount. It omits the 7 percent annual interest rate, the ₹700 earned, and the fi<br>`fin-002.ta`: [judge] explanation=The main instruction is missing: 'Never share your bank PIN or one-time password with anyone'. The translation only has  |

## Top failures

| item | lang | category | severity | where | evidence |
|---|---|---|---|---|---|
| fin-003.bn | bn | wrong_language | critical | இந்திய ரிசர்வ் வங்கி 1 ஏப்ரல் 1935 அன்று நிறுவப்பட்டது, இதன்… | [script] expected_script=BENGALI; found_script=TAMIL; foreign_word_share=1.0 |
| geo-005.bn | bn | wrong_language | critical | 8,586 மீட்டர் உயரமுள்ள கஞ்சன்ஜங்கா இந்தியாவின் மிக உயர்ந்த ம… | [script] expected_script=BENGALI; found_script=TAMIL; foreign_word_share=1.0 |
| hea-001.bn | bn | wrong_language | critical | சாப்பிடுவதற்கு முன்பும் கழிப்பறையைப் பயன்படுத்திய பின்பும் க… | [script] expected_script=BENGALI; found_script=TAMIL; foreign_word_share=1.0 |
| fin-001.hi | hi | omission | critical |  | [judge] explanation=The translation stops after the deposit amount. It omits the 7 percent annual interest rate, the ₹700 earned, and the first-year timeframe, so the main statement is missing. |
| geo-002.hi | hi | wrong_language | critical | মেঘালয়ের মাউসিনরামে বিশ্বের অন্যতম সর্বোচ্চ বৃষ্টিপাত হয়, … | [script] expected_script=DEVANAGARI; found_script=BENGALI; foreign_word_share=1.0 |
| hea-004.hi | hi | number | critical | 8 | [judge] explanation=The source says 1 litre of water, but the translation says 8 litres. This is a dangerous error for an ORS recipe. |
| fin-002.ta | ta | omission | critical |  | [judge] explanation=The main instruction is missing: 'Never share your bank PIN or one-time password with anyone'. The translation only has the concessive clause, so the sentence is incomplete and the warning is lost. |
| hea-004.ta | ta | wrong_language | critical | ओरल रिहाइड्रेशन सॉल्यूशन (oral rehydration solution) बनाने क… | [script] expected_script=TAMIL; found_script=DEVANAGARI; foreign_word_share=1.0 |
| math-001.bn | bn | mistranslation | critical | কখনই ১৮০ ডিগ্রী হয় না, | [judge] explanation=The source says the angle sum is always 180°. The translation says it is never 180° ("‹…› ... ‹…›"), which reverses the meaning and states a false fact. |
| sci-010.bn | bn | mistranslation | critical | দুর্বল | [judge] explanation="strong" is translated as "‹…›" (weak/poor). This reverses the meaning: copper is a strong conductor, and the translation says it is a weak one. The causal "so" clause then no longer makes sense. |
| edu-003.hi | hi | mistranslation | critical | जल्दी | [judge] explanation="late" is rendered as "‹…›" (early), so the translation says early submissions will not be accepted, reversing the rule's meaning. It should be "‹…›". |
| geo-004.hi | hi | mistranslation | critical | रात में भी गर्म हो जाता है | [judge] explanation=The source says the desert becomes cold at night. The translation says it becomes hot at night too ("‹…›"). This reverses the meaning and contradicts the stated reason that sand loses heat quickly. |
| sci-004.ta | ta | mistranslation | critical | மெதுவாக பயணிக்கிறது, | [judge] explanation=The source says sound travels faster in water than in air, but the translation says it travels slower (‹…› ), which reverses the meaning. |
| civ-003.bn | bn | number | major | ৫৪২ | [numbers] translation_number=542; source_numbers=['250', '552']; missing_from_translation=['552'] |
| fin-002.bn | bn | speech_repetition | major | করবেন না, এমনকি যদি ফোনকারী বলে যে তারা ব্যাঙ্কে কাজ করে | [back_transcription] repeated_text=করবেন না এমনকি যদি ফোনকারী বলে যে তারা ব্যাঙ্কে কাজ করে; heard=করবেন না এমনকি যদি ফোনকারী বলে যে তারা ব্যাঙ্কে কাজ করে |
