# Translation and speech quality report

Status: complete

108 valid items, 0 invalid lines, 36 flagged items.

| Language | Items | Flagged | Partial |
|---|---:|---:|---:|
| bn | 36 | 12 | 0 |
| hi | 36 | 12 | 0 |
| ta | 36 | 12 | 0 |

## Detection measurements

{
  "labelled_items": 108,
  "clean_items": 54,
  "seeded_items": 54,
  "detection_recall": 0.6666666666666666,
  "false_alarm_rate": 0.0,
  "category_accuracy": 1.0,
  "localization_overlap": 0.75,
  "per_category": {
    "omission": {
      "tp": 0,
      "fp": 0,
      "fn": 9,
      "precision": null,
      "recall": 0.0
    },
    "addition": {
      "tp": 0,
      "fp": 0,
      "fn": 9,
      "precision": null,
      "recall": 0.0
    },
    "mistranslation": {
      "tp": 0,
      "fp": 0,
      "fn": 0,
      "precision": null,
      "recall": null
    },
    "number": {
      "tp": 9,
      "fp": 0,
      "fn": 0,
      "precision": 1.0,
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
      "fp": 1,
      "fn": 0,
      "precision": 0.9,
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
- detection_recall: 0.6666666666666666; target at least 0.8; missed.
- false_alarm_rate: 0.0; target at most 0.1; met.
- localization_overlap: 0.75; target at least 0.7; met.
- category_accuracy: 1.0; target at least 0.7; met.

Misses can reflect weak semantic judgments, conservative uncalibrated thresholds, noisy round trips, incomplete API checks, and approximate omission labels. Inspect per-item evidence before assigning a cause.

## Cost and runtime

See [execution.json](execution.json) for this invocation's estimated cost, cache/API counts, scope and elapsed time. Assessment artifacts stay identical on cached replays; execution cost falls to zero.

## Calibration and limits

Human-labelled calibration items: 0.
An unset precision or recall is null, including categories with no labelled positives.
Injected labels measure detection of controlled perturbations. They are not human judgments of natural model errors.
Back-translations and ASR are fallible. Unsupported claims remain in the review queue.
Judge agreement: None over 0 repeated items.

## Review first

- `flores-0003-hi:number` (hi): number
  - {"type": "number", "severity": "major", "field": "translation", "span": [13, 17], "evidence": {"observed": "3230", "missing": ["0230"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0010-hi:wrong_language` (hi): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 161], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0015-hi:format` (hi): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 169], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0016-hi:entity` (hi): entity
  - {"type": "entity", "severity": "major", "field": "source", "span": [149, 157], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
- `flores-0017-hi:number` (hi): number
  - {"type": "number", "severity": "major", "field": "translation", "span": [144, 146], "evidence": {"observed": "71", "missing": ["41"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0020-hi:wrong_language` (hi): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 134], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0021-hi:format` (hi): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 91], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0023-hi:entity` (hi): entity
  - {"type": "entity", "severity": "major", "field": "source", "span": [107, 115], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
- `flores-0025-hi:number` (hi): number
  - {"type": "number", "severity": "major", "field": "translation", "span": [32, 33], "evidence": {"observed": "8", "missing": ["5"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0028-hi:wrong_language` (hi): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 105], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0029-hi:format` (hi): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 152], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0031-hi:entity` (hi): format, entity
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 121], "evidence": {"component": "table", "expected": [], "observed": [2]}, "check": "format_integrity", "confidence": 0.95}
  - {"type": "entity", "severity": "major", "field": "source", "span": [96, 104], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
- `flores-0003-ta:number` (ta): number
  - {"type": "number", "severity": "major", "field": "translation", "span": [4, 6], "evidence": {"observed": "69", "missing": ["39"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0010-ta:wrong_language` (ta): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 161], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0015-ta:format` (ta): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 233], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0016-ta:entity` (ta): entity
  - {"type": "entity", "severity": "major", "field": "source", "span": [149, 157], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
- `flores-0017-ta:number` (ta): number
  - {"type": "number", "severity": "major", "field": "translation", "span": [187, 189], "evidence": {"observed": "71", "missing": ["41"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0020-ta:wrong_language` (ta): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 134], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0021-ta:format` (ta): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 119], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0023-ta:entity` (ta): entity
  - {"type": "entity", "severity": "major", "field": "source", "span": [107, 115], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
- `flores-0025-ta:number` (ta): number
  - {"type": "number", "severity": "major", "field": "translation", "span": [37, 38], "evidence": {"observed": "8", "missing": ["5"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0028-ta:wrong_language` (ta): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 105], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0029-ta:format` (ta): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 180], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0031-ta:entity` (ta): entity
  - {"type": "entity", "severity": "major", "field": "source", "span": [96, 104], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
- `flores-0003-bn:number` (bn): number, number, number
  - {"type": "number", "severity": "major", "field": "translation", "span": [19, 20], "evidence": {"observed": "2", "missing": ["9", "0230"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "major", "field": "translation", "span": [21, 23], "evidence": {"observed": "30", "missing": ["9", "0230"]}, "check": "number_preservation", "confidence": 0.98}
  - {"type": "number", "severity": "major", "field": "translation", "span": [29, 31], "evidence": {"observed": "02", "missing": ["9", "0230"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0010-bn:wrong_language` (bn): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 161], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0015-bn:format` (bn): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 168], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0016-bn:entity` (bn): entity
  - {"type": "entity", "severity": "major", "field": "source", "span": [149, 157], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
- `flores-0017-bn:number` (bn): number
  - {"type": "number", "severity": "major", "field": "translation", "span": [154, 156], "evidence": {"observed": "71", "missing": ["41"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0020-bn:wrong_language` (bn): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 134], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0021-bn:format` (bn): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 102], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0023-bn:entity` (bn): entity
  - {"type": "entity", "severity": "major", "field": "source", "span": [107, 115], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
- `flores-0025-bn:number` (bn): number
  - {"type": "number", "severity": "major", "field": "translation", "span": [42, 43], "evidence": {"observed": "8", "missing": ["2", "2", "5"]}, "check": "number_preservation", "confidence": 0.98}
- `flores-0028-bn:wrong_language` (bn): wrong_language
  - {"type": "wrong_language", "severity": "major", "field": "translation", "span": [0, 105], "evidence": {"reason": "English source was copied unchanged into a requested Indic output"}, "check": "untranslated_source", "confidence": 0.99}
- `flores-0029-bn:format` (bn): format
  - {"type": "format", "severity": "major", "field": "translation", "span": [0, 175], "evidence": {"component": "math", "expected": ["$x^2 + y^2 = z^2$"], "observed": []}, "check": "format_integrity", "confidence": 0.95}
- `flores-0031-bn:entity` (bn): entity
  - {"type": "entity", "severity": "major", "field": "source", "span": [96, 104], "evidence": {"term": "Sample-A", "allowed_targets": ["Sample-A"]}, "check": "explicit_glossary", "confidence": 0.9}
