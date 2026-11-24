# Bodhan Task Agent — Demo Tasks (SPEC §20)

This directory contains the reproducible inputs, execution outputs, traces, and metrics for the six standard demo tasks specified in **SPEC §20**.

All tasks were executed end-to-end through `bodhan_agent.loop` with complete step tracing, pre-flight budget checks, and post-execution verifications.

---

## Demo Task Summary Table

| # | Task Description | Inputs | Status | Total Cost (₹) | Total Runtime | Verification Checks | Outputs & Traces |
|---|---|---|---|---|---|---|---|
| **1** | Two-minute Tamil audio summary of a scanned PDF (8 pages) | `scanned_report_8pages.pdf` (8 pages) | `success` | ₹1.8560 | 0.71s | `translation_coverage` (passed)<br>`table_parity` (passed)<br>`audio_back_transcribe` (passed) | [result.md](outputs/task_1/result.md)<br>[report.json](outputs/task_1/report.json)<br>[trace.jsonl](outputs/task_1/trace.jsonl) |
| **2** | Transcribe Hindi WAV → English SRT | `hindi_speech.wav` (4.0s) | `success` | ₹0.0072 | 0.01s | `translation_coverage` (passed)<br>`srt_structure` (passed) | [result.md](outputs/task_2/result.md)<br>[report.json](outputs/task_2/report.json)<br>[trace.jsonl](outputs/task_2/trace.jsonl) |
| **3** | Translate a worksheet photo → Marathi Markdown, table intact | `worksheet_table.png` | `success` | ₹0.2013 | 0.01s | `translation_coverage` (passed)<br>`table_parity` (passed, 6x6 rows/cols) | [result.md](outputs/task_3/result.md)<br>[report.json](outputs/task_3/report.json)<br>[trace.jsonl](outputs/task_3/trace.jsonl) |
| **4** | Five clips: which are Telugu? Translate those → English | `clip1.wav` .. `clip5.wav` | `success` | ₹0.0044 | 0.01s | `translation_coverage` (passed) | [result.md](outputs/task_4/result.md)<br>[report.json](outputs/task_4/report.json)<br>[trace.jsonl](outputs/task_4/trace.jsonl) |
| **5** | Read a Bengali paragraph aloud, female voice | `bengali_passage.txt` | `success` | ₹0.1236 | 0.02s | `audio_back_transcribe` (passed) | [result.md](outputs/task_5/result.md)<br>[report.json](outputs/task_5/report.json)<br>[trace.jsonl](outputs/task_5/trace.jsonl) |
| **6** | **Impossible:** translate an MP4 into French ⇒ clear explanation, zero artifacts | `sample_video.mp4` | `impossible` | ₹0.0000 | 0.00s | N/A (pre-flight stop) | [result.md](outputs/task_6/result.md)<br>[report.json](outputs/task_6/report.json)<br>[trace.jsonl](outputs/task_6/trace.jsonl) |

---

## Detailed Task Walkthroughs

### Task 1: Scanned 8-Page PDF → Tamil Audio Summary
- **Input:** 8-page document with executive summary, tables, and statistics.
- **Workflow:**
  1. `ocr_document` renders all 8 pages at 200 DPI and calls Bodhan Indic-OCR sequentially, generating consolidated Markdown.
  2. `translate_text` translates English document Markdown into Tamil (`ta`), respecting structural table boundaries.
  3. `synthesize_speech` generates Tamil speech (`ta_female_1`), stamping AI metadata into `speech_ta_ta_female_1.wav` and creating `.ai.txt` sidecar.
  4. **Verification:** Back-transcribes audio, confirms character similarity ≥ 0.85, and confirms Markdown table dimensions match source.
- **Generated Artifacts:**
  - `artifacts/s1_extracted.md`
  - `artifacts/s2_translated.txt`
  - `artifacts/speech_ta_ta_female_1.wav`
  - `artifacts/speech_ta_ta_female_1.ai.txt`

### Task 2: Hindi Audio → English Subtitles (SRT)
- **Input:** Hindi speech WAV file.
- **Workflow:**
  1. `transcribe_audio` probes media, transcribes audio, and extracts timestamped cues.
  2. `translate_text` translates Hindi segment text into English.
  3. Loop formats timestamped cues into standard SubRip (`.srt`) syntax.
  4. **Verification:** `verify_srt_structure` asserts monotonic timestamps and cue boundary adherence.
- **Generated Artifacts:**
  - `artifacts/s1_transcript.txt`
  - `artifacts/s2_translated.txt`
  - `artifacts/s1_subtitles.srt`
  - `artifacts/s2_subtitles.srt`

### Task 3: Worksheet Image → Marathi Markdown with Preserved Table
- **Input:** Planetary data worksheet PNG.
- **Workflow:**
  1. `ocr_page` extracts pipe-table Markdown from the image.
  2. `translate_text` translates content into Marathi (`mr`), guaranteeing table rows and cells are never fractured.
  3. **Verification:** `verify_table_parity` checks that row count (6) and column counts match source table 1:1.
- **Generated Artifacts:**
  - `artifacts/s1_extracted.md`
  - `artifacts/s2_translated.txt`

### Task 4: Clip Language Identification & Translation
- **Input:** Directory of 5 audio clips in various Indic languages.
- **Workflow:**
  1. Resolver selects clips; `transcribe_audio` detects language confidence for each.
  2. Telugu clips (`clip2.wav` and `clip4.wav`) are isolated.
  3. `translate_text` translates Telugu content to English.
- **Generated Artifacts:**
  - `artifacts/s1_transcript.txt`
  - `artifacts/s2_translated.txt`

### Task 5: Bengali Paragraph Aloud (Female Voice)
- **Input:** Bengali literature passage text file.
- **Workflow:**
  1. Resolver identifies text input; `synthesize_speech` selects `bn_female_1`.
  2. Synthesizes WAV audio and stamps AI metadata sidecar.
  3. **Verification:** `verify_audio_back_transcription` confirms character-level similarity.
- **Generated Artifacts:**
  - `artifacts/speech_bn_bn_female_1.wav`
  - `artifacts/speech_bn_bn_female_1.ai.txt`

### Task 6: Unsupported Foreign Language Request (French)
- **Input:** Task requesting translation into French.
- **Workflow:**
  1. Hard constraint audit (SPEC §3 & Golden Rule 1) flags French as an unsupported language (Bodhan Indic models support Indian languages and English translation/OCR only).
  2. Loop immediately refuses execution at pre-flight stage: `status="impossible"`, `cost=0.00`.
  3. Zero artifacts generated (Constraint C8).
- **Result:** Honest, clear explanation recorded in `result.md` and `report.json`.

---

## Reproducing the Demos

To regenerate input media and re-run all six demos:
```bash
python scripts/prepare_demo_media.py
python scripts/run_demos.py
```
