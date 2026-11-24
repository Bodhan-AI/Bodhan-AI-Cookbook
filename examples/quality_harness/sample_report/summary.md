# Translation quality report

| language | items | scored | flagged | uncertain | clean | MQM | MQM / 100 words |
|---|---|---|---|---|---|---|---|
| bn | 128 | 128 | 69 (54%) | 0 | 59 | 458 | 12.79 |
| hi | 128 | 128 | 68 (53%) | 0 | 60 | 342 | 9.01 |
| ta | 124 | 124 | 69 (56%) | 0 | 55 | 403 | 11.54 |

Model requests: llm 425, translate 1236. Bodhan cost of these outputs: ₹0.99 (paid in this run or an earlier one; reruns use the cache). This run's own runtime and spend: run.json.

## Quality targets

| measure | value | target | |
|---|---|---|---|
| Detection recall (labelled errors flagged at their place, held-out) | 94% | ≥ 80% | ✅ met |
| False alarms on clean labelled items (held-out) | 6% | ≤ 10% | ✅ met |
| Flag precision (flags pointing at a labelled error) | 91% |  |  |
| Localization (found at the right place, of errors whose item is flagged) | 97% |  |  |
| Category accuracy (of found errors, flagged in the right category) | 73% |  |  |
| Judge consistency (same verdict on re-judging) | 91% | ≥ 90% | ✅ met |
| Runtime per 200 items, fresh (projected: these requests at llm 8, speak 7, transcribe 8, translate 12 requests/min) | 54.2 min | < 30 min | ❌ missed |

From calibration.json: 229 labelled items (71 clean, 178 labelled errors), strategy `keep`, flag threshold 0.65. `keep`: this calibration changed nothing, as no tuning beat the thresholds in use on held-out folds. If those thresholds were set from these same labels, the numbers above are in-sample; calibration.json has every strategy's held-out result.

| category | labelled errors | found | right category | flags | precision |
|---|---|---|---|---|---|
| addition | 20 | 95% | 85% | 18 | 100% |
| fluency | 3 | 0% | 0% | 0 | — |
| format | 21 | 100% | 86% | 29 | 79% |
| mistranslation | 39 | 95% | 54% | 33 | 76% |
| number | 22 | 96% | 91% | 27 | 100% |
| omission | 26 | 100% | 62% | 28 | 100% |
| term | 27 | 89% | 48% | 16 | 94% |
| wrong_language | 20 | 100% | 85% | 17 | 100% |

## Flags by language and category

Count (critical / major / minor).

| language | addition | format | mistranslation | number | omission | term | wrong_language |
|---|---|---|---|---|---|---|---|
| bn | 4 (0/4/0) | 8 (0/5/3) | 31 (5/22/4) | 6 (0/6/0) | 18 (4/13/1) | 9 (0/9/0) | 7 (6/1/0) |
| hi | 7 (0/7/0) | 12 (0/7/5) | 17 (2/12/3) | 9 (0/9/0) | 14 (1/10/3) | 7 (0/6/1) | 6 (3/3/0) |
| ta | 6 (0/6/0) | 11 (0/9/2) | 25 (1/20/4) | 13 (0/13/0) | 8 (0/7/1) | 12 (1/10/1) | 6 (4/2/0) |

## Worst items

