# Bodhan API as implemented (read from https://console.bodhan.ai/api-docs/ on 2026-11-22)

Base URL `https://api.bodhan.ai`. Auth: `Authorization: Bearer <key>` on every request.
The docs do not state key scope per model; `scripts/verify_bodhan.py` tests it.

| Service | Request | Notes enforced in code |
|---|---|---|
| indic-transcribe | `POST /v1/audio/transcriptions`, multipart: `file`, `model=indic-transcribe`, `language` | <=30 s per request; WAV/FLAC/OGG/MP3 only (WebM, M4A, AAC -> 415). Backend converts everything to mono 16 kHz PCM16 WAV and splits at <=29 s (configurable, hard-capped at 30), cutting at the quietest nearby moment. Response: `{"text": ...}` |
| indic-translate | `POST /translate` JSON `{text, target_language, script?}` | No source field. 26 target codes. `bhb`/`bho` refused locally. `script`: native (default, omitted) / roman / codemix (not for en, ks-Deva, sd-Arab). `truncated` is checked; truncated chunks are halved and retried (depth 2), then flagged. Response `{"translation", "truncated", "usage"}` |
| indic-transliterate | `POST /transliterate` JSON `{text, language, script}` | One language, script `roman` or `native`; English refused. Response `{"transliteration", "truncated"}` |
| indic-speak | `POST /v1/audio/speech` JSON `{model, input, voice, instructions}` | `instructions` is a JSON *string* (`'{"lang": "te"}'`, optional `style`). 45 voices; any voice reads any language. Binary `audio/wav` (PCM16, 24 kHz, mono) is required; JSON or invalid WAV is rejected. Long text is split at sentence boundaries (default 200 chars) and WAVs joined (150 ms gap); mismatched segment formats raise |
| indic-ocr | `POST /v1/chat/completions` JSON `{model, messages:[{role:user, content:[{type:image_url, image_url:{url:data:...;base64,...}}]}], table_format?, max_tokens?}` | PNG/JPEG only, one image per request, no language field. Text at `choices[0].message.content`, layout `blocks` at top level. PDFs are rendered to PNG per page (PyMuPDF, 200 dpi, max side 4000 px, max 50 pages). 502 (large table) is retried only by raising `max_tokens` (default -> 4096 -> 8192); decode errors are not retried |

## Assumptions (not stated in the docs; all configurable)
* Chunk sizes for translate/transliterate (2000 chars) and speak (200 chars) are conservative guesses.
* A 502 whose `detail` mentions decoding/corruption is treated as a bad image; any other OCR 502 is treated
  as a block exceeding `max_tokens`. The exact 502 detail strings are not documented.
* Transcribe docs list 25 codes in the table while the prose says "25 Indian languages and English"; the
  table is used as authoritative.
* Retries: 429/500/502/503/504 and network/timeouts up to 2 times with exponential backoff and `Retry-After`
  (<=10 s). 400/401/413/415/422 are never retried.
