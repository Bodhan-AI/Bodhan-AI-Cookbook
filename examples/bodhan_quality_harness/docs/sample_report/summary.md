# Translation and speech quality report

**108 items** across bn, hi, ta · **59 flags** on 53 items

## Headline

| Metric | Value | Target |
| --- | --- | --- |
| Detection recall | 96.1% | ≥ 80% |
| False alarms on clean items | 5.3% | ≤ 10% |
| Precision | 83.1% | — |
| F1 | 0.891 | — |

## Per category

| Category | TP | FP | FN | Precision | Recall | Localization | Category acc. |
| --- | --- | --- | --- | --- | --- | --- | --- |
| addition | 28 | 4 | 0 | 88% | 100% | 100% | 100% |
| language | 15 | 0 | 0 | 100% | 100% | 100% | 100% |
| number | 6 | 2 | 2 | 75% | 75% | 100% | 100% |
| omission | 0 | 1 | 0 | 0% | 0% | — | — |
| term | 0 | 3 | 0 | 0% | 0% | — | — |

## Per language

| Language | Items | Flags | Flags/item | Top categories |
| --- | --- | --- | --- | --- |
| bn | 36 | 17 | 0.47 | addition (7), language (6), number (2) |
| hi | 36 | 26 | 0.72 | addition (15), number (6), language (4) |
| ta | 36 | 16 | 0.44 | addition (10), language (5), term (1) |

## Top failures

- **src-004-hi--inj-number** · `number`/critical · 18–19 — source number '5' is missing from the translation (span points at the nearest output number; omission cannot be localized exactly)
- **src-004-ta--inj-language** · `language`/critical · 0–69 — expected tamil script for 'ta' but the output is Latin/other (0% in-script); also script.untranslated: the translation is identical to the English source (left untranslated)
- **src-008-hi** · `number`/critical · 19–22 — source number '20' is missing from the translation (span points at the nearest output number; omission cannot be localized exactly)
- **src-009-hi--inj-number** · `number`/critical · 24–28 — '1957' appears in the translation but not in the source (context: भारतीय संविधान 26 जनवरी 1957 को लागू हुआ।); also numbers.missing: source number '1950' is missing from the translation (span points at the nearest output number; omission cannot be localized exactly)
- **src-009-ta--inj-language** · `language`/critical · 0–59 — expected tamil script for 'ta' but the output is Latin/other (0% in-script); also script.untranslated: the translation is identical to the English source (left untranslated)
- **src-009-bn--inj-language** · `language`/critical · 0–59 — expected bengali script for 'bn' but the output is Latin/other (0% in-script); also script.untranslated: the translation is identical to the English source (left untranslated)
- **src-013-ta--inj-language** · `language`/critical · 0–62 — expected tamil script for 'ta' but the output is Latin/other (0% in-script); also script.untranslated: the translation is identical to the English source (left untranslated)
- **src-014-hi--inj-language** · `language`/critical · 0–44 — expected devanagari script for 'hi' but the output is Latin/other (0% in-script); also script.untranslated: the translation is identical to the English source (left untranslated)
- **src-016-hi--inj-number** · `number`/critical · 16–18 — '00' appears in the translation but not in the source (context: एक समकोण का माप 00 डिग्री होता है।); also numbers.missing: source number '90' is missing from the translation (span points at the nearest output number; omission cannot be localized exactly)
- **src-016-bn--inj-number** · `number`/critical · 20–22 — '93' appears in the translation but not in the source (context: একটি সমকোণের পরিমাপ ৯3 ডিগ্রি।); also numbers.missing: source number '90' is missing from the translation (span points at the nearest output number; omission cannot be localized exactly)

## Cost and runtime

- Total: **₹0.00** over 0s
- Cache: 0 hits / 0 misses (0% hit rate)

---

Deterministic digest: `2e6b34b0d36bdfb7` — a cached rerun must reproduce this exactly at zero model cost.
