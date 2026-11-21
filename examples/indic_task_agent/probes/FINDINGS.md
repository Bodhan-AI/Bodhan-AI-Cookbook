# Bodhan API probe findings (2026-11-20)

Recorded with the scripts in this folder against `https://api.bodhan.ai`. Full request and response recordings are written to `probes/out/` (git-ignored). Total spend for all probes: about Rs 1.3.

The API sits behind a LiteLLM proxy in front of vLLM (`system_fingerprint: vllm-0.27.1`). **The Cookbook's `/translate` and `/transliterate` routes return 404**; everything goes through the OpenAI-style `/v1` routes. Each key only sees its own model (`GET /v1/models` lists one id).

## Common

| Item | Observed |
|---|---|
| Error body | `{"error": {"message", "type", "param", "code"}}`; `code` is a string such as `"400"`. No `request_id`, despite the Cookbook's docs. |
| Auth failure | 401, `type: token_not_found_in_db`. The message echoes a masked key and its hash, so upstream error text must be redacted before tracing. |
| Rate-limit headers | None returned (`x-ratelimit-*` and `Retry-After` absent). Backoff cannot rely on them. |
| Request ids | Chat-style responses carry `id`; transcription and speech carry none. |

## indic-transcribe: `POST /v1/audio/transcriptions` (multipart)

| Probe | Result |
|---|---|
| `language=hi`, 6.5 s clip | 200, `{"text", "usage": null}` in 6 s |
| No `language` | 200, same text, but **24 s latency** (4x slower). No detected-language field. |
| `response_format=verbose_json` | 200 but `language`, `duration`, `words`, `segments` are all `null`. **No timestamps.** |
| `response_format=srt` | Ignored; returns the JSON text. |
| `timestamp_granularities[]` | 422, unexpected field. |
| `language=fr` | 200 with the Hindi transcript. **Unsupported codes are silently ignored.** |
| 39 s clip (sample looped 6x) | 200, but only **5 of the 6 repeats came back**. Audio past ~30 s is silently dropped. |

## indic-translate: `POST /v1/chat/completions`, `model: "indic-translate"`

Documented body (re-probed 2026-11-20 with the `tr_*` cases): `messages: [{"role": "user", "content": text}]` plus top-level **`target_language_code`** and **`target_script`** (`native` default, `roman`, `codemix`). Response is a standard chat completion with `usage.completion_tokens`, so cost is exact (Rs 0.20 per 10,000 output tokens).

| Probe | Result |
|---|---|
| en to kn / ta, hi to en | 200 in under 1.2 s; source language auto-detected |
| `target_script: "roman"` / `"codemix"` | 200; romanised Hindi / Hindi with English words kept |
| `target_script` omitted | 200, native script (the default) |
| `source_language_code: "en"` | Accepted; no visible effect |
| Markdown with a table, to Marathi | 200; **headings, pipes and separator row preserved**, cells translated, digits converted to Devanagari numerals |
| `max_output_tokens: 8` | 200, output cut, `finish_reason: "length"` |
| No target language | 200 with **garbage** (half Bengali, half Hindi). Always send it. |
| `target_language_code: "fr"` | 400, message lists every supported code |
| `target_script: "cyrillic"` | 400, `supported: native, roman, codemix` |
| Empty text | 400, `messages[0].content must be a non-empty string` |
| Key not starting with `sk-` | 401, `LiteLLM Virtual Key expected` |
| about 2,500-word input (2,657 prompt tokens) | 200 after 29 s, but `finish_reason: "length"` at 6,632 output tokens. **Long input is silently truncated**; the signal is `finish_reason`. |

`max_tokens` is rejected (400: generation is pinned server-side). The first probe round guessed `target_language` / `script`; the server happened to accept those too, but the code uses only the documented names.

## indic-speak: `POST /v1/audio/speech`

Body: `model, input, voice, instructions` where `instructions` is a JSON string `{"lang", "style"?}`. Returns WAV, PCM16, 24 kHz, mono.

