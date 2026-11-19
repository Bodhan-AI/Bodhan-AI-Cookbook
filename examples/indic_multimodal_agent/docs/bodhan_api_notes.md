# Bodhan AI API Documentation & Verified Response Shapes

*Verified with live endpoint calls in Step 0 (< ₹0.25 total).*

---

## 1. Indic-Transcribe (Audio Speech-to-Text)

* **Endpoint**: `POST https://api.bodhan.ai/v1/audio/transcriptions`
* **Auth**: `Authorization: Bearer <BODHAN_TRANSCRIBE_KEY>`
* **Content-Type**: `multipart/form-data`
* **Pricing**: ₹0.10 per input audio minute.

### Request Parameters
* `file`: Audio file binary (e.g. `probe_speech.wav`, `sample.mp3`).
* `model`: `"indic-transcribe"`
* `language`: Optional ISO code (e.g. `"hi"`, `"ta"`, `"te"`). **Omission test passed**: When `language` is omitted, the API automatically detects the spoken language and transcribes accurately.
* `vad`: `True` / `False` (Voice activity detection)
* `response_format`: `"text"` or `"verbose_json"` (Note: `verbose_json` returns `language`, `duration`, `words`, and `segments` as `null`. Segment timestamps must be handled via our chunking engine).

### Live Response Shape
```json
{
  "text": "नमस्ते यह एक परीक्षण है",
  "usage": null
}
```

---

## 2. Indic-Translate (Text Translation)

* **Endpoint**: `POST https://api.bodhan.ai/v1/chat/completions`
* **Auth**: `Authorization: Bearer <BODHAN_TRANSLATE_KEY>`
* **Content-Type**: `application/json`
* **Pricing**: ₹0.20 per 10,000 output tokens.

### Request Body
```json
{
  "model": "indic-translate",
  "messages": [
    {"role": "user", "content": "Good morning, how are you?"}
  ],
  "target_language_code": "hi",
  "source_language_code": "en",
  "target_script": "native"
}
```
*(Note: `source_language_code` is accepted; when omitted, source language is auto-detected).*

### Live Response Shape
```json
{
  "id": "20261116_bdd6ed6a-1f0b-4f66-b9f0-68f8ec8971f5",
  "created": 1794831000,
  "model": "indic-translate",
  "object": "chat.completion",
  "system_fingerprint": "vllm-0.27.1-9be2c277",
  "choices": [
    {
      "finish_reason": "stop",
      "index": 0,
      "message": {
        "content": "सुप्रभात, आप कैसे हैं?",
        "role": "assistant"
      }
    }
  ],
  "usage": {
    "prompt_tokens": 24,
    "completion_tokens": 8,
    "total_tokens": 32
  }
}
```

### Rate Limit Headers Observed
* `X-Ratelimit-Api_key-Limit-Max_parallel_requests`: `10`
* `X-Ratelimit-Api_key-Limit-Requests`: `120`

---

## 3. Indic-Speak (Speech Synthesis / TTS)

* **Endpoint**: `POST https://api.bodhan.ai/v1/audio/speech`
* **Auth**: `Authorization: Bearer <BODHAN_SPEAK_KEY>`
* **Content-Type**: `application/json`
* **Pricing**: ₹6.00 per 10,000 input characters.

### Request Body
```json
{
  "model": "indic-speak",
  "input": "नमस्ते, यह एक परीक्षण है।",
  "voice": "Amit",
  "instructions": "{\"lang\": \"hi\"}"
}
```

### Live Response Details
* **Status**: `200 OK`
* **Content-Type**: `audio/mpeg` (Audio stream returned is MPEG/MP3 format).
* **Remote Voices Endpoint**: `GET /v1/audio/voices` returns `404 Not Found`. Voices are managed locally via language and gender mapping.

---

## 4. Indic-OCR (Vision / Document OCR)

* **Endpoint**: `POST https://api.bodhan.ai/v1/chat/completions`
* **Auth**: `Authorization: Bearer <BODHAN_OCR_KEY>`
* **Content-Type**: `application/json`
* **Pricing**: ₹0.20 per image.

### Request Body
```json
{
  "model": "indic-ocr",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,<BASE64_STRING>"
          }
        }
      ]
    }
  ]
}
```

### Live Response Shape
```json
{
  "id": "indic-ocr-e88e22df0b454b4ea6218c2f6dbcd550",
  "created": 1794831027,
  "model": "indic-ocr",
  "object": "chat.completion",
  "choices": [
    {
      "finish_reason": "stop",
      "index": 0,
      "message": {
        "content": "CHAPTER 1: INTRODUCTION",
        "role": "assistant"
      }
    }
  ],
  "usage": {
    "completion_tokens": 0,
    "prompt_tokens": 0,
    "total_tokens": 0
  },
  "blocks": [
    {
      "order": 1,
      "label": "Chapter-title",
      "type": "Title",
      "bbox_xyxy": [27.9, 55.5, 347.2, 76.3],
      "conf": 0.693,
      "text": "CHAPTER 1: INTRODUCTION"
    }
  ]
}
```
* **Full Text**: Accessible directly at `resp.json()["choices"][0]["message"]["content"]`.
* **Layout Blocks**: Detailed structural blocks (bounding boxes, layout tags, order) accessible at `resp.json()["blocks"]`.
