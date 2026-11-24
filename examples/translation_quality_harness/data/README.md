# Data

| File | Items | Languages | Purpose |
|---|---|---|---|
| `demo.jsonl` | 120 | hi, ta, bn (40 each) | Real Indic-Translate outputs: 60 untouched, 51 text injections, nine speech injections; 18 external audio clips |
| `flores_inputs.jsonl` | 120 | hi, ta, bn (40 each) | Original FLORES fixtures and English inputs for the hosted rebuild |
| `calibration.jsonl` | 90 | hi, ta, bn (30 each) | Labelled calibration subset used with `--calibration` to tune thresholds |
| `heldout_mr_te.jsonl` | 60 | mr, te (30 each) | Held-out languages, never used for tuning, to test generalization |

## Where the text comes from

All English sources and the human translations in the original fixtures are from [FLORES-101](https://github.com/facebookresearch/flores) (dev split for `flores_inputs.jsonl`, devtest split for calibration and held-out data), released by Meta AI under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). These files are derived works and are shared under the same license. Each row records its exact FLORES line numbers and article URL in `meta.provenance`.

`demo.jsonl` uses the same 120 English inputs, translated by the hosted `indic-translate` model. The builder discards the fixture's translation, reference and labels before generation, retains source provenance, and records a hash of each untouched model translation. It then injects errors into those outputs. `demo.build.json` records the dataset hash, generation spend, audio limits and any speech failures. Calibration and held-out translations remain independent human fixtures; their results are not measurements of Indic-Translate quality.

Generated audio is hosted in the [public asset release](https://github.com/Purv-Kabaria/Bodhan-AI-Cookbook/releases/tag/quality-harness-demo-v1), outside Git. Run `python scripts/fetch_demo_assets.py` before replaying speech checks. SHA-256 checksums are pinned in `demo_assets.json`.

Only short single-sentence sources (at most 180 characters) with speakable translations of at most 220 characters are eligible for audio, with at most six clips per language. Half of the selected clips are reserved as untouched speech controls. Paragraphs, Markdown and LaTeX receive text checks only.

Content mix per language: single sentences (about half with numbers), short paragraphs of 2 to 3 sentences from the same article, Markdown lists, numbered lists, tables and headings, and sentences with inline or display LaTeX. The original FLORES fixtures wrap parallel human text with Markdown and identical formulas. The hosted demo translates the complete formatted English input with Indic-Translate before injecting errors.

## Labels

**Every label in these files was produced by the programmatic error injector (`"annotator": "injector"`). None are human annotations.** No label was written by the author by hand and no LLM wrote any target-language text:

- omission, addition, mistranslation, number, term, format: programmatic edits of the existing translation (deleting a sentence or clause, inserting a sentence from another item, replacing a word with a word from another item, mutating digits, corrupting characters of a name, breaking Markdown or LaTeX);
- wrong_language: the same sentence from another language's FLORES translation (for Hindi: Marathi, Bengali, Tamil; for Bengali: Assamese, Hindi, Tamil), or one sentence left in the English source;
- term errors target the token that renders a source name; the LLM was asked only for that token's index (never for text).
- speech omission, repetition and garbling in the hosted demo are waveform edits of Indic-Speak output. Text offsets for a cut inside a synthesis chunk are approximate; chunk-boundary offsets are exact.

Clean items have `"labels": []`. Spans are character offsets into the translation unless the label says `"field": "source"`.

An empty label list means **no injected error**, not human-certified correctness. Both FLORES and real Bodhan outputs can contain natural errors. A valid finding on an unannotated natural error still counts as a false alarm against these synthetic labels.

## Rebuild

```bash
curl -L -o flores101.tgz https://dl.fbaipublicfiles.com/flores101/dataset/flores101_dataset.tar.gz && tar xzf flores101.tgz
python scripts/build_flores_dataset.py --flores-dir flores101_dataset --out outputs/flores-fixtures
python scripts/build_bodhan_demo.py --input data/flores_inputs.jsonl --out data/demo.jsonl --audio-dir data/audio --max-cost-inr 3
```
