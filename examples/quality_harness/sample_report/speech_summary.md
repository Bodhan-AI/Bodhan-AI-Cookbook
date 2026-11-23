# Translation quality report

| language | items | scored | flagged | uncertain | clean | MQM | MQM / 100 words |
|---|---|---|---|---|---|---|---|
| bn | 26 | 26 | 19 (73%) | 0 | 7 | 138 | 15.88 |
| hi | 24 | 24 | 16 (67%) | 0 | 8 | 86 | 11.21 |
| ta | 20 | 20 | 13 (65%) | 0 | 7 | 75 | 13.04 |

Model requests: llm 75, transcribe 115, translate 184. Bodhan cost of these outputs: ₹2.27 (paid in this run or an earlier one; reruns use the cache). This run's own runtime and spend: run.json.

## Quality targets

| measure | value | target | |
|---|---|---|---|
| Detection recall (labelled errors flagged at their place, held-out) | 92% | ≥ 80% | ✅ met |
| False alarms on clean labelled items (held-out) | 0% | ≤ 10% | ✅ met |
| Flag precision (flags pointing at a labelled error) | 100% |  |  |
| Localization (found at the right place, of errors whose item is flagged) | 100% |  |  |
| Category accuracy (of found errors, flagged in the right category) | 100% |  |  |
| Judge consistency (same verdict on re-judging) | — | ≥ 90% | not measured in this run |
| Runtime per 200 items, fresh (projected: these requests at llm 8, speak 7, transcribe 8, translate 12 requests/min) | 43.8 min | < 30 min | ❌ missed |

From calibration.json: 44 labelled items (8 clean, 36 labelled errors), strategy `keep`, flag threshold 0.65. Held-out = tuned on 4/5 of the labelled items, measured on the rest.

| category | labelled errors | found | right category | flags | precision |
|---|---|---|---|---|---|
| speech_garbled | 18 | 100% | 100% | 18 | 100% |
| speech_omission | 12 | 92% | 92% | 12 | 100% |
| speech_repetition | 6 | 67% | 67% | 4 | 100% |

## Flags by language and category

Count (critical / major / minor).

| language | mistranslation | number | speech_garbled | speech_omission | speech_repetition | term |
|---|---|---|---|---|---|---|
| bn | 8 (0/5/3) |  | 6 (0/6/0) | 15 (0/15/0) | 1 (0/1/0) |  |
| hi | 11 (0/5/6) |  | 6 (0/6/0) | 3 (0/3/0) | 2 (0/2/0) |  |
| ta |  | 2 (0/2/0) | 6 (0/6/0) | 5 (0/5/0) | 1 (0/1/0) | 1 (0/1/0) |

## Worst items

