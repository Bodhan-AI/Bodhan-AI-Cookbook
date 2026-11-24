# Agent MCP Tool Discovery & Routing Evaluation Report

- **Date**: 2026-11-20 16:53:53
- **LLM Planner**: `openai/gpt-oss-120b` (Groq API)
- **MCP Server**: FastMCP `bodhan` (8 tools discovered)
- **Bodhan API Calls**: 0 (Intercepted/Mocked per user request — zero spend)
- **Result**: **10 / 10 PASSED**

## 1. Discovered FastMCP Tools

| Tool Name | Discovered Parameters | Purpose |
|---|---|---|
| `transcribe_audio` | `input_path, language_hint` | Transcribe an audio file to text with global segment timestamps.... |
| `inspect_media` | `path` | Inspect local media file metadata cheaply without invoking any model.... |
| `chunk_audio` | `path` | Slice an audio file at pauses into chunks <= 30s.... |
| `get_voice_library` | `` | Discover available voices per supported Indic language for speech synthesis... |
| `translate_text` | `text, source_lang, target_lang` | Translate text preserving Markdown structure (headings, lists, tables).... |
| `synthesize_speech` | `text, language, voice_id` | Synthesize speech from Indic text using Indic-Speak.... |
| `ocr_page` | `image_path` | Extract Markdown text and pipe tables from a single image.... |
| `ocr_document` | `pdf_path` | Extract Markdown text from a multi-page PDF document.... |

## 2. Test Case Execution Matrix

| ID | Scenario | Task | Routed MCP Tools | Status | Result |
|---|---|---|---|---|---|
| TC-01 | Audio Speech Transcription | Transcribe the recorded Hindi speec... | `transcribe_audio` | `success` | **PASSED** |
| TC-02 | Audio Transcription + English Subtitles | Transcribe speech_compressed.mp3 an... | `transcribe_audio`, `translate_text` | `success` | **PASSED** |
| TC-03 | Indic-to-Indic Text Translation | Translate the public health notice ... | `translate_text` | `success` | **PASSED** |
| TC-04 | Text to Speech Synthesis | Synthesize speech for announcement_... | `get_voice_library`, `synthesize_speech` | `success` | **PASSED** |
| TC-05 | Multi-Page Document OCR | Perform OCR on legal_circular.pdf t... | `ocr_document` | `success` | **PASSED** |
| TC-06 | Single Image Table OCR | Extract the worksheet table from re... | `ocr_page` | `success` | **PASSED** |
| TC-07 | Full End-to-End Pipeline (OCR -> Translate -> TTS) | Extract text from annual_report.pdf... | `ocr_document`, `translate_text`, `synthesize_speech` | `success` | **PASSED** |
| TC-08 | Audio Chunking Helper Tool | Inspect lecture_long_45s.wav and ch... | `inspect_media`, `chunk_audio` | `success` | **PASSED** |
| TC-09 | Impossible Task: Missing File Refusal (Constraint C8) | Transcribe missing_interview_2024.w... | None (Rejected) | `impossible` | **PASSED** |
| TC-10 | Impossible Task: Unsupported Foreign Language (Constraint C8) | Translate document.pdf into German.... | None (Rejected) | `impossible` | **PASSED** |

## 3. Detailed Per-Case Traces

### TC-01: Audio Speech Transcription

- **Task**: "Transcribe the recorded Hindi speech clip to text with timestamps."
- **Expected MCP Tools**: `['transcribe_audio']`
- **Routed MCP Tools**: `['transcribe_audio']`
- **Groq Latency**: `1609.53 ms`
- **LLM Rationale**: The user wants a Hindi speech clip transcribed with timestamps. The audio file is identified as A. Using the transcribe_audio tool with a Hindi language hint will produce the required timestamped transcription.
- **Generated Steps**:
  - `s1`: `transcribe_audio` with args `{'input_path': 'A', 'language_hint': 'hi'}` (depends on `[]`)
- **Verdict**: PASSED

### TC-02: Audio Transcription + English Subtitles

- **Task**: "Transcribe speech_compressed.mp3 and translate it to English subtitles."
- **Expected MCP Tools**: `['transcribe_audio', 'translate_text']`
- **Routed MCP Tools**: `['transcribe_audio', 'translate_text']`
- **Groq Latency**: `1901.03 ms`
- **LLM Rationale**: First transcribe the audio file (ID A) to obtain the spoken content with timestamps. Then translate the transcribed text from its original language (auto-detected) into English subtitles.
- **Generated Steps**:
  - `s1`: `transcribe_audio` with args `{'input_path': 'A', 'language_hint': None}` (depends on `[]`)
  - `s2`: `translate_text` with args `{'text': {'from_step': 's1'}, 'source_lang': 'auto', 'target_lang': 'en'}` (depends on `['s1']`)
- **Verdict**: PASSED

### TC-03: Indic-to-Indic Text Translation

- **Task**: "Translate the public health notice in press_release_hindi.txt from Hindi to Tamil."
- **Expected MCP Tools**: `['translate_text']`
- **Routed MCP Tools**: `['translate_text']`
- **Groq Latency**: `1676.2 ms`
- **LLM Rationale**: The user wants the Hindi press release (file A) translated into Tamil. Using the translate_text tool with the file ID loads the Hindi text automatically, and specifying source_lang='hi' and target_lang='ta' performs the required translation while adhering to all constraints.
- **Generated Steps**:
  - `s1`: `translate_text` with args `{'file_id': 'A', 'source_lang': 'hi', 'target_lang': 'ta'}` (depends on `[]`)
