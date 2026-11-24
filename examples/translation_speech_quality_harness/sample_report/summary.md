# Translation & speech quality report

**260 items scored** across 3 languages (bn, hi, ta); 133 flagged, 127 clean, 0 skipped (no translation), 0 errored, 0 malformed lines. Speech checked on 92 items. Judge: `openai/gpt-oss-120b`.

Model cost of the outputs used: **₹1.59** (₹0.61 per 100 items; actual spend this run is in run_meta.json).

## Quality vs labels

Measured on 260 labelled items (132 seeded/labelled errors, 128 verified-clean items).

| Target | Goal | Tuned (in-sample) | Cross-validated | Met? |
|---|---|---|---|---|
| Detection recall | >= 80% | 97% | 96% | ✅ |
| False alarms on clean items | <= 10% | 4% | 8% | ✅ |
| Localization | >= 70% | 92% | 92% | ✅ |
| Category accuracy | >= 70% | 100% | 100% | ✅ |
| Judge consistency (same verdict on re-judge) | >= 90% | 100% | (30 items) | ✅ |

Per category (tuned thresholds):

| Category | Precision | Recall | Detected (any flag) | Threshold |
|---|---|---|---|---|
| omission | 100% | 100% | 100% | 0.9 |
| addition | 92% | 100% | 100% | 0.6 |
| mistranslation | 37% | 100% | 100% | 0.85 |
| number | 100% | 100% | 100% | 0.9 |
| term | 80% | 80% | 80% | 0.9 |
| format | 100% | 95% | 95% | 0.9 |
| wrong_language | 100% | 100% | 100% | 0.6 |
| speech_omission | 96% | 96% | 96% | 0.7 |
| speech_repetition | 100% | 100% | 100% | 0.55 |
| speech_garbled | 100% | 100% | 100% | 0.65 |

## Per language

| Lang | Scored | Flagged | Critical | Major | Minor | MQM-style score | Top categories |
|---|---|---|---|---|---|---|---|
| bn | 85 | 41 (48%) | 12 | 34 | 0 | 82.07 | speech_omission 8, format 6, mistranslation 5 |
| hi | 87 | 45 (52%) | 13 | 34 | 2 | 81.39 | speech_omission 9, format 7, mistranslation 6 |
| ta | 88 | 47 (53%) | 15 | 35 | 3 | 80.05 | speech_omission 9, mistranslation 8, format 7 |

MQM-style score = 100 − 100 × (1·minor + 5·major + 10·critical) / source words.

## Per category

| Category | Flags | Items | By language | By severity |
|---|---|---|---|---|
| speech_omission | 26 | 24 | bn 8, hi 9, ta 9 | critical 20, major 6 |
| format | 20 | 20 | bn 6, hi 7, ta 7 | major 18, minor 2 |
| mistranslation | 19 | 19 | bn 5, hi 6, ta 8 | critical 1, major 16, minor 2 |
| addition | 13 | 13 | bn 4, hi 4, ta 5 | major 12, minor 1 |
| number | 13 | 12 | bn 4, hi 4, ta 5 | critical 10, major 3 |
| omission | 12 | 12 | bn 4, hi 4, ta 4 | major 12 |
| wrong_language | 12 | 12 | bn 4, hi 4, ta 4 | critical 9, major 3 |
| speech_repetition | 12 | 12 | bn 4, hi 4, ta 4 | major 12 |
| term | 11 | 10 | bn 4, hi 3, ta 4 | major 11 |
| speech_garbled | 10 | 10 | bn 3, hi 4, ta 3 | major 10 |

## Top failures

1. **ta · number (critical)** · `ent-03.ta.inj-number` · chars 11–12 · confidence 1.00  
   Source number '3' appears as '6' in the translation.  
   Flagged text: “6”  
   Source: “3”  
   Evidence from: judge, numbers
2. **ta · number (critical)** · `num-01.ta.inj-number` · chars 21–24 · confidence 1.00  
   Source number '100' appears as '200' in the translation.  
   Flagged text: “200”  
   Source: “100”  
   Evidence from: judge, numbers
3. **hi · number (critical)** · `num-04.hi.inj-number` · chars 24–25 · confidence 1.00  
   Source number '7' appears as '8' in the translation.  
   Flagged text: “8”  
   Source: “7”  
   Evidence from: judge, numbers
4. **bn · number (critical)** · `num-05.bn.inj-number` · chars 29–30 · confidence 1.00  
   Source number '3' appears as '৬' in the translation.  
   Flagged text: “৬”  
   Source: “3”  
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
7. **hi · number (critical)** · `num-08.hi.inj-number` · chars 26–28 · confidence 1.00  
   Source number '45' appears as '40' in the translation.  
   Flagged text: “40”  
   Source: “45”  
   Evidence from: judge, numbers
8. **bn · number (critical)** · `num-10.bn.inj-number` · chars 62–63 · confidence 1.00  
   Source number '4' appears as '৬' in the translation.  
   Flagged text: “৬”  
   Source: “4”  
   Evidence from: judge, numbers
9. **hi · number (critical)** · `ent-03.hi.inj-number` · chars 47–48 · confidence 0.98  
   Source number '3' appears as '0' in the translation.  
   Flagged text: “0”  
   Source: “3”  
   Evidence from: numbers
10. **ta · number (critical)** · `num-04.ta.inj-number` · chars 102–103 · confidence 0.98  
   Source number '9' appears as '6' in the translation.  
   Flagged text: “6”  
   Source: “9”  
   Evidence from: numbers
11. **bn · speech_omission (critical)** · `ent-01.bn.inj-truncation` · chars 36–114 · confidence 0.95  
   Words were not spoken (11 of 17 words missing from the transcript).  
   Flagged text: “করেছিলেন। পরে আলবার্ট আইনস্টাইন মহাকর্ষকে স্থান ও কালের বক্রতা হিসাবে ব্যাখ্যা”  
   Evidence from: asr_alignment
12. **hi · speech_omission (critical)** · `ent-01.hi.inj-truncation` · chars 45–129 · confidence 0.95  
   The end of the text was not spoken (16 of 25 words missing from the transcript).  
   Flagged text: “अल्बर्ट आइंस्टीन ने बाद में गुरुत्वाकर्षण को स्थान और समय के झुकने के रूप में समझाया”  
   Evidence from: asr_alignment

## How to read this

Every flag in items.jsonl has a category, severity, span (character offsets into the translation; speech errors point at the words that were not spoken correctly), the flagged text, and the evidence from each check that contributed. `sources` lists the checks that agreed. Back-translation and ASR are noisy, so neither can raise a flag on its own.
