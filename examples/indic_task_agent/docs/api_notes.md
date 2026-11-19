# Bodhan API notes

Behaviour of the hosted API at `https://api.bodhan.ai` as observed on 19 November 2026, compared with the [API reference](https://console.bodhan.ai/api-docs/). The code in `src/bodhan_mcp/client.py` follows the observed behaviour.

## Keys

One key per model: `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_OCR_KEY`. Each request sends `Authorization: Bearer <key>`.

## indic-transcribe

| | |
|---|---|
| Request | `POST /v1/audio/transcriptions`, multipart: `file`, `model=indic-transcribe`, optional `language` |
| Limit | About 30 s of audio per request |
| Response | `{"text": "...", "usage": null}`. No timestamps, segments or detected language. |
| Price | ₹0.10 per audio minute, billed per second |

The documented error for unmeasurable containers is 415, so the tool always sends 16 kHz mono WAV that it decodes itself. Under load the model returned `429 "Maximum rate limit reached for the model"` to every request for a few minutes. Latency rose from about 0.5 s to about 6 s per 6 s clip.

## indic-translate

| | |
|---|---|
| Documented request | `POST /translate` with `text`, `target_language` |
| Observed | `POST /translate` returns **404**. `POST /v1/translate` returns 404 too. |
| Working request | `POST /v1/chat/completions` with `model=indic-translate`, exactly one `user` message, `target_language`, optional `script` (`native`, `roman`, `codemix`), optional `max_output_tokens` |
| Response | Chat completion. `finish_reason: "length"` means truncated. `usage.completion_tokens` is billed. |
| Price | ₹0.20 per 10,000 output tokens |

Other observed behaviour:

- Omitting `target_language` returns a **Hindi** translation without an error. The client always sends the field.
- `target_language_code` is accepted as an alias.
- `fr` returns 400 `unsupported language 'fr'; supported: ...`.
- A `system` message returns 400. `temperature` returns 400.
- Several paragraphs sent together come back merged into one paragraph. An English paragraph inside a Hindi text was **dropped** when translating to English. The translation tool therefore sends one paragraph per request and keeps same-language paragraphs as they are.

## indic-speak

| | |
|---|---|
| Request | `POST /v1/audio/speech` with `model=indic-speak`, `input`, `voice`, and `instructions` as a JSON **string** such as `"{\"lang\": \"bn\"}"`, with an optional `style` |
| Response | WAV bytes, PCM16, 24 kHz, mono. The `Content-Type` header says `audio/mpeg`, so the client checks the bytes. |
| Limit | About one or two sentences per request. The tool sends at most 220 characters. |
| Price | ₹6 per 10,000 input characters |

An unknown voice name (`kavya` instead of `Kavya`) returns **HTTP 500 "Internal server error"**, not the documented 422. The tool validates voice names before calling.

## indic-ocr

| | |
|---|---|
| Request | `POST /v1/chat/completions` with `model=indic-ocr`, one `user` message whose content is one `image_url` part (base64 PNG or JPEG data URI), optional `table_format` (`html` default, or `markdown`), optional `max_tokens` (per block, default 2048) |
| Response | Chat completion with page Markdown in `choices[0].message.content`, plus top-level `blocks[]` (`order`, `label`, `type`, `bbox_xyxy`, `conf`, `text`) |
| Limit | One image per request. No PDF input. |
| Price | ₹0.20 per image |
| Headers seen | `x-ratelimit-api_key-limit-requests: 120`, `x-ratelimit-api_key-limit-max_parallel_requests: 10` |

## Languages

`src/bodhan_mcp/languages.py` holds the language codes, voices and styles. It matches `scripts/bodhan_api_rules.json` in this repository and the live error message for unsupported translation targets.