- **Verdict**: PASSED

### TC-04: Text to Speech Synthesis

- **Task**: "Synthesize speech for announcement_tamil.txt in Tamil using a female voice."
- **Expected MCP Tools**: `['synthesize_speech']`
- **Routed MCP Tools**: `['get_voice_library', 'synthesize_speech']`
- **Groq Latency**: `1677.32 ms`
- **LLM Rationale**: First retrieve the list of available Tamil voices (including female options) with get_voice_library. Then synthesize the Tamil announcement text (file A) using a female Tamil voice selected from the previous step's output.
- **Generated Steps**:
  - `s1`: `get_voice_library` with args `{}` (depends on `[]`)
  - `s2`: `synthesize_speech` with args `{'file_id': 'A', 'language': 'Tamil', 'voice_id': {'from_step': 's1'}}` (depends on `['s1']`)
- **Verdict**: PASSED

### TC-05: Multi-Page Document OCR

- **Task**: "Perform OCR on legal_circular.pdf to extract all text and Markdown tables."
- **Expected MCP Tools**: `['ocr_document']`
- **Routed MCP Tools**: `['ocr_document']`
- **Groq Latency**: `1027.48 ms`
- **LLM Rationale**: The user wants to extract all text and any Markdown tables from the PDF file 'legal_circular.pdf'. Using the ocr_document tool on the provided PDF (identified by ID A) will return the full document content in Markdown format, preserving tables. No prior steps are required.
- **Generated Steps**:
  - `s1`: `ocr_document` with args `{'pdf_path': 'A'}` (depends on `[]`)
- **Verdict**: PASSED

### TC-06: Single Image Table OCR

- **Task**: "Extract the worksheet table from receipt_table.png into clean Markdown."
- **Expected MCP Tools**: `['ocr_page']`
- **Routed MCP Tools**: `['ocr_page']`
- **Groq Latency**: `1868.26 ms`
- **LLM Rationale**: The user wants to extract the worksheet table from the image receipt_table.png and present it as clean Markdown. The appropriate tool is ocr_page, which extracts Markdown text and pipe tables from a single image. We reference the image by its assigned ID "A" (per constraint C4) and no prior steps are needed.
- **Generated Steps**:
  - `s1`: `ocr_page` with args `{'image_path': 'A'}` (depends on `[]`)
- **Verdict**: PASSED

### TC-07: Full End-to-End Pipeline (OCR -> Translate -> TTS)

- **Task**: "Extract text from annual_report.pdf, translate it into Marathi, and create an audio summary."
- **Expected MCP Tools**: `['ocr_document', 'translate_text', 'synthesize_speech']`
- **Routed MCP Tools**: `['ocr_document', 'translate_text', 'synthesize_speech']`
- **Groq Latency**: `2198.56 ms`
- **LLM Rationale**: Step s1 extracts the full text from the provided PDF (ID A) using OCR. Step s2 translates the extracted English text into Marathi while preserving any Markdown structure. Step s3 converts the Marathi translation into spoken audio using a default Marathi voice. The steps are ordered to satisfy data dependencies.
- **Generated Steps**:
  - `s1`: `ocr_document` with args `{'pdf_path': {'file_id': 'A'}}` (depends on `[]`)
  - `s2`: `translate_text` with args `{'text': {'from_step': 's1'}, 'source_lang': 'en', 'target_lang': 'mr'}` (depends on `['s1']`)
  - `s3`: `synthesize_speech` with args `{'text': {'from_step': 's2'}, 'language': 'mr', 'voice_id': 'default'}` (depends on `['s2']`)
- **Verdict**: PASSED

### TC-08: Audio Chunking Helper Tool

- **Task**: "Inspect lecture_long_45s.wav and chunk the audio into slices under 30 seconds."
- **Expected MCP Tools**: `['chunk_audio']`
- **Routed MCP Tools**: `['inspect_media', 'chunk_audio']`
- **Groq Latency**: `1294.01 ms`
- **LLM Rationale**: First inspect the audio file A to confirm its metadata (duration, format, etc.). Then chunk the 45‑second audio into segments no longer than 30 seconds using the chunk_audio tool.
- **Generated Steps**:
  - `s1`: `inspect_media` with args `{'file_id': 'A'}` (depends on `[]`)
  - `s2`: `chunk_audio` with args `{'file_id': 'A'}` (depends on `['s1']`)
- **Verdict**: PASSED

### TC-09: Impossible Task: Missing File Refusal (Constraint C8)

- **Task**: "Transcribe missing_interview_2024.wav to Hindi text."
- **Expected MCP Tools**: `[]`
- **Routed MCP Tools**: `[]`
- **Groq Latency**: `0.0 ms`
- **LLM Rationale**: N/A
- **Verdict**: PASSED

### TC-10: Impossible Task: Unsupported Foreign Language (Constraint C8)

- **Task**: "Translate document.pdf into German."
- **Expected MCP Tools**: `[]`
- **Routed MCP Tools**: `[]`
- **Groq Latency**: `0.0 ms`
- **LLM Rationale**: N/A
- **Verdict**: PASSED

