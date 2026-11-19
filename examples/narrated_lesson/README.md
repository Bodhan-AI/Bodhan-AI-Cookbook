# narrated_lesson

Turns scanned textbook pages into a translated lesson with section by section narration audio, for teachers and students working in Indian languages.

**Bodhan models:** indic-ocr, indic-translate, indic-speak, indic-transcribe
**Languages shown:** Hindi, Tamil, Bengali (source pages in English)
**Format:** CLI (`python -m bodhan_lesson`)

## What it does

Give it a folder of page images or a scanned PDF of one textbook chapter (5 to 30 pages) and a list of target languages. It reads every page with Indic-OCR, assembles one clean source lesson, translates it while keeping tables, equations and numbers intact, writes a narration script that explains tables, equations and figures in words, and synthesizes audio with Indic-Speak. Each language gets a translated Markdown lesson, one audio file per section and a full lesson track. A `report.json` records the checks, retries, time and cost of the run.

All narration audio is AI-generated.

## Setup

System dependencies: Python 3.10 or newer, ffmpeg and poppler (needed to render PDF pages).

```bash
cd examples/narrated_lesson
python -m venv .venv
# Mac/Linux: source .venv/bin/activate
# Windows:   .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env   # then paste your keys from https://console.bodhan.ai
```

The `.env` file needs one key per Bodhan model (`BODHAN_OCR_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_TRANSCRIBE_KEY`) and an OpenAI-compatible LLM endpoint (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`).

## Run

```bash
python -m bodhan_lesson --input path/to/scans/ --langs hi,ta,bn --out ./out
```

`--input` takes a folder of PNG or JPG pages or a single PDF. Optional flags: `--source-lang`, `--glossary`, `--voices` and `--code-mixed`. Language codes are plain ISO codes such as `hi`, `ta` and `bn`.

## How it works

1. Render pages to images and read each one with `indic-ocr`, keeping the raw output and a flag for doubtful blocks.
2. Assemble one source lesson: drop repeated headers and footers, rejoin text and tables split across pages, and repair reading order.
3. Translate each section with `indic-translate`, protecting LaTeX and numbers, and check that the structure survived.
4. Write an English narration script, with the LLM deciding how to read tables, equations and figures aloud, then translate it with `indic-translate`.
5. Synthesize each section with `indic-speak` in short chunks, back-transcribe it with `indic-transcribe` to measure the character error rate, and retry on repetition or omission.
6. Normalize loudness, join the sections into a full lesson track with section markers, and write `report.json`.

## Limitations

Indic-Speak requests are kept short, so long sections are split into chunks. Translation between two Indian languages goes through English. Handwriting and low quality scans can produce doubtful OCR, which is flagged and reported rather than translated silently. Cost depends mostly on narration length, since speech synthesis is the most expensive step.