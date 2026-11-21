
# Scanned Chapter Narrated Lesson

> Converts scanned textbook PDF chapters into structured Markdown study guides and spoken audio lessons in Indian languages.

**Bodhan models:** indic-ocr, indic-translate, indic-speak, indic-transcribe
**Languages shown:** English, Hindi, Marathi, Tamil, Bengali
**Format:** CLI

## What it does

Takes a scanned textbook PDF and page range as input and produces a clean Markdown study guide (`lesson_<lang>.md`) alongside per-section and full-chapter WAV audio narration (`audio_<lang>/full_lesson.wav`). Built for educators, edtech developers, and students who want accessible multilingual audio lessons from print textbooks without manual retyping or voiceover recording.

Sample output structure:
```text
outputs/hindi_lesson/
├── lesson_hi.md
└── audio_hi/
    ├── sec_1.wav
    ├── sec_2.wav
    ├── sec_3.wav
    └── full_lesson.wav ```

## Setup

```bash

cd examples/scanned-chapter-narrated-lesson
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your keys from [https://console.bodhan.ai](https://console.bodhan.ai)
```

## Run
```bash 

# Translate and narrate specific pages into Hindi with ASR audio verification
 PYTHONPATH=src python -m bodhan_lesson --pdf sample_data/chapter.pdf --pages 1-2 --lang hi --voice Kavya --verify-asr --out outputs/hindi_lesson 

# Run on geometry/math pages in Marathi
 PYTHONPATH=src python -m bodhan_lesson --pdf sample_data/chapter.pdf --pages 17-18 --lang mr --voice Kavya --out outputs/marathi_lesson

# Process an entire PDF
 PYTHONPATH=src python -m bodhan_lesson --pdf sample_data/chapter.pdf --pages all --lang ta --out outputs/tamil_full 

## How it works

1.Extract text and layout blocks from scanned PDF pages using indic-ocr (POST /v1/chat/completions), caching page JSON locally.

2.Clean OCR noise and raw LaTeX escapes into Unicode math symbols with an LLM (gemini-3-flash-preview), generating structured study sections, practice questions, and a spoken narration script.

3.Translate headings, study notes, narration scripts, and practice questions into the target Indian language with indic-translate using batched requests and rate-limit backoff.

4.Synthesize sentence-chunked narration audio with indic-speak, concatenate per-section and full_lesson.wav files, and optionally verify the opening audio clip with indic-transcribe.


## Limitations

- Complex diagrams or unlabeled geometric figures are flagged in low_confidence_notes rather than visually interpreted.

- Each indic-speak request is chunked under ~240 characters to stay within the ~30-second per-request speech limit.

- Requires Poppler on your system PATH when converting PDF pages to images via pdf2image.

---


