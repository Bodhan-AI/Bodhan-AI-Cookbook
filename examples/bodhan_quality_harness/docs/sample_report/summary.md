# Translation and speech quality report

**103 items** across bn, hi, ta · **49 flags** on 39 items

## Headline

| Metric | Value | Target |
| --- | --- | --- |
| Detection recall | 78.3% | ≥ 80% |
| False alarms on clean items | 3.5% | ≤ 10% |
| Precision | 73.5% | — |
| F1 | 0.758 | — |

## Per category

| Category | TP | FP | FN | Precision | Recall | Localization | Category acc. |
| --- | --- | --- | --- | --- | --- | --- | --- |
| addition | 16 | 7 | 2 | 70% | 89% | 100% | 100% |
| language | 12 | 1 | 0 | 92% | 100% | 100% | 100% |
| mistranslation | 1 | 0 | 6 | 100% | 14% | 100% | 0% |
| number | 7 | 2 | 1 | 78% | 88% | 100% | 100% |
| omission | 0 | 2 | 0 | 0% | 0% | — | — |
| term | 0 | 1 | 1 | 0% | 0% | — | — |

## Per language

| Language | Items | Flags | Flags/item | Top categories |
| --- | --- | --- | --- | --- |
| bn | 34 | 18 | 0.53 | addition (11), language (4), number (1) |
| hi | 35 | 11 | 0.31 | addition (6), number (3), language (2) |
| ta | 34 | 20 | 0.59 | addition (7), language (7), number (5) |

## Top failures

- **src-004-hi--inj-number** · `number`/critical · 18–19 — source number '5' is missing from the translation (span points at the nearest output number; omission cannot be localized exactly)
- **src-007-ta--inj-language** · `language`/critical · 0–47 — expected tamil script for 'ta' but the output is Latin/other (0% in-script); also script.untranslated: the translation is identical to the English source (left untranslated)
- **src-009-ta--inj-number** · `number`/critical · 20–24 — '1150' appears in the translation but not in the source (context: இந்திய அரசியலமைப்பு 1150 ஜனவரி 26 அன்று நடைமுறைக்கு வந...)
- **src-009-ta--inj-number** · `number`/critical · 31–33 — source number '1950' is missing from the translation (span points at the nearest output number; omission cannot be localized exactly)
- **src-009-bn--inj-language** · `language`/critical · 0–59 — expected bengali script for 'bn' but the output is Latin/other (0% in-script); also script.untranslated: the translation is identical to the English source (left untranslated)
- **src-013-ta--inj-language** · `language`/critical · 0–62 — expected tamil script for 'ta' but the output is Latin/other (0% in-script); also script.untranslated: the translation is identical to the English source (left untranslated)
- **src-014-bn--inj-language** · `language`/critical · 0–44 — expected bengali script for 'bn' but the output is Latin/other (0% in-script); also script.untranslated: the translation is identical to the English source (left untranslated)
- **src-016-hi--inj-number** · `number`/critical · 9–11 — '00' appears in the translation but not in the source (context: एक समकोण 00 डिग्री का होता है।); also numbers.missing: source number '90' is missing from the translation (span points at the nearest output number; omission cannot be localized exactly)
- **src-016-bn--inj-number** · `number`/critical · 20–22 — '93' appears in the translation but not in the source (context: একটি সমকোণের পরিমাপ ৯3 ডিগ্রি।); also numbers.missing: source number '90' is missing from the translation (span points at the nearest output number; omission cannot be localized exactly)
- **src-021-ta--inj-addition** · `language`/critical · 0–80 — expected tamil script for 'ta' but the output is Latin/other (0% in-script)

## Cost and runtime

- Total: **₹0.00** over 0s
- Cache: 0 hits / 0 misses (0% hit rate)

---

Deterministic digest: `c847820d47cef00d` — a cached rerun must reproduce this exactly at zero model cost.
