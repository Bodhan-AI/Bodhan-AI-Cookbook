# Bodhan AI API Integration Guide

This document describes the Bodhan AI hosted API endpoints, authentication mechanisms, and request/response contracts used by `examples/video_dubbing`, aligned with `scripts/bodhan_api_rules.json`.

## Base Configuration
- **Base URL**: `https://api.bodhan.ai` (configured via `BODHAN_BASE_URL`)
- **Authentication**: HTTP Bearer Token (`BODHAN_API_KEY`)
  ```http
  Authorization: Bearer <BODHAN_API_KEY>
  ```

---

## 1. Bodhan Indic-Transcribe (Speech Recognition)

- **Endpoint**: `POST https://api.bodhan.ai/v1/audio/transcriptions`
- **Content-Type**: `multipart/form-data`
- **Model**: `"indic-transcribe"`
- **Parameters**:
  - `file`: Audio file binary (WAV PCM16, chunked to <= 30 seconds per request; pipeline uses 20s windows).
  - `model`: `"indic-transcribe"`
  - `language`: ISO language code (`"en"` for source, `"hi"`, `"ta"`, `"bn"` for back-transcription).
  - `response_format`: `"verbose_json"` or `"json"`.

### Response Schema:
```json
{
  "text": "Welcome to this artificial intelligence demonstration video."
}
```

---

## 2. Bodhan Indic-Translate (Translation)

- **Endpoint**: `POST https://api.bodhan.ai/translate`
- **Content-Type**: `application/json`
- **Model**: `"indic-translate"` (implicit for `/translate`)
- **Parameters**:
  - `text`: Source text string.
  - `target_language`: Target ISO language code (e.g., `"hi"`, `"ta"`, `"bn"`).
  - `script`: `"native"` (default) or `"codemix"`.
  - `max_output_tokens`: Optional output token cap (default `512`).

### Request Example:
```json
{
  "text": "Hello, welcome to this video about machine learning.",
  "target_language": "hi",
  "script": "native",
  "max_output_tokens": 512
}
```

### Response Example:
```json
{
  "translation": "नमस्ते, मशीन लर्निंग के बारे में इस वीडियो में आपका स्वागत है।",
  "target_language": "hi",
  "script": "native",
  "truncated": false
}
```

---

## 3. Bodhan Indic-Speak (Speech Synthesis)

- **Endpoint**: `POST https://api.bodhan.ai/v1/audio/speech`
- **Content-Type**: `application/json`
- **Model**: `"indic-speak"`
- **Parameters**:
  - `model`: `"indic-speak"`
  - `input`: Spoken text in native Indic script.
  - `voice`: Official language-matched speaker name (e.g., `"Kavya"` for `hi`, `"Anitha"` for `ta`, `"Ishita"` for `bn`).
  - `instructions`: JSON string containing language code and optional style (e.g. `'{"lang": "hi", "style": "news"}'`).

### Request Example:
```json
{
  "model": "indic-speak",
  "input": "नमस्ते, मशीन लर्निंग के बारे में इस वीडियो में आपका स्वागत है।",
  "voice": "Kavya",
  "instructions": "{\"lang\": \"hi\", \"style\": \"news\"}"
}
```

### Response:
- Binary audio stream: `audio/wav` (PCM16, 24 kHz, mono).
