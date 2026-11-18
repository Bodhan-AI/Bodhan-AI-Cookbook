# Design Notes & Empirical Findings

## 1. Sovereign Foundation Model Integration

### 1.1 Zero-Fallback Policy
Per project requirements, the system interfaces directly with Bodhan AI endpoints (`https://api.bodhan.ai/v1`). No third-party model fallbacks (OpenAI Whisper, Google Translate, Tesseract OCR) are permitted. All transcription, translation, document extraction, and speech synthesis are performed exclusively by Bodhan foundation models.

### 1.2 Model Surface & Endpoints
| Capability | Model Name | Endpoint | Pricing |
| :--- | :--- | :--- | :--- |
| **Translation** | `indic-translate` | `/chat/completions` | ₹0.20 / 10k output tokens |
| **OCR** | `indic-ocr` | `/chat/completions` | ₹0.20 / page |
| **Transcription** | `indic-transcribe` | `/audio/transcriptions` | ₹0.10 / minute (pro-rata per second) |
| **Speech (TTS)** | `indic-speak` | `/audio/speech` | ₹6.00 / 10k input characters |

---

## 2. Empirical Findings & Billing Measurement

### 2.1 Audio Transcription Billing Rounding
- **Empirical Test**: Live audio clip with duration `1.962667s` was transcribed.
- **Measured Cost**: Console billed **₹0.003271**, perfectly matching pro-rata calculation `(1.962667 / 60.0) * 0.10 = ₹0.0032711...`.
- **Finding**: Billing is strictly pro-rata per second. It is NOT rounded up to the nearest whole minute (which would have been ₹0.10) and is not a flat minimum fee per request.

### 2.2 Text-to-Speech Sentence Chunking & WAV Format
- **Chunking Limit**: Text is packed by sentence delimiters up to 250 characters. Documents longer than 250 chars are split along sentence and word boundaries, respecting Bodhan's documented guidance ("a sentence or two, about 30 seconds").
- **Concatenation & Transcoding**: Multi-chunk audio is joined and transcoded via `ffmpeg` into a standard format: 24 kHz, 16-bit PCM, single-channel (mono) WAV. If a single chunk already meets the target PCM WAV specification, transcoding is bypassed to avoid redundant re-encoding.
- **Pacing Plausibility**: Audio verification tests that speech duration falls within 4 to 25 characters per second.

### 2.3 Voice Gender Classification Disclosure
- **Catalog Sourcing**: Bodhan's API documentation at `https://console.bodhan.ai/api-docs` provides 45 voice names grouped by language code without an explicit gender column.
- **Convention-Based Inference**: Voice gender is inferred from traditional Indian naming conventions (e.g. Kavya, Anitha as female; Amit, Arun as male).
- **Listening Status**: Voice genders **have not been verified by human listening**.
- **Transparency**: All `indic_speak` outputs include `"voice_gender_source": "inferred"` to signal this status explicitly. Setting `voice="male"` resolves to the first documented male voice for that language or raises `NO_MALE_VOICE`.

---

## 3. Post-Mortem of Duplicate Spend (`hist_003` & `hist_004`)

### 3.1 Incident Analysis
- At `2026-11-17 00:15:00 IST` (`hist_003`), a live test script invoked `IndicSpeakTool` on a live endpoint. The Bodhan server synthesized the audio, returned HTTP 200, and billed ₹0.0156.
- A client-side response hook `log_speak_response` had called `await response.aread()` on a streaming response. When `aread()` was called again during body processing, `httpx` raised `ResponseNotRead`.
- The exception crashed the script *after* the request had been billed and *before* writing to `.cache/bodhan_speak/`.
- One minute later at `2026-11-17 00:16:00 IST` (`hist_004`), the developer fixed the hook and re-ran the script. Because no disk cache entry existed, a second paid request was dispatched, incurring another ₹0.0156.

### 3.2 Implemented Architectural Fixes
1. **Cache-Before-Processing**: Raw response bytes (audio, JSON) are written to disk cache immediately upon receiving HTTP 200, before any ffmpeg transcoding, parsing, or post-processing begins.
2. **Pre-Dispatch Spend Ledger**: A ledger entry with status `"dispatched"` is recorded *before* the HTTP request is transmitted over the wire, then updated to `"completed"` or `"failed"` with the status code.
3. **Strict Retry Policy**: Retries are permitted only on connection drop (before response bytes) or HTTP 429/5xx. Any exception occurring after receiving a 2xx response never triggers a network retry; it uses cached bytes or returns `UNKNOWN_BILLED`.

---

## 4. Verification Heuristics Disclosure
Automated verification uses the following thresholds:
- **Speech**: Normalized CER $\le 0.20$ (pass), $\le 0.40$ (warn), $> 0.40$ (fail).
- **Translation Similarity**: English back-translation similarity $\ge 0.35$ (pass), $\ge 0.20$ (warn).
- **Disclosure**: These thresholds are heuristic baselines derived from empirical testing on short sentences, rather than tuned on large production evaluation corpora.
- **Coverage Assertions**: Non-similarity checks normalize Unicode Indic digits across scripts to ASCII `0-9` using `unicodedata.decimal()` and verify full number preservation.
