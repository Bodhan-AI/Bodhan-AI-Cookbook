# Bodhan quality report

Scored **213** items. Partial **18**. Unscored **0**. Invalid input rows **0**.

## Findings by language and category

| Language | Items | Flagged items | omission | addition | mistranslation | number | entity | format | wrong_language | speech_omission | speech_repetition | garbled_audio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| hi | 79 | 42 | 5 | 4 | 8 | 5 | 3 | 6 | 4 | 5 | 4 | 4 |
| ta | 76 | 40 | 9 | 4 | 4 | 4 | 2 | 8 | 4 | 4 | 4 | 4 |
| bn | 76 | 42 | 5 | 4 | 8 | 4 | 2 | 8 | 5 | 4 | 4 | 4 |

MQM-style score per language (100 minus penalty points per 100 source words; minor 1, major 5, critical 10): bn **81.44**, hi **82.11**, ta **83.12**.

## Change against the baseline

New flags **113**, resolved **1**, persistent **15**, severity changes **1**. Examples are in diff.md.

## Findings by category

| Category | Flags | Held-out precision | Held-out recall |
| --- | ---: | ---: | ---: |
| omission | 19 | 69.2% | 100.0% |
| addition | 12 | 100.0% | 100.0% |
| mistranslation | 20 | 66.7% | 88.9% |
| number | 13 | 100.0% | 100.0% |
| entity | 7 | 71.4% | 55.6% |
| format | 22 | 60.0% | 100.0% |
| wrong_language | 13 | 100.0% | 100.0% |
| speech_omission | 13 | 100.0% | 100.0% |
| speech_repetition | 12 | 100.0% | 100.0% |
| garbled_audio | 12 | 100.0% | 100.0% |

## Quality targets

| Measure | Target | Observed |
| --- | ---: | --- |
| Seeded error recall | ≥80% | 95.6% (87 of 91 labelled errors) |
| Clean false alarms | ≤10% | 0.0% (0 of 1 associated clean items) |
| Localization | ≥70% | 98.8% (79 of 80 detections with declared label coordinates) |
| Category accuracy | ≥70% | 100.0% (87 of 87 detections) |
| Fresh judge agreement | ≥90% | 100.0% (10 of 10 comparisons) |
| Flag rate on unmutated controls | ≤10% | 9.6% (7 of 73 controls; an upper bound, since an original output can carry a real defect) |

Seeded detections matched by category alone **8**.
Unmeasured cohorts have no denominator. See calibration.json for false negatives, false positives, and coverage.
Cache replay and the 200-item runtime target require separate execution measurements.

## Evidence and cost

- Confirmed and unconfirmed evidence appears in items.jsonl.
- Estimated Bodhan cost represented by successful unique requests **₹2.5752**.
- Judge usage **190 of 190** successful requests returned token counts.
- Actual dispatch count, retry reservations, and elapsed time are in the run log.
- See diff.md for changes and comparable examples.

## Checks that did not complete

| Check and reason | Rows |
| --- | ---: |
| judge: http_402 | 18 |

## Findings to inspect first

- edu-009-mistranslation [bn] mistranslation (critical, judge). 'অসম'
- edu-028-mistranslation [bn] mistranslation (critical, judge). 'ঘড়ির কাঁটার দিকে'
- edu-020-garbled_audio [bn] garbled_audio (major, silence). 'Internal quiet signal for 2.90 s, from 3.90 s to 6.80 s (no frame above -90 dBFS).'
- edu-029-garbled_audio [hi] garbled_audio (major, silence). 'Internal quiet signal for 2.90 s, from 4.20 s to 7.10 s (no frame above -90 dBFS).'
- edu-015-format [bn] format (major, latex). 'v = d/t$'
- edu-022-format [bn] format (major, markdown). '- সকাল ০৮:০০ টায় গেজটি পড়ুন। - সেতু থেকে বেরোনোর আগে ফলাফলটি লিখে রাখুন।'
- edu-028-wrong_language [bn] wrong_language (major, script). 'Rotate the paper counterclockwise by 90 degrees. The center of the paper stays in place'
- edu-028-wrong_language [hi] wrong_language (major, script). 'Rotate the paper counterclockwise by 90 degrees. The center of the paper stays in place'
- edu-025-speech_omission [hi] speech_omission (major, transcript_alignment). 'किसी रिकॉर्डिंग का पुन: उपयोग करने से पहले स्रोत की जाँच करें'
- edu-026-speech_omission [ta] speech_omission (major, transcript_alignment). 'இணைப்பியின் வடிவம் மட்டுமே அதன் தரவு விகிதத்தை அடையாளம் காணாது'
- edu-012-number [bn] number (major, numbers). '৫০'
- edu-019-number [bn] number (major, numbers). '৩০'
- edu-003-speech_repetition [bn] speech_repetition (major, transcript_alignment). 'মিনিট পর আবার দেখুন ক্লাসরুমের থার্মোমিটার চব্বিশ ডিগ্রি সেলসিয়াস দেখাচ্ছে জানালা খোলার'
- edu-008-speech_repetition [bn] speech_repetition (major, transcript_alignment). 'দিকে বিদ্যুৎ পরিবহন করে তারের প্রান্তগুলো অদলবদল করুন এ'
- edu-001-addition [bn] addition (major, judge). 'কিছু শিক্ষার্থী এই ধাপটি কঠিন মনে করে।'
- edu-008-omission [bn] omission (major, judge). 'An LED usually conducts in one direction.'
- edu-016-addition [bn] addition (major, judge). 'এটি করার সময় গ্লাভস পরুন।'
- edu-016-omission [bn] omission (major, judge). 'The speed of sound in dry air near room temperature is about 343 m/s.'
- edu-025-entity [bn] entity (major, judge). 'কুরি'
- edu-026-entity [bn] entity (major, judge). 'কুরি তার'
