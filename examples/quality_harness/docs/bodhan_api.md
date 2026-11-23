# Bodhan API: verified behaviour

Checked with live calls on 2026-11-20 against the hosted API, plus the API reference at
console.bodhan.ai/api-docs. Where the two disagreed, the live call wins. Raw text responses are in
`tests/fixtures/bodhan/`. Total cost of the probe calls: about ₹0.15.

## Common

- Base URL `https://api.bodhan.ai/v1`, header `Authorization: Bearer <key>`. Each key is limited to its own model
  (calling another model with it returns 403 `key_model_access_denied`).
- Rate limits, from the response headers on the translate key: 120 requests/minute and 10 parallel requests
  (`x-ratelimit-api_key-limit-requests`, `x-ratelimit-api_key-limit-max_parallel_requests`). Over the limit you get
  429 with `retry-after`. Running out of credit also returns 429, with a different message.
- **A second, unpublished per-model limit** (seen 2026-11-21 on Indic-Translate): `429 Maximum rate limit reached for the model`
  with `retry-after` of about 55 s, while the per-key headers still showed spare requests. It held a 90-item run to
  about 17 calls/minute. Its size isn't in any header. The client pauses all calls of that model together and slows
  down after a 429 (`clients.py`, `RATE_LIMIT_ROUNDS`).
- Error bodies are OpenAI-style: `{"error": {"message", "type", "param", "code"}}`.

## Indic-Translate

- **Endpoint:** `POST /v1/chat/completions` with `"model": "indic-translate"`. The reference also documents
  `POST /translate`, but it returns 404 (tried `/translate`, `/translate/` and `/v1/translate`).
- **Request:** a single user message holding the source text, plus `"target_language_code": "<code>"`.
  `target_language` is accepted too. There is no source-language field, because the source is detected.
- **If the target is omitted, the output is Hindi.** The client must always send the target code.
- **Codes:** as, bn, brx, doi, en, gu, hi, kn, kok, ks, ks-Deva, mai, ml, mni, mni-Beng, mr, ne, or, pa, sa, sat,
  sd, sd-Arab, ta, te, ur. Any other code returns 400 with this list in the message.
- **`temperature` returns a 400.** Decoding is fixed to greedy on the server, and an identical request gives
  byte-identical output. Only `max_output_tokens` can be set.
- **Response:** standard `chat.completion`; the translation is in `choices[0].message.content`, with
  `usage.completion_tokens` (the billed quantity) and `prompt_tokens`. Watch for `finish_reason` other than
  `stop`, which means the output was truncated.
- **Behaviour seen:**
  - Markdown headings, tables, inline code, links and `$…$` LaTeX were all kept intact (en→ta).
  - Digits stay ASCII (`100`, `5 मार्च 2023`).
  - Unit symbols may be spelled out: `°C` became `डिग्री सेल्सियस`. The number check must accept a spelled-out unit.
  - Back-translation hi→en round-tripped the sample sentence almost verbatim: "100 degrees Celsius",
    "March 5, 2023".

## Indic-Speak

- **Endpoint:** `POST /v1/audio/speech`, JSON body
  `{"model": "indic-speak", "input": "<text>", "voice": "<name>", "instructions": "{\"lang\": \"hi\"}"}`.
  `instructions` is a JSON string, and `lang` is required inside it. An optional `style` can also go in it.
- **Voices** are given by name, for example Kavya, Suhani and Amit (hi), Anitha and Arun (ta), Ishita and Sourav (bn).
  The reference lists 45 voices and says any voice can read any language.
- **Languages (`lang`):** as, bn, brx, doi, en, gu, hi, kn, kok, ks, mai, ml, mni, mr, ne, or, pa, sa, sat, sd, ta,
  te, ur. Neither page has a list of 13 "production-tier" languages (open question 3 is still unanswered).
- **Output:** WAV, PCM16, mono, 24 kHz. The `content-type` header says `audio/mpeg`, but the bytes are WAV.
  Detect the format from the file bytes and ignore the header.
- **Latency:** about 9.3 s for 9.2 s of audio from 95 characters, so synthesis runs at roughly real time.
- **Input size:** the reference says to keep each request to a sentence or two (about 30 s of speech), and the
  text is not split for you. Long items must be synthesized sentence by sentence.

## Indic-Transcribe

- **Endpoint:** `POST /v1/audio/transcriptions`, multipart form: `file`, `model=indic-transcribe`,
  optional `language`.
- **There is only one model,** `indic-transcribe`. The brief's "Core" and "Flex" names are refused with 403,
  so a second-ASR opinion is not available.
- **Response:** `{"text": ...}` only. `response_format=verbose_json` is accepted, but `words`, `segments`,
  `duration` and `language` come back null. `timestamp_granularities[]` returns a 422.
  **No timestamps,** so audio time spans must come from our own signal analysis.
- **Limits:** 30 s of audio per request. WAV, FLAC, OGG and MP3 are accepted; M4A, AAC, WebM and AMR return 415.
- **Behaviour seen** (transcribing the Indic-Speak audio of the Hindi sample):
  - Numbers come back as **English number words in Devanagari**: `100` → `हंड्रेड` ("hundred"),
    `2023` → `ट्वेंटी ट्वेंटी थ्री` ("twenty twenty three"), `5` → `फाइव` ("five").
  - Abbreviations are expanded: `डॉ` → `डॉक्टर` (doctor).
  - All punctuation is dropped.
  - Everything else matched word for word.
  - The comparison must therefore treat numbers and abbreviations as wildcards (plan §12.3) and strip punctuation.

## What this changes in the plan

- The client uses one transport, chat completions, for translation and back-translation, and always sends
  `target_language_code`.
- There's no second ASR model. Borderline speech flags need another tie-breaker: the signal checks, the speech
  rate, and the LLM adjudicator.
- Speech without timestamps: time spans come from silence and energy analysis, or are estimated in proportion
  to text position.
- Synthesis and transcription work on sentence-sized chunks, because both are limited to about 30 s.
- Concurrency defaults: 10 parallel requests per model, and stay under 120 requests/minute.
