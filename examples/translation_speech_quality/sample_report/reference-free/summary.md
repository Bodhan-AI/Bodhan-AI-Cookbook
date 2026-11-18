# Translation and speech quality report

Status: complete

108 valid items, 0 invalid lines, 79 flagged items.

| Language | Items | Flagged | Partial |
|---|---:|---:|---:|
| bn | 36 | 26 | 0 |
| hi | 36 | 28 | 0 |
| ta | 36 | 25 | 0 |

## Detection measurements

{
  "labelled_items": 108,
  "clean_items": 54,
  "seeded_items": 54,
  "detection_recall": 1.0,
  "false_alarm_rate": 0.46296296296296297,
  "category_accuracy": 0.9814814814814815,
  "localization_overlap": 0.7592592592592593,
  "per_category": {
    "omission": {
      "tp": 8,
      "fp": 5,
      "fn": 1,
      "precision": 0.6153846153846154,
      "recall": 0.8888888888888888
    },
    "addition": {
      "tp": 9,
      "fp": 1,
      "fn": 0,
      "precision": 0.9,
      "recall": 1.0
    },
    "mistranslation": {
      "tp": 0,
      "fp": 33,
      "fn": 0,
      "precision": 0.0,
      "recall": null
    },
    "number": {
      "tp": 9,
      "fp": 2,
      "fn": 0,
      "precision": 0.8181818181818182,
      "recall": 1.0
    },
    "entity": {
      "tp": 9,
      "fp": 0,
      "fn": 0,
      "precision": 1.0,
      "recall": 1.0
    },
    "format": {
      "tp": 9,
      "fp": 6,
      "fn": 0,
      "precision": 0.6,
      "recall": 1.0
    },
    "wrong_language": {
      "tp": 9,
      "fp": 0,
      "fn": 0,
      "precision": 1.0,
      "recall": 1.0
    },
    "speech_omission": {
      "tp": 0,
      "fp": 0,
      "fn": 0,
      "precision": null,
      "recall": null
    },
    "speech_repetition": {
      "tp": 0,
      "fp": 0,
      "fn": 0,
      "precision": null,
      "recall": null
    },
    "garbled_audio": {
      "tp": 0,
      "fp": 0,
      "fn": 0,
      "precision": null,
      "recall": null
    }
  },
  "annotation_sources": {
    "human_translation_control_assumption": 54,
    "programmatic_injection": 54
  }
}

## Quality targets

These measurements use controlled injections and assumed-clean reference controls, not human-reviewed natural errors.
- detection_recall: 1.0; target at least 0.8; met.
- false_alarm_rate: 0.46296296296296297; target at most 0.1; missed.
- localization_overlap: 0.7592592592592593; target at least 0.7; met.
- category_accuracy: 0.9814814814814815; target at least 0.7; met.

Misses can reflect weak semantic judgments, conservative uncalibrated thresholds, noisy round trips, incomplete API checks, and approximate omission labels. Inspect per-item evidence before assigning a cause.

## Cost and runtime

See [execution.json](execution.json) for this invocation's estimated cost, cache/API counts, scope and elapsed time. Assessment artifacts stay identical on cached replays; execution cost falls to zero.

## Calibration and limits

Human-labelled calibration items: 0.
An unset precision or recall is null, including categories with no labelled positives.
Injected labels measure detection of controlled perturbations. They are not human judgments of natural model errors.
Back-translations and ASR are fallible. Unsupported claims remain in the review queue.
Judge agreement: 1.0 over 6 repeated items.

## Review first