| Probe | Result |
|---|---|
| 93 Tamil chars | 200, 7.8 s of audio, **8 s latency** (about 12 chars per second of speech) |
| `style: "news"` | 200, 6.5 s |
| English text with a Hindi voice, `lang: "en"` | 200 (there are no English-only voices) |
| Hindi voice with `lang: "ta"` | 200. **Voice/language mismatch is not rejected.** |
| Unknown voice | **500** `internal_server_error` (not a 4xx) |
| Unknown style | **500** `internal_server_error` |
| 469 Tamil chars (about 39 s expected) | 200 after 34 s, but audio is **exactly 30.55 s**. Output is silently capped at about 30 s. |

## indic-ocr: `POST /v1/chat/completions`, `model: "indic-ocr"`

Body: one user message with an `image_url` data URL, plus `table_format` (`markdown` or `html`).

| Probe | Result |
|---|---|
| Synthetic page (Hindi lines + table) | 200 in 1.1 s. Markdown in `choices[0].message.content`; top-level `blocks: [{order, label, type, bbox_xyxy, conf, text}]`; `usage` present. Table returned as a Markdown table. |
| Bytes that are not an image | **200 with a `null` body.** Not an error status. |

The few wrong Hindi characters in the OCR output come from our test page: Pillow here lacks libraqm, so it draws Devanagari vowel signs in the wrong place. Real scans or a raqm-enabled render are needed for quality checks.

## indic-transliterate: `POST /v1/chat/completions`, `model: "indic-transliterate"`

Documented body (re-probed with the `tl_*` cases): `messages` plus **`target_language_code`** (the language of the text) and **`target_script`** (`roman` or `native`).

| Probe | Result |
|---|---|
| Hindi to roman, Tamil to roman | 200, correct romanisation |
| roman to Hindi (`native`) | 200, round-trips back to the original Devanagari |
| `target_language_code: "fr"` | 400, lists supported codes |
| `target_script: "cyrillic"` | 400, lists supported scripts |

The first probe round used guessed names (`language`, `script`), which were ignored and gave wrong output; the documented names work.

## Design consequences

1. **Validate before calling.** Language codes (transcribe ignores bad ones), voices and styles (bad ones cause 500s), voice-language match, image validity, non-empty text. A 500 from a bad voice would otherwise be retried as if it were a server fault.
2. **Chunk below the hard limits, with margin.** Transcribe: windows of at most about 25 s cut at silences. Speak: at most about 200 characters per request (about 16 s). Translate: windows of about 600 input tokens.
3. **Detect silent truncation** in code: translate `finish_reason == "length"`; speak output duration against the expected duration from character count; transcribe by keeping windows short. Verification by back-transcription is still needed for the repeat/omit defect.
4. **Subtitles need our own timings.** No timestamps come back, so SRT cues come from our silence-based segmentation: each segment's start and end are known from the cut points.
5. **Always pass a language hint to transcribe when known.** Auto-detect is 4x slower and doesn't report the language. For unknown audio, transcribe without a hint once, then classify the script.
6. **Cost from usage where available** (translate, OCR tokens), from measured audio seconds (transcribe) and input characters (speak) otherwise.
7. **Latency needs concurrency.** Speech runs about 1 s per second of audio and transcription about 1 s per second of audio. A 2-minute summary is roughly 2 minutes of sequential speech calls, so run up to 4 in parallel.
8. **Retry policy:** retry 429, 502, 503, 504 and timeouts. Retry a 500 at most once, and only after input validation passed. Never retry 400, 401, 422.
9. **Treat a 200 with an empty or `null` body as an error.**
10. **Keep `transliterate_text` as an optional tool.** It works with the documented parameters. Its key is optional, so without it the tool returns `MISSING_API_KEY`.
11. **Never use FFmpeg's `whisper` filter.** The local FFmpeg 9.1 full build ships one (whisper.cpp ASR). A test greps the code for it.
