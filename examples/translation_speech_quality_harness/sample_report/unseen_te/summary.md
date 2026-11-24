# Translation & speech quality report

**12 items scored** across 1 languages (te); 1 flagged, 11 clean, 0 skipped (no translation), 0 errored, 0 malformed lines. Speech checked on 6 items. Judge: `openai/gpt-oss-120b`.

Model cost of the outputs used: **₹0.49** (₹4.09 per 100 items; actual spend this run is in run_meta.json).

## Per language

| Lang | Scored | Flagged | Critical | Major | Minor | MQM-style score | Top categories |
|---|---|---|---|---|---|---|---|
| te | 12 | 1 (8%) | 0 | 1 | 0 | 97.72 | mistranslation 1 |

MQM-style score = 100 − 100 × (1·minor + 5·major + 10·critical) / source words.

## Per category

| Category | Flags | Items | By language | By severity |
|---|---|---|---|---|
| mistranslation | 1 | 1 | te 1 | major 1 |

## Top failures

1. **te · mistranslation (major)** · `md-01.te` · chars 54–58 · confidence 0.87  
   The source term 'Root' is translated as "[…]", which means 'different' or 'separate' and does not convey the botanical meaning of 'root'. The correct Telugu term should be "[…]" or similar.  
   Flagged text: “వేరు”  
   Source: “Root”  
   Evidence from: judge

## How to read this

Every flag in items.jsonl has a category, severity, span (character offsets into the translation; speech errors point at the words that were not spoken correctly), the flagged text, and the evidence from each check that contributed. `sources` lists the checks that agreed. Back-translation and ASR are noisy, so neither can raise a flag on its own.
