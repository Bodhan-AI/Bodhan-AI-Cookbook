# Tool and agent design notes

## Why these tool boundaries?

Four model calls are exposed at an atomic level (`ocr_document`, `transcribe_audio`, `translate_text`, `speak_text`) so another agent can compose them. Two higher-level deterministic helpers (`translate_segments`, `create_subtitles`) encapsulate timestamp-safe orchestration. Inspect/read/save tools make the server practical without teaching the LLM to read binary files or write Indian-language text.

The same `ToolHub` backs both the MCP stdio server and the task CLI. It enforces path safety, language/voice validity, budgets and cache; this avoids two diverging implementations.

## Required model wiring

| Capability | Exact endpoint | Wire payload | Published model |
|---|---|---|---|
| ASR | `POST /v1/audio/transcriptions` | multipart file, `model`, optional `language` | `indic-transcribe` |
| Translation | `POST /translate`; on HTTP 404 try `/v1/chat/completions` | JSON text/target or model chat request | `indic-translate` |
| TTS | `POST /v1/audio/speech` | JSON `model`, `input`, `voice`, `instructions` JSON-string with `lang` | `indic-speak` |
| OCR | `POST /v1/chat/completions` | `indic-ocr`, user image_url data URI generated *inside the tool*, optional table_format | `indic-ocr` |

Every API key is per-model and read from an environment variable. Base URL defaults to `https://api.bodhan.ai`; no other providers are used for those four capabilities.

## Document, timing and size limits

- `ocr_document` uses PyMuPDF **render to pixels** for every PDF page, even if selectable text exists; it calls Indic-OCR once per page. Failure on one page is recorded rather than silently glossed over.
- `transcribe_audio` converts media to 16-kHz PCM and sends windows of at most 25 seconds. Segment start/end times refer to those windows; there is no false claim of precise word timing.
- `speak_text` uses short chunks of ~210 characters, concatenates WAV using ffmpeg and optionally encodes MP3. Fixed voices come from the Cookbook registry; output is labelled `ai_generated`.
- `translate_text` preserves structural Markdown markers and math/numeric tokens; failed preservation triggers a protected-token fallback and an explicit QA warning.
- Multi-language inputs use an English pivot for Indic-to-Indic translation to avoid unsupported assumptions about direct source-pair handling.

## Verification and error recovery

Translation returns QA data: exact numbers, exact math tokens, warnings, and a *diagnostic* similarity to a Bodhan backtranslation. Backtranslation can be wrong, so the numeric score is not treated as authoritative.

TTS is verified by sending the generated audio back through Indic-Transcribe. Character similarity estimates whether speech omitted/repeated content; the QA sidecar retains `checked` status and a reason if verification failed. This is not native-speaker perceptual scoring.

The gateway retries transient `429`/`5xx` and network exceptions at most twice after the first call, using bounded backoff. Validation and unsupported-language errors are returned immediately. A budget refusal halts the agent, which writes a non-success status. Actual vendor bills may differ from the estimated reservation, and costs from the independent LLM are not quoted.

## Prompt injection and data exposure

The general-purpose LLM is told to ignore all instructions found inside documents, OCR, transcripts and tool output. Tool responses are marked as untrusted data. The agent doesn't execute source text, invoke a shell tool, or permit arbitrary path escape. Large outputs are passed by file path, not inline media. The MCP service always uses its configured `BODHAN_WORKSPACE`.

The in-recipe `.gitignore` excludes credentials, audio, images, PDFs, cache and generated outputs. All demo sources must be rights-cleared.

## Acknowledged missing work

The assignment calls for six *live* demos including a graceful refusal, independent MCP client verification, sample traces, and a video. As of 22 November, the Windows development copy passed 37 mocked/offline tests, and live Bengali speech, Hindi English-subtitle, OCR and independent MCP Inspector runs have succeeded. A direct Marathi output passed an offline structural audit, but a separate full autonomous Marathi task failed, and the initial QA sidecar flagged numbers; that discrepancy must remain visible. Tamil chapter narration, Telugu selection, the public demo video and a final PR were not verified in the supplied evidence. See `LIVE_VALIDATION_RESULTS.md`.
