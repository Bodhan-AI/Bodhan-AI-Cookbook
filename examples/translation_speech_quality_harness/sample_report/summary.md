# Translation and speech quality report

Dataset `demo.jsonl` · languages: bn, hi, ta · 180 items (180 scored, 0 skipped) · 0 bad lines · 0 filtered out by `--langs`

## By language

| Language | Items | Scored | Flagged items | Flags | Uncertain items | MQM score |
|---|---:|---:|---:|---:|---:|---:|
| bn | 60 | 60 | 36 | 47 | 19 | 93.9 |
| hi | 60 | 60 | 32 | 46 | 10 | 93.9 |
| ta | 60 | 60 | 41 | 69 | 23 | 91.2 |

MQM score: 100 minus penalties per source word (minor 1, major 5, critical 10), averaged per item.

## By category

| Category | Flags | Items | Severity |
|---|---:|---:|---|
| omission | 23 | 20 | critical 1, major 13, minor 9 |
| addition | 6 | 6 | major 6 |
| mistranslation | 21 | 20 | critical 2, major 17, minor 2 |
| number | 13 | 10 | critical 9, major 4 |
| term | 11 | 9 | major 10, minor 1 |
| format | 12 | 11 | major 11, minor 1 |
| wrong_language | 8 | 8 | critical 7, minor 1 |
| speech_omission | 34 | 30 | major 34 |
| speech_repetition | 17 | 17 | major 17 |
| speech_garbled | 17 | 17 | major 17 |

## Top 10 failures

1. **bn / chem-01** — wrong_language (critical, 0.98) “जल (H2O) एक ध्रुवीय अकार्बनिक यौगिक है जो कमरे के तापमान पर…” — Only 0% of letters are Bengali (expected for Bengali); text is mostly written in Devanagari script.
2. **ta / chem-04** — wrong_language (critical, 0.98) “## পৃথকীকরণ প্রক্রিয়া পৃথকীকরণ প্রক্রিয়া এমন একটি পদ্ধতি …” — Only 0% of letters are Tamil (expected for Tamil); text is mostly written in Bengali script.
3. **hi / chem-05** — wrong_language (critical, 0.98) “ஒரு வேதியியல் தனிமம், பெரும்பாலும் வெறுமனே ஒரு தனிமம் என்று…” — Only 0% of letters are Devanagari (expected for Hindi); text is mostly written in Tamil script.
4. **ta / eco-03** — wrong_language (critical, 0.98) “অর্থনৈতিক খাত হলো অর্থনীতির একটি অংশ যা একই ধরনের অর্থনৈতিক…” — Only 0% of letters are Tamil (expected for Tamil); text is mostly written in Bengali script.
5. **bn / hist-06** — wrong_language (critical, 0.98) “ताजमहल भारत के उत्तर प्रदेश राज्य में यमुना नदी के दाहिने क…” — Only 0% of letters are Bengali (expected for Bengali); text is mostly written in Devanagari script.
6. **bn / phy-13** — wrong_language (critical, 0.98) “Newton's law of universal gravitation describes gravity as …” — Only 0% of letters are Bengali (expected for Bengali); text is mostly left in English (untranslated).
7. **bn / chem-05** — number (critical, 0.97) “১২.০১” — Number changed: source '12.011' -> translation '১২.০১'
8. **bn / phy-08** — number (critical, 0.97) “১৪৪৮” — Number changed: source '1440' -> translation '১৪৪৮'
9. **hi / geo-01** — wrong_language (critical, 0.95) “Kangchenjunga is the third-highest mountain in the world.” — Segment left in English: 'Kangchenjunga is the third-highest mountain in the world.'
10. **bn / eco-04** — omission (critical, 0.93) source: “The consumer protection bill 2019 primarily defines the fol…” — Source number '2019' is missing from the translation

## Calibration (labelled items, in-sample; cross-validated numbers in `calibration.json`)

| Labelled items | Labels | Recall | False alarms | Precision | Localisation | Category accuracy |
|---:|---:|---:|---:|---:|---:|---:|
| 40 | 27 | 78% | 0% | 88% | 95% | 100% |

## Checks

An item a check could not run on has no flags from it: read zero flags there as not checked.

| Check | Ran | Skipped / not applicable | Failed | Main reasons |
|---|---:|---:|---:|---|
| asr | 180 | 0 | 0 |  |
| audio_stats | 180 | 0 | 0 |  |
| back_translation | 180 | 0 | 0 |  |
| entities | 144 | 36 | 0 | no names or terms in source (36) |
| format | 72 | 108 | 0 |  |
| judge | 180 | 0 | 0 |  |
| length | 180 | 0 | 0 |  |
| numbers | 180 | 0 | 0 |  |
| reference | 0 | 180 | 0 |  |
| script | 180 | 0 | 0 |  |

## Cost

Model outputs used: ₹13.9670 (llm ₹0.0000, transcribe ₹13.5974, translate ₹0.3695); judge LLM tokens: 785539 prompt + 57508 completion. Money actually spent in this run (cache misses only) is in `run_meta.json`.

## Data problems

None.