| item | lang | MQM | category | severity | words | why |
|---|---|---|---|---|---|---|
| in22-1009-human | bn | 15 | speech_omission | major | এলিফ্যান্ট জলপ্রপাত, মাওকডক | speech_asr: {"kind": "omission", "missing_words": 3, "missing_chars": 24, "missing_text": "এলিফ্যান্ট জলপ্রপা… |
| | | | speech_omission | major | ভ্যালি ভিউ এবং অন্যান্য | speech_asr: {"kind": "omission", "missing_words": 4, "missing_chars": 20, "missing_text": "ভ্যালি ভিউ এবং অন্… |
| | | | speech_omission | major | মাওসমাই গুহা, চেরাপুঞ্জি ইকো পার্ক, নোহ… | speech_asr: {"kind": "truncation", "missing_words": 11, "missing_chars": 70, "missing_text": "মাওসমাই গুহা চে… |
| in22-1009-human~end_silence | bn | 15 | speech_omission | major | এলিফ্যান্ট জলপ্রপাত, মাওকডক | speech_asr: {"kind": "omission", "missing_words": 3, "missing_chars": 24, "missing_text": "এলিফ্যান্ট জলপ্রপা… |
| | | | speech_omission | major | ভ্যালি ভিউ এবং অন্যান্য | speech_asr: {"kind": "omission", "missing_words": 4, "missing_chars": 20, "missing_text": "ভ্যালি ভিউ এবং অন্… |
| | | | speech_omission | major | মাওসমাই গুহা, চেরাপুঞ্জি ইকো পার্ক, নোহ… | speech_asr: {"kind": "truncation", "missing_words": 11, "missing_chars": 70, "missing_text": "মাওসমাই গুহা চে… |
| in22-1009-human~quieter | bn | 15 | speech_omission | major | এলিফ্যান্ট জলপ্রপাত, মাওকডক | speech_asr: {"kind": "omission", "missing_words": 3, "missing_chars": 24, "missing_text": "এলিফ্যান্ট জলপ্রপা… |
| | | | speech_omission | major | ভ্যালি ভিউ এবং অন্যান্য | speech_asr: {"kind": "omission", "missing_words": 4, "missing_chars": 20, "missing_text": "ভ্যালি ভিউ এবং অন্… |
| | | | speech_omission | major | মাওসমাই গুহা, চেরাপুঞ্জি ইকো পার্ক, নোহ… | speech_asr: {"kind": "truncation", "missing_words": 11, "missing_chars": 70, "missing_text": "মাওসমাই গুহা চে… |
| in22-1009-human~silence | bn | 15 | speech_garbled | major | ্থানে থামার বিকল্প দেয় (এলিফ্যান্ট জলপ… | speech_signal: {"kind": "silence", "time": [7.23, 8.88], "seconds": 1.65} |
| | | | speech_omission | major | ভ্যালি ভিউ এবং অন্যান্য | speech_asr: {"kind": "omission", "missing_words": 4, "missing_chars": 20, "missing_text": "ভ্যালি ভিউ এবং অন্… |
| | | | speech_omission | major | মাওসমাই গুহা, চেরাপুঞ্জি ইকো পার্ক, নোহ… | speech_asr: {"kind": "truncation", "missing_words": 11, "missing_chars": 70, "missing_text": "মাওসমাই গুহা চে… |
| in22-0086-bodhan~noise | bn | 10 | speech_garbled | major | গাযোগ করছে কিনা ত | speech_signal: {"kind": "noise", "time": [23.73, 25.28], "seconds": 1.55} |
| | | | mistranslation | major | মিয়ান মিরের ক্যাম্পে যদিও সিপাহীদের সং… | LLM judge (high confidence): The English states the officers 'found it difficult to ascertain whether they we… |
| in22-0086-bodhan~omission | bn | 10 | speech_omission | major | এবং যখন খবরটি এল | speech_asr: {"kind": "omission", "missing_words": 4, "missing_chars": 13, "missing_text": "এবং যখন খবরটি এল",… |
| | | | mistranslation | major | মিয়ান মিরের ক্যাম্পে যদিও সিপাহীদের সং… | LLM judge (high confidence): The English states the officers 'found it difficult to ascertain whether they we… |
| in22-0086-human~truncation | ta | 10 | speech_omission | major | மீரட் சிப்பாய்களுடன் ரகசியத் தொடர்பு உள… | speech_asr: {"kind": "truncation", "missing_words": 10, "missing_chars": 82, "missing_text": "மீரட் சிப்பாய்க… |
| | | | number | major | மியான் மீர் முகாமில் நால்வருக்கு ஒருவர்… | LLM judge (high confidence): The source states the Sepoys outnumbered English soldiers by four to one (ratio … |
| in22-0460-human~clipping | hi | 10 | speech_garbled | major | आज जिस प्रकार से क्रिकेट खेला जाता है, … | speech_signal: {"kind": "clipping", "time": [0.0, 6.49], "clipped_share": 0.0057} |
| | | | mistranslation | major | आज जिस प्रकार से क्रिकेट खेला जाता है, … | LLM judge (high confidence): The English source says 'south-eastern' but the Hindi translation uses 'दक्षिण-उ… |
| in22-0460-human~repetition | hi | 10 | speech_repetition | major | उसकी उत्पत्ति | speech_asr: {"kind": "repeated_words", "extra_words": 2, "heard": "उसकी उत्पत्ति", "time": [3.86, 4.23]} |
| | | | mistranslation | major | आज जिस प्रकार से क्रिकेट खेला जाता है, … | LLM judge (high confidence): The English source says 'south-eastern' but the Hindi translation uses 'दक्षिण-उ… |
| num-railways~truncation | ta | 10 | speech_omission | major | அகல ரயில் பாதைகளில் சுமார் | speech_asr: {"kind": "omission", "missing_words": 4, "missing_chars": 23, "missing_text": "அகல ரயில் பாதைகளில… |
| | | | speech_omission | major | மின்மயமாக்கப்பட்டுள்ளன | speech_asr: {"kind": "truncation", "missing_words": 1, "missing_chars": 22, "missing_text": "மின்மயமாக்கப்பட்… |

## Not checked

- Every check ran on every item.

Per-item details: items.jsonl (flags with evidence and the candidates behind them).
