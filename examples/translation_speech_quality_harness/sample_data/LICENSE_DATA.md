# Data licences and attribution

| File | Content | Licence |
|---|---|---|
| `fleurs_sources.jsonl` | English sentences and their human Hindi, Tamil and Bengali versions, taken from the FLEURS text files (`raw_transcription` column). | FLEURS: CC BY 4.0. The underlying sentences come from FLoRes-101 (Wikinews, Wikijunior and Wikivoyage) and are CC BY-SA 4.0. |
| `markdown_sources.jsonl` | 8 short English Markdown passages (tables, LaTeX, a link, a code fence) written for this recipe. | CC0 1.0 |
| `demo.jsonl`, `fresh_seed19.jsonl` | The sources above, Bodhan Indic-Translate outputs, and errors injected programmatically by `bodhan_eval.inject` (seed 7 for `demo.jsonl`; seed 19 across the 8 free categories for `fresh_seed19.jsonl`). | CC BY-SA 4.0 (share-alike, inherited from FLoRes) |

How the dataset was built: `fleurs_sources.jsonl` contains 38 English sentences (20 short, 12 single-sentence, 6 two-sentence pairs) and their human Hindi, Tamil, and Bengali references sampled with a fixed seed from the Hugging Face `google/fleurs` test TSVs (`raw_transcription` column, matching utterance IDs across `en`, `hi`, `ta`, and `bn`), paired with 8 original English Markdown/LaTeX passages in `markdown_sources.jsonl`. Running `scripts/build_demo.py` combines these 46 sources across 3 languages (138 items), translates and synthesizes them with Bodhan `indic-translate` / `indic-speak`, injects 72 programmatic errors (balanced per category, seed 7), and attaches the 5 author-reviewed pre-existing Bodhan defects from `known_issues.jsonl`.

Attribution:
- A. Conneau et al., "FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech", 2022. https://huggingface.co/datasets/google/fleurs
- N. Goyal et al., "The FLORES-101 Evaluation Benchmark for Low-Resource and Multilingual Machine Translation", 2021.

Only FLEURS text was used. No FLEURS audio is included or needed. Demo audio is generated with Bodhan Indic-Speak and kept out of git.

Note: FLoRes is a widely used MT benchmark and may be part of Bodhan's training data, so clean demo translations may look better than translations of unseen text.
