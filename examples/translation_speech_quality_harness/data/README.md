# Demo data

| File | What it is |
| --- | --- |
| `own_sources.en.jsonl` | 16 English items written for this recipe: 10 structured passages (Markdown, tables, LaTeX, lists) and 6 heavy on names and numbers |
| `demo_clean.jsonl` | 40 sources × Hindi, Tamil, Bengali = 120 rows, with Indic-Translate translations and no injected errors |
| `demo.jsonl` | The demo set: `demo_clean.jsonl` with 60 errors injected (seed 7), 2 per category per language. The other 60 rows are clean |
| `demo_v2.jsonl` | The same items with different injected errors (seed 11), for the `--baseline` diff |
| `demo_labels.jsonl` | Hand checks of 30 untouched rows (see below) |
| `audio/` | Indic-Speak audio for 45 short items, 15 per language, plus injected variants. Not in git (48 MB): download [quality_harness_demo_audio.zip](https://github.com/Achindra2003/Bodhan-AI-Cookbook/releases/download/quality-harness-demo-assets/quality_harness_demo_audio.zip) and unzip it inside `data/` |

Without `audio/`, the speech checks on those rows report `not_run: audio file not found`, and every other check still runs.

## Sources and licences

- **24 news sentences from WMT24++** (`google/wmt24pp` on Hugging Face, Apache-2.0). They were picked by SHA-256 order among news segments of 60–300 characters and 10 or more words that no language marks as a bad source. The human post-edits are the `reference` field.
- **16 items written by the author of this recipe** (`own_sources.en.jsonl`), free to use.
- **Every translation and every audio file** comes from Bodhan models: Indic-Translate, greedy at temperature 0, and Indic-Speak. No Indian-language text in this folder was typed by hand or written by an LLM.

`tools/build_demo.py` rebuilds every file from these sources. It costs Bodhan credit, and every call is cached.

## Labels

Each row has a `labels` list. An empty list means the row is labelled clean. Every label has an `origin`:

- **`origin: "injected"`** (60 labels) was made by the error injector (`python -m bodhan_eval.inject`) with string edits, or by editing the English source and re-translating it with Indic-Translate. Each label has its category, severity, spans on both sides and the recipe used. A test checks, on every row, that the text outside the label's span is unchanged.
- **`origin: "human"`** (2 labels) is **our own annotation, added by the author of this recipe, not by Bodhan.** `demo_labels.jsonl` has `annotator: "candidate"` on each of its rows.

The hand-checked subset is 30 rows: 21 from our own sources and 7 WMT rows chosen by hash, plus the 2 errors found. The author does not read all three languages, so only checks that need no reading of the language were done: numbers and units, Markdown and LaTeX, script, and English left untranslated. Each row lists these in `checked`. Two real errors were found, both `format`, `minor`:

- `math-speed/ta` drops a `###` heading marker.
- `health-dose/hi` drops a heading marker and merges the heading into the paragraph.

Two rows looked wrong but are correct. `civics-election/ta` writes 910 million as "91 கோடி" (crore), and `ent-metro/bn` writes 6.5 million as "৬৫ লক্ষ" (lakh).

A row labelled clean has no *injected* error. It may still hold a real model error that nobody labelled, which is why `summary.md` quotes every flag on a labelled-clean row.
