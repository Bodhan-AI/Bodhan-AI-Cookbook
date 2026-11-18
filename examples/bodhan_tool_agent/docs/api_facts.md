# Bodhan API: observed behaviour

Measured with `scripts/probe_api.py` on 2026-11-18 against `https://api.bodhan.ai`, and cross-checked with the [API docs](https://console.bodhan.ai/api-docs/). The tools are built on these facts.

| Model | Route that works | What we observed | How the tools handle it |
|---|---|---|---|
| indic-transcribe | `POST /v1/audio/transcriptions` (multipart: `file`, `model`, optional `language`) | Returns only `{"text", "usage": null}`: **no detected language, no timestamps**. `response_format` with `timestamp_granularities[]` gives 422. Auto-detect works when `language` is omitted. The docs give a hard 30 s limit per request, and M4A/AAC/WebM are refused with 415. | Every input is converted to 16 kHz mono WAV with ffmpeg (video soundtracks too). Cuts land at silences, so each chunk is at most 30 s. Timestamps come from our own cut points. Language comes from the transcript's script. |
| indic-translate | `POST /v1/chat/completions` (`model`, one `user` message, `target_language`, optional `script`) | `POST /translate` returned **404** for our keys on every path variant. Chat completions works, honours `target_language`, and infers the source language. Unsupported targets (e.g. `fr`) give 400 listing the supported codes. `finish_reason: "length"` means truncated. | The client uses the chat transport and returns the documented `/translate` response shape. Unsupported languages are rejected before any call. |
| indic-translate (tables) | | Translating a whole Markdown table **dropped the `\|---\|` separator row** and converted `45` → `४५` (Devanagari digits). | Tables are translated cell by cell and rebuilt in code. The number-preservation check treats Indic digits as their ASCII equivalents. |
| indic-translate (hidden instructions) | | "Ignore all previous instructions…" was translated literally, not obeyed. | The text is still treated as untrusted data on the agent side. |
| indic-speak | `POST /v1/audio/speech` (`model`, `input`, `voice`, `instructions` = JSON string with `lang`, optional `style`) | Returns **WAV (PCM16, 24 kHz, mono) labelled `Content-Type: audio/mpeg`**. An unknown voice gave **500** (the docs say 422), so the client would retry it pointlessly. No hard character limit is documented; the guidance is "a sentence or two, about 30 s". | Voices are validated locally before any call. Text is packed into pieces of about 250 characters at sentence boundaries. Audio is identified by its bytes, not its content type. |
| indic-ocr | `POST /v1/chat/completions` (one `image_url` data URL, `table_format`) | `choices[0].message.content` is clean Markdown **with tables intact**. There is also `blocks` (label, type, bbox, conf, text) and token `usage`. | One page per call. PDFs are rendered to PNG first. Output is Markdown with page markers. |

Errors come in two envelopes: `{"error": {"message", "code", "request_id"}}` (translate) and the LiteLLM style `{"error": {"message", "type", "param", "code": "<status>"}}` (other models). The client reads both.

The basic probe cost ₹0.24 in total.
