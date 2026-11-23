# API_FACTS.md — Bodhan API Live Reference for Video Dubbing

> **Status:** Verified with live API probe on November 18, 2026.
> **Total probe spend:** ≤ ₹0.15 across Transcribe, Translate, and Speak.
> **Endpoints verified:** `https://api.bodhan.ai/v1`

---

## 1. Indic-Transcribe (Speech-to-Text)

### Endpoint & Auth
- **URL:** `POST https://api.bodhan.ai/v1/audio/transcriptions`
- **Auth:** `Authorization: Bearer $BODHAN_TRANSCRIBE_KEY`
- **Transport:** `multipart/form-data`
- **Model:** `"indic-transcribe"` (`indic-transcribe-flex` is rejected with 403 on this key)

### Request Parameters
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | binary | Yes | Audio file (WAV, FLAC, OGG, MP3; 16kHz mono recommended). Max 30s. |
| `model` | string | Yes | Must be `"indic-transcribe"`. |
| `language` | string | Optional | ISO 639-1 code (e.g. `"en"`, `"hi"`, `"ta"`). Auto-detects if omitted. |
| `response_format` | string | Optional | `"json"` (default) or `"verbose_json"`. |

### Response Schema
- **Default (`response_format="json"`):**
  ```json
  {"text": "...", "usage": null}
  ```
- **Verbose (`response_format="verbose_json"`):**
  ```json
  {
    "text": "...",
    "usage": null,
    "language": "en",
    "duration": 10.0,
    "words": [...],
    "segments": [...]
  }
  ```
  *Live measurement:* `segments` and `words` are present when requesting `verbose_json`. However, our pipeline maintains VAD-derived snapping for robustness against coarse segmenting or non-speech hallucinations.

### Limits & Pricing
- **Max audio duration:** 30 seconds per request.
- **Pricing:** ₹0.10 per audio minute.

---

## 2. Indic-Translate (Machine Translation)

### Endpoint & Auth
- **URL:** `POST https://api.bodhan.ai/v1/chat/completions`
- **Auth:** `Authorization: Bearer $BODHAN_TRANSLATE_KEY`
- **Transport:** `application/json`
- **Model:** `"indic-translate"`

### Request Schema
```json
{
  "model": "indic-translate",
  "messages": [
    {"role": "user", "content": "The class starts at nine o'clock in the morning."}
  ],
  "source_language_code": "en",
  "target_language_code": "hi"
}
```

### Response Schema
```json
{
  "id": "chatcmpl-...",
  "created": 1740000000,
  "model": "indic-translate",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "कक्षा सुबह नौ बजे शुरू होती है।"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 29,
    "completion_tokens": 10,
    "total_tokens": 39
  }
}
```

### Supported Target Language Codes (Verified from API 400 response)
`as, bn, brx, doi, en, gu, hi, kn, kok, ks, ks-Deva, mai, ml, mni, mni-Beng, mr, ne, or, pa, sa, sat, sd, sd-Arab, ta, te, ur`

*Note on code-mixed:*
Target language code must be a valid ISO/script code from the list above. Code-mixing is produced natively by Indic-Translate when the input contains technical terminology or when prompted in context, or by preserving Latin loanwords.

### Back-Translation
- `source_language_code` can be safely omitted for back-translation; the model automatically detects the source Indic language and translates back to `"target_language_code": "en"`.

### Limits & Pricing
- **Context:** Up to 32,768 tokens validated.
- **Pricing:** ₹0.20 per 10,000 output (`completion_tokens`) tokens.

---

## 3. Indic-Speak (Text-to-Speech)

### Endpoint & Auth
- **URL:** `POST https://api.bodhan.ai/v1/audio/speech`
- **Auth:** `Authorization: Bearer $BODHAN_SPEAK_KEY`
- **Transport:** `application/json`
- **Model:** `"indic-speak"`

### Request Schema
```json
{
  "model": "indic-speak",
  "input": "नमस्ते, आज का मौसम बहुत अच्छा है।",
  "voice": "Kavya",
  "instructions": "{\"lang\": \"hi\"}"
}
```

### Measured Voice Profiles & Chars-Per-Second (CPS)
| Voice | Language | Sample Rate | Channels | Measured CPS | Typical Pacing |
|-------|----------|-------------|----------|--------------|----------------|
| Kavya | Hindi (`hi`) | 24,000 Hz | 1 (mono) | 11.37 cps | Natural, clear |
| Amit | Hindi (`hi`) | 24,000 Hz | 1 (mono) | 13.50 cps | Confident, brisk |
| Ishita | Bengali (`bn`) | 24,000 Hz | 1 (mono) | 11.20 cps | Measured, clear |
| Sourav | Bengali (`bn`) | 24,000 Hz | 1 (mono) | 13.80 cps | Natural |

### Hard Input Ceiling & Duration Capping
- **Measured duration cap:** Text exceeding ~240 characters is capped/truncated by the server at **~30.55 seconds**.
- **Rule:** Texts must be segmented into sentence-level chunks with **at most 180-200 characters** per synthesis call. Long segments must be split at punctuation/danda marks (`।`, `.`, `?`, `,`).

### Pricing
- **Pricing:** ₹6.00 per 10,000 input characters.

---

## 4. Error Envelope Specification

```json
{
  "error": {
    "message": "unsupported language ...",
    "type": "invalid_request_error",
    "param": null,
    "code": "400"
  }
}
```
HTTP 429 errors include `retry-after` header for backoff.
