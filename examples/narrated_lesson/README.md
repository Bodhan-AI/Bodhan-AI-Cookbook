# narrated_lesson

Turns scanned textbook pages into a lesson translated into Indian languages, for teachers and students. Status: OCR, page assembly and translation work end to end. Narration audio is not implemented yet (see Limitations).

**Bodhan models:** indic-ocr and indic-translate are used by the pipeline. indic-speak and indic-transcribe are wired into the client (`client.py`) but no stage calls them yet.
**Languages shown:** any code that indic-translate accepts, for example hi, ta, bn
**Format:** CLI (`python -m bodhan_lesson`)

## What it does

Give it a PDF or a folder of page images (one textbook chapter) and a list of target languages. It reads every page with indic-ocr, assembles one clean source lesson (headers, footers and page codes removed, figure labels grouped with their figure, paragraphs split across pages rejoined), then translates it with indic-translate while keeping equations, numbers and structure intact. Every run writes `report.json` with the problems found, the cost and the time.

Pages and scans are not stored in this repository. The demo chapter is NCERT Class 9 Mathematics, Chapter 6 (Lines and Angles), a 20 page PDF at https://www.aspirationsinstitute.com/wp-content/uploads/2021/01/9-Maths-NCERT-Book-Lines-and-Angles.pdf

## Setup

System tools: Python 3.10 or newer, and poppler (used to render PDF pages). On Windows with conda: `conda install -c conda-forge poppler`. On macOS: `brew install poppler`. On Linux: `sudo apt install poppler-utils`.

```bash
cd examples/narrated_lesson
python -m venv .venv
# Mac/Linux: source .venv/bin/activate
# Windows:   .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env   # then paste your keys from https://console.bodhan.ai
```

Bodhan issues one key per model. The pipeline needs `BODHAN_OCR_KEY` and `BODHAN_TRANSLATE_KEY`. `BODHAN_SPEAK_KEY` and `BODHAN_TRANSCRIBE_KEY` are read only when a stage that uses them runs. The `LLM_*` variables are for the companion LLM, which no stage uses yet.

## Run

```bash
python -m bodhan_lesson --input path/to/chapter.pdf --langs hi,ta,bn --out ./out
```

`--input` takes a PDF or a folder of PNG or JPG pages. Optional flags: `--max-pages 3` for a cheap trial run, and `--workers 4` for parallel translation calls. The exit code is 0 when everything worked, 1 when a page or language had a problem (details in `report.json`), and 2 for a setup error such as a missing key.

Outputs in the output folder:

| Path | Contents |
| --- | --- |
| `source/pages/page_001.json` | raw OCR result per page, with block labels, boxes, confidence and flags |
| `source/lesson.src.md` | the assembled source lesson, with `<!-- flagged -->` comments on doubtful blocks |
| `source/lesson.structure.json` | the same lesson as typed elements and sections |
| `source/assembly_report.json` | what assembly dropped, merged and flagged |
| `hi/lesson.hi.md` | the translated lesson (one folder per language) |
| `report.json` | problems, per stage time, cost per page and per language |

Every API response is cached under `.cache/`, so a rerun does not repeat paid calls.

## How it works

1. **OCR.** Pages are rendered (200 dpi), rotated upright, capped at 2400 pixels, and read with indic-ocr, two pages at a time. A page the model refuses (a "truncated" error) is retried, then cut in half at a blank row and read in halves, down to quarters. A region that still fails becomes a flagged placeholder, so one bad area does not lose the page.
2. **Assembly.** The text comes from the page Markdown (it keeps word spacing), while the blocks supply labels, positions and confidence. Rules remove running headers, page numbers, website banners and catalog codes, group labels drawn inside a diagram with their figure and caption, and rejoin a paragraph that continues on the next page.
3. **Translation.** Math is swapped for `⟦M1⟧` placeholders, several short pieces share one call, and every result is checked (each placeholder once, same numerals). A piece that fails is retried alone, then sentence by sentence, then kept in English, flagged and listed in the report.

Diagrams, the flow chart and the design notes are in [docs/design.md](docs/design.md).

## Limitations

- **No narration audio.** The narration script, speech synthesis (indic-speak) and the back-transcription quality check (indic-transcribe) are not built. The report lists them under `not_implemented`.
- **Tested on one chapter.** Only the English demo chapter, a clean flatbed scan, was run. Phone photos, multi-column pages, handwriting and Indian-language source chapters are untested.
- **Tables** have translation code, tested only on synthetic input, because the demo chapter has none.
- **No glossary.** Term consistency across a chapter is not enforced or measured.
- **Units** are translated, for example "5 cm" becomes "5 सेमी". Numerals and LaTeX are checked against the source, units are not.
- **The LLM is not used yet.** Reading order is only sanity checked by position, not repaired.
- **Figures** are kept as caption plus labels. The pictures themselves are not described.

## Tests

```bash
python -m pytest tests -q
```

The tests cover assembly rules on real OCR output, the page-split fallback, translation validation and batching, and a two page end-to-end smoke test with a fake client (no network, no cost).


## Output
- Reports from the demo run: docs/demo_reports/report.json
- Outputs: https://drive.google.com/drive/folders/1JKMvSSlU8dq4ifB_C_Ps2Sri37Trcshc?usp=sharing
