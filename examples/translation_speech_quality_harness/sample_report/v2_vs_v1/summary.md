# Translation & speech quality report

**260 items scored** across 3 languages (bn, hi, ta); 130 flagged, 130 clean, 0 skipped (no translation), 0 errored, 0 malformed lines. Speech checked on 92 items. Judge: `openai/gpt-oss-120b`.

Model cost of the outputs used: **₹1.58** (₹0.61 per 100 items; actual spend this run is in run_meta.json).

## Quality vs labels

Measured on 260 labelled items (123 seeded/labelled errors, 137 verified-clean items).

| Target | Goal | Tuned (in-sample) | Cross-validated | Met? |
|---|---|---|---|---|
| Detection recall | >= 80% | 97% | 94% | ✅ |
| False alarms on clean items | <= 10% | 8% | 9% | ✅ |
| Localization | >= 70% | 95% | 95% | ✅ |
| Category accuracy | >= 70% | 96% | 97% | ✅ |

Per category (tuned thresholds):

| Category | Precision | Recall | Detected (any flag) | Threshold |
|---|---|---|---|---|
| omission | 100% | 80% | 95% | 0.9 |
| addition | 80% | 100% | 100% | 0.6 |
| mistranslation | 33% | 100% | 100% | 0.85 |
| number | 100% | 87% | 100% | 0.9 |
| term | 47% | 78% | 78% | 0.9 |
| format | 45% | 100% | 100% | 0.9 |
| wrong_language | 100% | 100% | 100% | 0.6 |
| speech_omission | 96% | 96% | 96% | 0.7 |
| speech_repetition | 100% | 100% | 100% | 0.55 |
| speech_garbled | 100% | 100% | 100% | 0.65 |

## Per language

| Lang | Scored | Flagged | Critical | Major | Minor | MQM-style score | Top categories |
|---|---|---|---|---|---|---|---|
| bn | 85 | 41 (48%) | 12 | 36 | 0 | 81.45 | speech_omission 8, format 7, omission 6 |
| hi | 87 | 42 (48%) | 12 | 35 | 1 | 81.76 | speech_omission 9, format 6, omission 6 |
| ta | 88 | 47 (53%) | 16 | 39 | 2 | 78.28 | format 9, speech_omission 9, term 7 |

MQM-style score = 100 − 100 × (1·minor + 5·major + 10·critical) / source words.

## Per category

| Category | Flags | Items | By language | By severity |
|---|---|---|---|---|
| speech_omission | 26 | 24 | bn 8, hi 9, ta 9 | critical 20, major 6 |
| format | 22 | 22 | bn 7, hi 6, ta 9 | major 21, minor 1 |
| term | 18 | 15 | bn 6, hi 5, ta 7 | major 18 |
| omission | 16 | 16 | bn 6, hi 6, ta 4 | major 16 |
| mistranslation | 15 | 15 | bn 5, hi 4, ta 6 | critical 1, major 12, minor 2 |
| number | 14 | 13 | bn 4, hi 4, ta 6 | critical 12, major 2 |
| speech_repetition | 12 | 12 | bn 4, hi 4, ta 4 | major 12 |
| addition | 10 | 10 | bn 2, hi 3, ta 5 | major 10 |
| wrong_language | 10 | 10 | bn 3, hi 3, ta 4 | critical 7, major 3 |
| speech_garbled | 10 | 10 | bn 3, hi 4, ta 3 | major 10 |

## Top failures

1. **ta · number (critical)** · `ent-03.ta.inj-number` · chars 11–12 · confidence 1.00  
   Source number '3' appears as '6' in the translation.  
   Flagged text: “6”  
   Source: “3”  
   Evidence from: judge, numbers
2. **ta · number (critical)** · `num-01.ta` · chars 54–59 · confidence 1.00  
   Source number '3,000' appears as '3,700' in the translation.  
   Flagged text: “3,700”  
   Source: “3,000”  
   Evidence from: judge, numbers
3. **ta · number (critical)** · `num-01.ta.inj-number` · chars 21–24 · confidence 1.00  
   Source number '100' appears as '200' in the translation.  
   Flagged text: “200”  
   Source: “100”  
   Evidence from: judge, numbers
4. **hi · number (critical)** · `num-04.hi.inj-number` · chars 24–25 · confidence 1.00  
   Source number '7' appears as '8' in the translation.  
   Flagged text: “8”  
   Source: “7”  
   Evidence from: judge, numbers
5. **ta · number (critical)** · `num-06.ta.inj-number` · chars 98–99 · confidence 1.00  
   Source number '8' appears as '9' in the translation.  
   Flagged text: “9”  
   Source: “8”  
   Evidence from: judge, numbers
6. **bn · number (critical)** · `num-07.bn.inj-number` · chars 32–34 · confidence 1.00  
   Source number '56' appears as '৭৬' in the translation.  
   Flagged text: “৭৬”  
   Source: “56”  
   Evidence from: judge, numbers
7. **bn · number (critical)** · `num-08.bn` · chars 32–34 · confidence 1.00  
   Source number '45' appears as '৬৫' in the translation.  
   Flagged text: “৬৫”  
   Source: “45”  
   Evidence from: judge, numbers
8. **hi · number (critical)** · `num-08.hi.inj-number` · chars 26–28 · confidence 1.00  
   Source number '45' appears as '40' in the translation.  
   Flagged text: “40”  
   Source: “45”  
   Evidence from: judge, numbers
9. **bn · number (critical)** · `num-10.bn.inj-number` · chars 62–63 · confidence 1.00  
   Source number '4' appears as '৬' in the translation.  
   Flagged text: “৬”  
   Source: “4”  
   Evidence from: judge, numbers
10. **bn · number (critical)** · `num-06.bn` · chars 11–17 · confidence 1.00  
   Source number '300,000' appears as '০০০০০০' in the translation.  
   Flagged text: “০০০০০০”  
   Source: “300,000”  
   Evidence from: judge, numbers
11. **hi · number (critical)** · `ent-03.hi.inj-number` · chars 47–48 · confidence 0.98  
   Source number '3' appears as '0' in the translation.  
   Flagged text: “0”  
   Source: “3”  
   Evidence from: numbers
12. **ta · number (critical)** · `num-04.ta.inj-number` · chars 102–103 · confidence 0.98  
   Source number '9' appears as '6' in the translation.  
   Flagged text: “6”  
   Source: “9”  
   Evidence from: numbers

## Regressions vs baseline

21 new flags (21 major or worse), 16 resolved; 11 items newly flagged, 14 newly clean. Details in diff.md.

## How to read this

Every flag in items.jsonl has a category, severity, span (character offsets into the translation; speech errors point at the words that were not spoken correctly), the flagged text, and the evidence from each check that contributed. `sources` lists the checks that agreed. Back-translation and ASR are noisy, so neither can raise a flag on its own.
