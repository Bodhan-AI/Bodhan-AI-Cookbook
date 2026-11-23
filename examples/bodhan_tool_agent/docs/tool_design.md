# Tool Design & API Compliance

All tool implementations strictly enforce the Bodhan AI API specifications defined in `scripts/bodhan_api_rules.json`.

## Core Model Operations

| Tool Name | Bodhan Model | Endpoint | Responsibilities |
|---|---|---|---|
| `transcribe_audio` | `indic-transcribe` | `POST /v1/audio/transcriptions` | Validates audio formats, checks duration, chunks audio > 28s, preserves timestamps, aggregates transcript. |
| `translate_text` | `indic-translate` | `POST /translate` | Auto-detects source language, preserves Markdown formatting, chunks long documents, detects unsupported language directions. |
| `synthesize_speech` | `indic-speak` | `POST /v1/audio/speech` | Validates voice/language compatibility, splits text on sentence boundaries, joins 24 kHz WAV chunks, labels speech as AI-generated. |
| `extract_text_from_document` | `indic-ocr` | `POST /v1/chat/completions` | Rasterizes multi-page PDFs, encodes pages to base64 data URLs, preserves reading order and tables, aggregates Markdown output. |

## Official Pricing Schedule (INR)

- **`indic-transcribe`**: ₹0.01 per second of audio
- **`indic-translate`**: ₹0.15 per 1,000 output tokens (input tokens free)
- **`indic-speak`**: ₹0.03 per second of audio
- **`indic-ocr`**: ₹0.30 per document page

## Language and Voice Allowlist

- **Transcription**: 25 Indic languages + `en`
- **Translation**: 22 Indic languages + `en` (scripts: `native`, `roman`, `codemix`)
- **Speech**: 22 Indic languages + `en`, 45 voices, 14 delivery styles
- **OCR**: Script-agnostic across all Indian languages and English