- `flores-0003-hi` (hi): omission
  - {"type": "omission", "severity": "minor", "field": "source", "span": [81, 93], "evidence": {"quote": "and exploded", "explanation": "The source text mentions that the aircraft 'exploded', but this detail is omitted in the Hindi translation.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
- `flores-0003-hi:number` (hi): number, number
  - {"type": "number", "severity": "major", "field": "translation", "span": [13, 17], "evidence": {"observed": "3230", "missing": ["0230"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "major", "field": "translation", "span": [13, 21], "evidence": {"quote": "3230 UTC", "explanation": "The source text specifies '0230 UTC', but the translation incorrectly renders this as '3230 UTC'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0008-hi:omission` (hi): omission, omission
  - {"type": "omission", "severity": "major", "field": "source", "span": [65, 119], "evidence": {"quote": "opposite the police-guarded entrance to Downing Street", "explanation": "The translation completely omits the location detail 'opposite the police-guarded entrance to Downing Street'.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
  - {"type": "omission", "severity": "minor", "field": "source", "span": [105, 119], "evidence": {"quote": "Downing Street", "explanation": "The translation omits the specific mention of 'Downing Street', which is a key entity in the source text.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
- `flores-0009-hi` (hi): mistranslation
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [0, 13], "evidence": {"quote": "ठीक 11:00 बजे", "explanation": "The source text says 'Just after 11:00', which implies a time shortly following 11:00. The translation 'ठीक 11:00 बजे' translates to 'exactly at 11:00', which changes the meaning.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0009-hi:addition` (hi): addition, mistranslation
  - {"type": "addition", "severity": "major", "field": "translation", "span": [99, 145], "evidence": {"quote": "This statement is not supported by the source.", "explanation": "The translator included an extraneous English sentence that is not present in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [0, 13], "evidence": {"quote": "ठीक 11:00 बजे", "explanation": "The source says 'Just after 11:00', but the translation uses 'ठीक 11:00 बजे' which means 'exactly at 11:00'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0010-hi` (hi): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [92, 108], "evidence": {"quote": "भीड़ इकट्ठा करने", "explanation": "The source text refers to 'traffic building up', whereas the translation uses 'भीड़ इकट्ठा करने' (gathering crowds). These are distinct concepts.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0010-hi:wrong_language` (hi): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 161], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0015-hi` (hi): mistranslation
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [38, 72], "evidence": {"quote": "सबसे महत्वपूर्ण इवेंट के अंतिम दौर", "explanation": "The source text says \"final rounds\" (plural) and \"most important events\" (plural), but the translation uses singular forms for both (\"अंतिम दौर\" and \"इवेंट\").", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0015-hi:format` (hi): format, mistranslation
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 169], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [38, 72], "evidence": {"quote": "सबसे महत्वपूर्ण इवेंट के अंतिम दौर", "explanation": "The source text uses plural 'final rounds' and 'most important events', but the translation uses singular forms for both.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0016-hi` (hi): mistranslation
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [89, 99], "evidence": {"quote": "शब्द युग्म", "explanation": "The source text uses the term 'umbrella term', which refers to a broad or inclusive category. The translation 'शब्द युग्म' literally means 'word pair' or 'compound word', which is an inaccurate translation of the concept.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0016-hi:entity` (hi): entity, mistranslation
  - {"type": "entity", "severity": "major", "field": "source", "span": [149, 157], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [89, 99], "evidence": {"quote": "शब्द युग्म", "explanation": "The source text uses the term 'umbrella term', which refers to a broad or inclusive category. The translation 'शब्द युग्म' literally means 'word pair' or 'compound word', which is an inaccurate translation of the concept.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0017-hi:number` (hi): number, number
  - {"type": "number", "severity": "major", "field": "translation", "span": [144, 146], "evidence": {"observed": "71", "missing": ["41"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "major", "field": "translation", "span": [143, 147], "evidence": {"quote": "[71]", "explanation": "The source text contains the citation '[41]', but the translation incorrectly changed this to '[71]'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0018-hi:omission` (hi): omission
  - {"type": "omission", "severity": "major", "field": "source", "span": [55, 134], "evidence": {"quote": "controlled roughly a third of the 1200 shell companies used to accomplish this.", "explanation": "The translation is incomplete; it stops abruptly after '1200 शेल' (1200 shell), omitting the information that Deutsche Bank controlled roughly a third of these companies.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
- `flores-0019-hi:addition` (hi): addition, mistranslation
  - {"type": "addition", "severity": "major", "field": "translation", "span": [129, 175], "evidence": {"quote": "This statement is not supported by the source.", "explanation": "The translator added an English sentence at the end of the translation that is not present in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [99, 108], "evidence": {"quote": "कई नेताओं", "explanation": "The source says 'the leaders' (implying the specific heads of government), whereas the translation says 'many leaders' (कई नेताओं), which changes the meaning significantly.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0020-hi` (hi): mistranslation
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [125, 139], "evidence": {"quote": "स्थाई नागरिकता", "explanation": "The source text says 'permanent resident', but the translation says 'permanent citizenship'. A green card grants permanent residency, not citizenship.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0020-hi:wrong_language` (hi): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 134], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0021-hi` (hi): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [15, 19], "evidence": {"quote": "साइह", "explanation": "The name 'Hsieh' has been transliterated as 'Cyah' (साइह) in the translation, which is an incorrect representation of the name 'Hsieh'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0021-hi:format` (hi): format, format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 91], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
  - {"type": "format", "severity": "minor", "field": "translation", "span": [75, 91], "evidence": {"quote": "$x^2 + y^2 = z^2", "explanation": "The mathematical formula is missing the closing dollar sign ($) in the translation, which breaks the LaTeX formatting.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0023-hi:entity` (hi): entity, omission, addition
  - {"type": "entity", "severity": "major", "field": "source", "span": [107, 115], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
  - {"type": "omission", "severity": "minor", "field": "source", "span": [106, 116], "evidence": {"quote": "(Sample-A)", "explanation": "The parenthetical reference '(Sample-A)' present in the source text was omitted in the translation.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
  - {"type": "addition", "severity": "minor", "field": "translation", "span": [105, 123], "evidence": {"quote": "(DIFFERENT_ENTITY)", "explanation": "The translation includes an extraneous tag '(DIFFERENT_ENTITY)' that was not present in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0025-hi:number` (hi): number, number
  - {"type": "number", "severity": "major", "field": "translation", "span": [32, 33], "evidence": {"observed": "8", "missing": ["5"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "critical", "field": "translation", "span": [32, 35], "evidence": {"quote": "8-3", "explanation": "The source text states the score was '5-3', but the translation incorrectly states '8-3'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0026-hi` (hi): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [56, 85], "evidence": {"quote": "ओवेचकिन को सबसे पहली मदद मिली", "explanation": "The translation incorrectly states that Ovechkin received the assist, whereas the source text states that Ovechkin provided the assist ('Ovechkin's first assist... was on the game-winning goal').", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0026-hi:omission` (hi): omission
  - {"type": "omission", "severity": "major", "field": "source", "span": [0, 43], "evidence": {"quote": "Ovechkin's first assist of the night was on", "explanation": "The translation completely omits the primary subject and action of the sentence, which is Ovechkin's first assist.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
- `flores-0027-hi` (hi): format
  - {"type": "format", "severity": "minor", "field": "translation", "span": [56, 73], "evidence": {"quote": "1995-96 से लेकरजब", "explanation": "There is a missing space between 'लेकर' and 'जब'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0027-hi:addition` (hi): addition
  - {"type": "addition", "severity": "minor", "field": "translation", "span": [204, 250], "evidence": {"quote": "This statement is not supported by the source.", "explanation": "The translator added an unnecessary English disclaimer at the end of the translation which is not present in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0028-hi:wrong_language` (hi): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 105], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0029-hi:format` (hi): format, format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 152], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
  - {"type": "format", "severity": "minor", "field": "translation", "span": [136, 152], "evidence": {"quote": "$x^2 + y^2 = z^2", "explanation": "The closing dollar sign ($) for the LaTeX math expression is missing in the translation.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0031-hi` (hi): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 113], "evidence": {"component": "table", "expected": [], "observed": [2]}, "check": "format_integrity", "confidence": 0.95}
- `flores-0031-hi:entity` (hi): format, entity, mistranslation, format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 121], "evidence": {"component": "table", "expected": [], "observed": [2]}, "check": "format_integrity", "confidence": 0.95}
  - {"type": "entity", "severity": "major", "field": "source", "span": [96, 104], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [85, 100], "evidence": {"quote": "पद भर संभल लिया", "explanation": "The phrase 'took over' in the context of military forces means 'to assume control'. The translation 'पद भर संभल लिया' (took charge of a position/post) is semantically incorrect and does not convey the meaning of taking control of the facility.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
  - {"type": "format", "severity": "minor", "field": "translation", "span": [103, 121], "evidence": {"quote": "(DIFFERENT_ENTITY)", "explanation": "The source text contains '(Sample-A)' at the end, but the translation replaced it with '(DIFFERENT_ENTITY)', which is an incorrect placeholder or label.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0003-ta:number` (ta): number, mistranslation
  - {"type": "number", "severity": "major", "field": "translation", "span": [4, 6], "evidence": {"observed": "69", "missing": ["39"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [4, 7], "evidence": {"quote": "69C", "explanation": "The source text specifies '39C', but the translation incorrectly uses '69C'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0008-ta` (ta): mistranslation, mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [30, 43], "evidence": {"quote": "வைட் ஹாலுக்கு", "explanation": "The translation incorrectly identifies 'Whitehall' as the Prime Minister's residence. In the source text, Whitehall is the location of the protest, and Downing Street is the Prime Minister's residence.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [0, 43], "evidence": {"quote": "பிரதமரின் அதிகாரபூர்வ இல்லமான வைட் ஹாலுக்கு", "explanation": "The translation incorrectly attributes the status of 'Prime Minister's official residence' to Whitehall instead of Downing Street.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0008-ta:omission` (ta): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [30, 43], "evidence": {"quote": "வைட் ஹாலுக்கு", "explanation": "The translation incorrectly identifies 'Whitehall' as the Prime Minister's residence. In the source text, the protest is on 'Whitehall' opposite the entrance to Downing Street (the residence). The translation implies the residence is Whitehall.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0009-ta:addition` (ta): addition
  - {"type": "addition", "severity": "major", "field": "translation", "span": [113, 159], "evidence": {"quote": "This statement is not supported by the source.", "explanation": "The translator added an extraneous English sentence that is not present in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0010-ta` (ta): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [88, 110], "evidence": {"quote": "சமமாகப் பாவிக்கக் கூறி", "explanation": "The source text says the police needed to 'balance' the right to protest with the traffic. The translation uses 'சமமாகப் பாவிக்கக் கூறி' (asking them to treat equally), which changes the meaning from managing/balancing competing interests to treating them as equal, which is not what the source implies.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0010-ta:wrong_language` (ta): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 161], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0015-ta:format` (ta): format, format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 233], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
  - {"type": "format", "severity": "minor", "field": "translation", "span": [38, 216], "evidence": {"quote": "\"\"மிக முக்கியமான நிகழ்வுகளின் இறுதி சுற்றுகளில் திரும்பியிருப்பதைக் குறித்து நான் மிக மகிழ்ச்சியடைகிறேன்\"\" என்றார். இதை வெற்றி பெறுவதற்கு முயற்சி செய்ய நான் இங்கு வந்துள்ளேன்.\"\"\"", "explanation": "The translation contains excessive and redundant quotation marks at the end of the sentence.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0016-ta` (ta): mistranslation
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [82, 92], "evidence": {"quote": "பெறக்கூடிய", "explanation": "The source says 'from', implying origin. The translation uses 'பெறக்கூடிய' (obtainable/can be obtained), which slightly alters the meaning from a statement of fact to a possibility.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0016-ta:entity` (ta): entity, mistranslation, format
  - {"type": "entity", "severity": "major", "field": "source", "span": [149, 157], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [82, 92], "evidence": {"quote": "பெறக்கூடிய", "explanation": "The source says 'from', implying the origin of the documents. The translation uses 'பெறக்கூடிய' (obtainable/can be obtained), which slightly alters the meaning from the source's statement of origin.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
  - {"type": "format", "severity": "minor", "field": "translation", "span": [0, 20], "evidence": {"quote": "\"\"\"பனாமா பேப்பர்ஸ்\"\"", "explanation": "There is an excessive use of quotation marks (triple quotes) at the beginning of the sentence.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0017-ta:number` (ta): number, number
  - {"type": "number", "severity": "major", "field": "translation", "span": [187, 189], "evidence": {"observed": "71", "missing": ["41"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "major", "field": "translation", "span": [186, 190], "evidence": {"quote": "[71]", "explanation": "The citation number in the source text is '[41]', but the translation incorrectly uses '[71]'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0018-ta` (ta): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [127, 155], "evidence": {"quote": "கட்டுப்படுத்த பரிந்துரைத்தது", "explanation": "The source text says 'controlled' (past tense, indicating a state of being), but the translation uses 'பரிந்துரைத்தது' (suggested/recommended to control), which changes the meaning to an instruction or recommendation rather than a factual claim about past control.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0018-ta:omission` (ta): omission, mistranslation
  - {"type": "omission", "severity": "critical", "field": "source", "span": [41, 88], "evidence": {"quote": "Deutsche Bank controlled roughly a third of the", "explanation": "The translation completely omits the subject 'Deutsche Bank' and the verb 'controlled', leaving the sentence incomplete and changing the meaning entirely.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [93, 101], "evidence": {"quote": "மூன்றில்", "explanation": "The translation ends abruptly at 'one-third' (மூன்றில்), failing to complete the sentence or convey the full information provided in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0019-ta:addition` (ta): addition
  - {"type": "addition", "severity": "major", "field": "translation", "span": [170, 216], "evidence": {"quote": "This statement is not supported by the source.", "explanation": "The translator added an extraneous English sentence to the end of the translation that is not present in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0020-ta` (ta): mistranslation
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [27, 74], "evidence": {"quote": "நியூயார்க் மற்றும் ஹார்வர்ட் சட்டக் கல்லூரியில்", "explanation": "The source text specifies 'New York University and Harvard Law School'. The translation implies that both universities are 'Law Schools' (நியூயார்க் மற்றும் ஹார்வர்ட் சட்டக் கல்லூரியில்), whereas only Harvard is a law school in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0020-ta:wrong_language` (ta): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 134], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0021-ta:format` (ta): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 119], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0023-ta` (ta): omission
  - {"type": "omission", "severity": "minor", "field": "source", "span": [34, 41], "evidence": {"quote": "handily", "explanation": "The adverb 'handily' (meaning easily or by a large margin) is missing from the Tamil translation.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 0.9}
- `flores-0023-ta:entity` (ta): entity, omission, format
  - {"type": "entity", "severity": "major", "field": "source", "span": [107, 115], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
  - {"type": "omission", "severity": "minor", "field": "source", "span": [34, 41], "evidence": {"quote": "handily", "explanation": "The adverb 'handily' (meaning easily or by a large margin) is missing from the Tamil translation.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 0.9}
  - {"type": "format", "severity": "minor", "field": "translation", "span": [137, 155], "evidence": {"quote": "(DIFFERENT_ENTITY)", "explanation": "The source text contains '(Sample-A)' while the translation contains '(DIFFERENT_ENTITY)'. This is a format/content mismatch.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0025-ta:number` (ta): number, number
  - {"type": "number", "severity": "major", "field": "translation", "span": [37, 38], "evidence": {"observed": "8", "missing": ["5"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "major", "field": "translation", "span": [37, 40], "evidence": {"quote": "8-3", "explanation": "The source text states the score was '5-3', but the translation incorrectly states '8-3'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0026-ta` (ta): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [0, 32], "evidence": {"quote": "ஓவெச்ச்கின் உடைய இரவு முதல் உதவி", "explanation": "The phrase 'Ovechkin's first assist of the night' is mistranslated as 'Ovechkin's night first assist', which sounds unnatural and loses the intended meaning of the assist occurring during the game.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0026-ta:omission` (ta): omission, mistranslation, mistranslation
  - {"type": "omission", "severity": "critical", "field": "source", "span": [37, 68], "evidence": {"quote": "was on the game-winning goal by", "explanation": "The translation omits the core action of the sentence, specifically that the assist was on the game-winning goal.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [28, 32], "evidence": {"quote": "உதவி", "explanation": "The word 'assist' in a sports context is mistranslated as 'help/aid' (உதவி) instead of the appropriate sports terminology.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [0, 27], "evidence": {"quote": "ஓவெச்ச்கின் உடைய இரவு முதல்", "explanation": "The phrase 'Ovechkin's first assist of the night' is translated as 'Ovechkin's night first', losing the meaning of the assist entirely.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0027-ta:addition` (ta): addition
  - {"type": "addition", "severity": "minor", "field": "translation", "span": [247, 293], "evidence": {"quote": "This statement is not supported by the source.", "explanation": "The translator included an English meta-commentary at the end of the translation which is not present in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0028-ta:wrong_language` (ta): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 105], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0029-ta:format` (ta): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 180], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0031-ta:entity` (ta): entity, omission
  - {"type": "entity", "severity": "major", "field": "source", "span": [96, 104], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
  - {"type": "omission", "severity": "minor", "field": "source", "span": [95, 105], "evidence": {"quote": "(Sample-A)", "explanation": "The source text includes the tag '(Sample-A)', which was omitted in the translation and replaced with '(DIFFERENT_ENTITY)'.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
- `flores-0003-bn` (bn): number, number
  - {"type": "number", "severity": "major", "field": "translation", "span": [21, 23], "evidence": {"observed": "30", "missing": ["0230"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "major", "field": "translation", "span": [29, 31], "evidence": {"observed": "02", "missing": ["0230"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0003-bn:number` (bn): number, number, number, mistranslation
  - {"type": "number", "severity": "major", "field": "translation", "span": [19, 20], "evidence": {"observed": "2", "missing": ["9", "0230"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "major", "field": "translation", "span": [21, 23], "evidence": {"observed": "30", "missing": ["9", "0230"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "major", "field": "translation", "span": [29, 31], "evidence": {"observed": "02", "missing": ["9", "0230"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [14, 26], "evidence": {"quote": "সকাল 2:৩০ এর", "explanation": "The source text states '9:30 am', but the translation incorrectly states '2:30 am'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0008-bn` (bn): number
  - {"type": "number", "severity": "major", "field": "translation", "span": [111, 113], "evidence": {"observed": "+1", "missing": ["1"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0008-bn:omission` (bn): omission, omission
  - {"type": "omission", "severity": "critical", "field": "source", "span": [0, 51], "evidence": {"quote": "The protest started around 11:00 local time (UTC+1)", "explanation": "The entire first part of the source sentence regarding the start time and the protest itself is missing from the translation.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
  - {"type": "omission", "severity": "major", "field": "source", "span": [39, 51], "evidence": {"quote": "time (UTC+1)", "explanation": "The time zone information is missing from the translation.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
- `flores-0009-bn:addition` (bn): addition
  - {"type": "addition", "severity": "major", "field": "translation", "span": [81, 127], "evidence": {"quote": "This statement is not supported by the source.", "explanation": "The translator included an extraneous meta-commentary sentence that is not present in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0010-bn:wrong_language` (bn): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 161], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0015-bn` (bn): format
  - {"type": "format", "severity": "minor", "field": "translation", "span": [35, 150], "evidence": {"quote": "\"\"আমি সর্বাধিক গুরুত্বপূর্ণ ইভেন্টের চূড়ান্ত রাউন্ডে ফিরে আসতে পেরে খুবই উচ্ছ্বসিত। আমি এটি জেতার চেষ্টায় এসেছি।\"\"", "explanation": "The translation uses double double-quotes (\"\") inside the main quotation marks, which is a formatting error in the target text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0015-bn:format` (bn): format, format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 168], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
  - {"type": "format", "severity": "minor", "field": "translation", "span": [35, 151], "evidence": {"quote": "\"\"আমি সর্বাধিক গুরুত্বপূর্ণ ইভেন্টের চূড়ান্ত রাউন্ডে ফিরে আসতে পেরে খুবই উচ্ছ্বসিত। আমি এটি জেতার চেষ্টায় এসেছি।\"\"\"", "explanation": "The translation contains excessive and nested quotation marks that do not match the source text's formatting.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0016-bn:entity` (bn): entity
  - {"type": "entity", "severity": "major", "field": "source", "span": [149, 157], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
- `flores-0017-bn` (bn): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [28, 41], "evidence": {"quote": "চৌদ্দ ব্যাংকে", "explanation": "The source says 'fourteen banks' (subject), but the translation uses 'চৌদ্দ ব্যাংকে' which translates to 'in fourteen banks' (locative), incorrectly changing the grammatical role of the banks from the agents of the action to the location of the action.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0017-bn:number` (bn): number, number, mistranslation
  - {"type": "number", "severity": "major", "field": "translation", "span": [154, 156], "evidence": {"observed": "71", "missing": ["41"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "major", "field": "translation", "span": [153, 157], "evidence": {"quote": "[71]", "explanation": "The source text contains the citation '[41]', but the translation incorrectly changed this to '[71]'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [28, 41], "evidence": {"quote": "চৌদ্দ ব্যাংকে", "explanation": "The source says 'fourteen banks' (subject), but the translation uses 'চৌদ্দ ব্যাংকে' which translates to 'in fourteen banks' (locative), changing the grammatical role of the banks in the sentence.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0018-bn` (bn): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [49, 60], "evidence": {"quote": "ডাচ ব্যাঙ্ক", "explanation": "The source text refers to 'Deutsche Bank' (a specific German bank), but the translation uses 'ডাচ ব্যাঙ্ক' which translates to 'Dutch bank'. This is a factual error regarding the entity name.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0018-bn:omission` (bn): omission, mistranslation
  - {"type": "omission", "severity": "major", "field": "source", "span": [55, 134], "evidence": {"quote": "controlled roughly a third of the 1200 shell companies used to accomplish this.", "explanation": "The translation is incomplete; it ends abruptly after 'এক তৃতীয়াংশ' (a third), failing to translate the rest of the sentence including the purpose of the shell companies.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [49, 60], "evidence": {"quote": "ডাচ ব্যাঙ্ক", "explanation": "The source text refers to 'Deutsche Bank' (German bank), but the translation uses 'ডাচ ব্যাঙ্ক' (Dutch bank), which is a factual error.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0019-bn:addition` (bn): addition
  - {"type": "addition", "severity": "major", "field": "translation", "span": [127, 173], "evidence": {"quote": "This statement is not supported by the source.", "explanation": "The translator added an extraneous English sentence that is not present in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0020-bn` (bn): mistranslation
  - {"type": "mistranslation", "severity": "minor", "field": "translation", "span": [0, 5], "evidence": {"quote": "মা-এর", "explanation": "The source text refers to 'Ma' (a surname), but the translation uses 'মা-এর' which literally translates to 'Mother's' in Bengali. It should have been transliterated as 'মা'-এর.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0020-bn:wrong_language` (bn): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 134], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0021-bn:format` (bn): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 102], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0023-bn` (bn): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [75, 89], "evidence": {"quote": "প্রচার মাধ্যমে", "explanation": "The source text says 'on a platform advocating', which refers to a political platform (policy agenda). The translation incorrectly interprets 'platform' as 'media' (প্রচার মাধ্যম).", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.9}
- `flores-0023-bn:entity` (bn): entity, mistranslation
  - {"type": "entity", "severity": "major", "field": "source", "span": [107, 115], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [75, 89], "evidence": {"quote": "প্রচার মাধ্যমে", "explanation": "The source text says 'on a platform advocating', which refers to a political platform or policy agenda. The translation incorrectly translates 'platform' as 'media' (প্রচার মাধ্যম), changing the meaning of the sentence.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 0.95}
- `flores-0025-bn:number` (bn): number, number
  - {"type": "number", "severity": "major", "field": "translation", "span": [42, 43], "evidence": {"observed": "8", "missing": ["2", "2", "5"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "critical", "field": "translation", "span": [42, 45], "evidence": {"quote": "8-৩", "explanation": "The source text states the score was '5-3', but the translation incorrectly states '8-3'.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0026-bn:omission` (bn): omission
  - {"type": "omission", "severity": "major", "field": "source", "span": [48, 93], "evidence": {"quote": "game-winning goal by rookie Nicklas Backstrom", "explanation": "The translation omits the second half of the sentence, specifically the information about the game-winning goal and the full name of the player.", "source_field_repaired": true}, "check": "llm_judge", "confidence": 1.0}
- `flores-0027-bn:addition` (bn): addition
  - {"type": "addition", "severity": "major", "field": "translation", "span": [198, 244], "evidence": {"quote": "This statement is not supported by the source.", "explanation": "The translator added an English sentence at the end of the translation that is not present in the source text.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0028-bn:wrong_language` (bn): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 105], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0029-bn:format` (bn): format, format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 175], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
  - {"type": "format", "severity": "minor", "field": "translation", "span": [159, 175], "evidence": {"quote": "$x^2 + y^2 = z^2", "explanation": "The closing dollar sign ($) for the LaTeX math expression is missing in the translation.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0031-bn` (bn): mistranslation
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [0, 120], "evidence": {"quote": "কারাগারে বন্দীদের অত্যাচারের খোঁজ পাওয়ার পরে কারাগারটি কুখ্যাত হয়েছিল এবং তার পরে মার্কিন বাহিনী দায়িত্ব গ্রহণ করেছিল।", "explanation": "The translation incorrectly states that US forces took over after the prison became notorious. The source text states that the abuse was discovered after US forces took over.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
- `flores-0031-bn:entity` (bn): entity, mistranslation
  - {"type": "entity", "severity": "major", "field": "source", "span": [96, 104], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
  - {"type": "mistranslation", "severity": "major", "field": "translation", "span": [0, 120], "evidence": {"quote": "কারাগারে বন্দীদের অত্যাচারের খোঁজ পাওয়ার পরে কারাগারটি কুখ্যাত হয়েছিল এবং তার পরে মার্কিন বাহিনী দায়িত্ব গ্রহণ করেছিল।", "explanation": "The translation incorrectly states that US forces took over after the prison became notorious, whereas the source states the prison became notorious after US forces took over.", "source_field_repaired": false}, "check": "llm_judge", "confidence": 1.0}