| item | lang | MQM | category | severity | words | why |
|---|---|---|---|---|---|---|
| in22-0154-bodhan~clean-nfd | bn | 15 | mistranslation | major | আবেদন করেনি | adjudicator: আবেদন করেনি means 'did not apply/request', not 'did not attract'; wrong verb for 'appeal to' in … |
| | | | mistranslation | major | জনপ্রিয় | adjudicator: জনপ্রিয় means 'popular' (neutral/positive), losing the negative 'populist' connotation of pande… |
| | | | mistranslation | major | দুর্নীতিগ্রস্ত প্রভাব | adjudicator: দুর্নীতিগ্রস্ত প্রভাব means 'corruption-affected influence' (passive), reversing the causal sens… |
| in22-0698-bodhan~addition | bn | 15 | addition | major | রোগ নির্ণয়, তীব্রতা এবং ব্যক্তিবিশেষের … | LLM judge (high confidence): Entire segment T2 has no corresponding source text; it adds content about person… |
| | | | mistranslation | major | স্বতন্ত্র | adjudicator: Translates 'distinctive' (unique/distinct) as 'স্বতন্ত্র' (independent/autonomous), changing the… |
| | | | mistranslation | major | জীবনধারা | adjudicator: Translates 'life forms' (biological organisms) as 'জীবনধারা' (lifestyles/ways of life), incorrec… |
| num-un~term | ta | 15 | term | critical | உலக சுகாதார அமைப்பு 24 அக்டோபர் 1945 அன… | LLM judge (high confidence): Translates 'United Nations' as 'World Health Organization' (WHO), a completely d… |
| | | | mistranslation | major | 193 பேர் | adjudicator: Translates '193 members' as '193 people', changing meaning from countries to individuals. |
| in22-0522-bodhan | bn | 12 | omission | minor | প্রাক-পল্লবন | adjudicator: Translation omits 'each with their culture' describing the pre-Pallavan people; 'প্রাক-পল্লবন' a… |
| | | | mistranslation | minor | বিকাশ | adjudicator: 'Flowering' implies flourishing peak; 'বিকাশ' is neutral development, losing the blossoming nuan… |
| | | | mistranslation | major | রূপান্তরকারী পর্যায় | adjudicator: 'Transitional stage' means a stage of transition; 'রূপান্তরকারী পর্যায়' means transformative ph… |
| in22-0698-bodhan | bn | 11 | mistranslation | major | স্বতন্ত্র | adjudicator: 'স্বতন্ত্র' means independent/isolated, not distinctive; changes meaning of 'seasonally distinct… |
| | | | mistranslation | major | জীবনধারা | adjudicator: 'জীবনধারা' means lifestyles, not biological life forms; completely different concept. |
| | | | mistranslation | minor | জলবায়ুর ঋতু | adjudicator: 'জলবায়ুর ঋতু' means 'season of climate', losing sense of seasonal pattern/cyclical nature impli… |
| in22-0735-bodhan | bn | 11 | omission | major | with a view to achieving enduring solid… | adjudicator: The clause 'with a view to achieving enduring solidarity and unity among the countries and peopl… |
| | | | mistranslation | major | এটি একটি | adjudicator: The translation says 'It is an ASEAN Community' instead of 'It envisions an ASEAN Community', ch… |
| | | | mistranslation | minor | কল্যাণ, জীবিকা এবং কল্যাণ | adjudicator: The translation omits 'inclusive and harmonious' describing the society, and uses 'কল্যাণ' for b… |
| ent-rbi~wrong_language | bn | 10 | wrong_language | critical | இந்த வங்கி நாட்டின் பண விநியோகத்தைக் கட… | script: text in TAMIL script where BENGALI was expected |
| ent-tiger~mistranslation | bn | 10 | mistranslation | critical | বেঙ্গল টাইগার (প্যান্থেরা টাইগ্রিস) হল … | LLM judge (high confidence): The translation says 'outside the Indian subcontinent' while the source says 'na… |
| in22-0086-bodhan~wrong_language | hi | 10 | wrong_language | critical | মিয়ান মিরের ক্যাম্পে যদিও সিপাহীদের সং… | script: text in BENGALI script where DEVANAGARI was expected |
| in22-0119-bodhan~wrong_language | bn | 10 | wrong_language | critical | लड़के भी यौन शोषण के प्रति समान रूप से … | script: text in DEVANAGARI script where BENGALI was expected |

## Not checked

- speech: skipped: --skip-speech: 380 item(s)
- judge: error: judge reply has no 'errors' list: 2 item(s)

Per-item details: items.jsonl (flags with evidence and the candidates behind them).
