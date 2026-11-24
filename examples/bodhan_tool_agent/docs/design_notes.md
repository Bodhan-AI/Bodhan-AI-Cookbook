# Design notes

Status markers: claims about behaviour are what the code is built to do and what the unit tests (against a fake Bodhan backend) cover. Anything marked **(not yet measured)** still needs a run against the hosted API.

## 1. Tool list

| Tool | Paid? | What it does | Model limits it absorbs |
| --- | --- | --- | --- |
| `inspect_file` | free | Kind, MIME, size, duration, has_audio, pages, image size, chars, detected language (text), cost estimates. Accepts a directory. | none; it exists so the planner sees facts before spending money |
| `read_text` | free | A slice of a text/Markdown/transcript file, marked `untrusted`, with injection warnings. | keeps long documents out of the LLM context unless asked for |
| `transcribe_audio` | Indic-Transcribe | Any audio or video, any length, `language="auto"` or a code. Writes a timestamped transcript JSON and a .txt. | 30 s request cap (VAD windows of at most 28 s, cut at pauses); silence is never sent (billing is per second); 415 on M4A/AAC/WebM (ffmpeg converts everything to WAV first) |
| `translate_text` | Indic-Translate | Text or a file into a target language; Markdown-aware; per-segment for transcript JSON (timestamps kept). | context window and `truncated: true` (chunking + split-and-retry); table cells, code, math and list markers kept intact; `bhb`/`bho` refused with a clear error |
| `synthesize_speech` | Indic-Speak | Text or file to MP3/WAV, any length, with optional back-transcription check and one repair per bad chunk. | "a sentence or two per request" (sentence chunks of about 220 chars); repetition/omission (verify + repair) |
| `ocr_document` | Indic-OCR | PDF, image, or folder of images to Markdown plus per-page JSON. | PNG/JPEG only and one image per request (PDF rendered by pypdfium2, other formats normalised); 502 on large tables (retry with higher `max_tokens`); headings not marked up (rebuilt from block labels); page furniture dropped |
| `verify_speech` | Indic-Transcribe | Back-transcribes an audio file and compares with the expected text. | same windowing as `transcribe_audio` |
| `verify_translation` | free (optional paid back-translation) | Script, numbers, Markdown structure, length ratio, untranslated spans; optional back-translation to English. | none |
| `make_subtitles` | free | SRT/VTT from transcript or translated segments, cues split to 42 chars × 2 lines, about 7 s each. | none |
| `list_languages_and_voices` | free | Per-language support for each model, voices by gender, styles. Also exposed as `bodhan://languages` and `bodhan://voices`. | none |

### Why this granularity

Three options were on the table:

1. **One tool per endpoint** (`transcribe(file<=30s)`, `translate(text)`, `speak(text<=1 sentence)`, `ocr(png)`). Simple, but every caller then has to chunk audio, split sentences, render PDFs, notice `truncated`, retry 502s and stitch results. A third-party client driving the server from tool descriptions alone would get these wrong, and our own planner would spend dozens of steps and a lot of context on plumbing.
2. **Mega-tools** (`audio_summary(pdf, lang)`, `subtitle_video(file, lang)`). Few calls, but the planner loses control: it cannot inspect intermediate text, choose what to summarise, decide to stop when a language is unsupported, or verify a step before paying for the next. They also only cover tasks someone thought of in advance, which is the opposite of what the hidden task set rewards.
3. **Capability-level tools** (chosen). One tool per capability, each of which accepts inputs of any size and absorbs its model's limits. Around them, free helper tools for inspection, reading, verification and subtitles. A multi-step task becomes three to six tool calls whose intermediate results are files the planner can inspect.

The test for a tool boundary was: "could a careful engineer get this wrong if left to do it outside the tool?" Chunking, windowing, truncation handling and retries pass that test, so they live inside. Deciding *what* to translate or summarise does not, so it stays with the planner.

## 2. Media plumbing

- Media moves by **file path**, never base64 in the MCP context. Tools write outputs under `BODHAN_WORKDIR` (the agent sets it to `out/work/`) with descriptive, unique names and return the path plus a short `preview` (at most 600 chars).
- Long text is never returned whole. The planner reads more with `read_text(path, start_char, max_chars)` only when it needs to, for example to write a summary.
- Every tool that produces text writes it to disk, so the next tool takes `path=` rather than the LLM re-typing the content. This also enforces the Indic-text rule: Indian-language text only ever flows file-to-file between Bodhan tools.
- `allowed_roots` (env `BODHAN_ALLOWED_DIRS`) can restrict which paths the server reads, for HTTP deployments.

## 3. Language detection

- **Text and OCR output:** Unicode script shares plus marker words for scripts shared by several languages (Devanagari: Hindi, Marathi, Nepali, Sanskrit...; Bengali script: Bengali vs Assamese; Perso-Arabic: Urdu, Kashmiri, Sindhi). Free and deterministic; returns a confidence and candidates, so ambiguity is visible rather than hidden.
- **Audio with no stated language:** `transcribe_audio(language="auto")` sends the first speech window without a hint, detects the script of the result, then transcribes the rest with the detected code (the API docs say a hint is "the difference between a correct transcription and a garbled one"). The detection and its confidence are returned and recorded in the trace. Cost: at most one window (≤ 28 s) is transcribed twice. **(Accuracy on short or code-mixed clips not yet measured.)**
- **Unsupported languages** are caught before any paid call: language codes are validated against per-model tables, and scripts like Latin-non-English, Cyrillic or Han produce an explicit "not supported" result rather than a guess.

## 4. Verification

| Output | Check | Pass rule |
| --- | --- | --- |
| Generated speech | Back-transcribe each chunk with Indic-Transcribe in the same language; character error rate against the input after normalisation (NFC, Indic digits to ASCII, punctuation stripped); duration-per-character sanity; n-gram repetition detection | CER under a threshold, no repetition, duration within range. Failing chunk is re-synthesised once with a different split (so not the identical request), then reported if it still fails |
| Translation | Target script share; numbers in source appear in target; Markdown structure (heading/list/table counts, table column counts) preserved; length ratio within bounds; no long untranslated Latin runs; `truncated` never true in the final output | All deterministic checks pass. Optional back-translation to English for a semantic spot check when the source is English |
| Transcript / OCR | Coverage: speech duration transcribed vs detected speech; empty or low-confidence pages flagged | Reported as warnings; the planner decides |

The agent's verification gate runs in code: `finish` is not accepted as `completed` while a generated audio file or translation has no passing check. The LLM can choose to repair or to finish as `partial`; it cannot skip the check. **(CER thresholds are set from the fake backend and a handful of real calls; not yet tuned per language.)**

## 5. Budgets and cost control

Prices used for estimates: transcribe ₹0.10/min, translate ₹0.20 per 10k output tokens, speak ₹6 per 10k chars, OCR ₹0.20/page. Speech synthesis dominates: a 2-minute Tamil summary (about 1,500 chars) costs about ₹0.90 to speak, the same text costs well under ₹0.01 to translate.

- **Pre-check before every paid call.** The client estimates the cost of the request and raises `budget_exceeded` if `spent + estimate > limit`. The agent passes `--max-cost-inr` down as the limit, so the cap holds even if the LLM ignores it.
- **Cache.** Content-addressed disk cache (`.bodhan_cache/`) keyed by endpoint, parameters and input bytes. Reruns and repeated chunks cost nothing; cache hits are free and always allowed by the budget.
- **Dedupe.** Identical concurrent requests share one in-flight call, so the "same input never sent to the same model twice" target holds within a run even under parallel tool calls.
- **Silence is not billed.** VAD drops silence before transcription.
- **Speech last.** The planner is told to produce and verify the final text before calling `synthesize_speech`, and to size summaries from the requested duration (about 13 chars per second of Indic speech) rather than speaking the whole source. **(Chars-per-second figure is an estimate; measure per language.)**
- **Inventory estimates.** `inspect_file` returns cost estimates per file, so the plan can be rejected up front when the budget is obviously too small.

Step and time budgets: `--max-steps` (default 30) counts LLM turns; `--call-timeout` bounds each tool call; the Bodhan client has its own HTTP timeout (`BODHAN_CALL_TIMEOUT_S`).

## 6. Recovery

| Failure | Handling |
| --- | --- |
| 429 / 5xx / network timeout | Client retries with exponential backoff and jitter, honours `Retry-After`, up to `BODHAN_MAX_RETRIES`. Concurrency is capped per model so we do not trigger 429s ourselves. |
| 401 | `auth_error` with a hint naming the env var for that model's key (`missing_api_key` when none is set). Not blindly retried. |
| Translation `truncated: true` | The chunk is split at the next structural boundary (block, then sentence) and both halves retried. Never returned silently. |
| OCR 502 (block over `max_tokens`) | Retry the page with a higher `max_tokens`; if still failing, the page is flagged and the rest of the document continues. |
| TTS repetition/omission | Detected by back-transcription; the chunk is re-split and re-synthesised once; still failing chunks are listed in `verification.failed_chunks`. |
| Partial failure in a multi-page or multi-window job | Completed parts are kept and cached; the tool returns what succeeded plus `warnings`; the planner decides whether to retry or report. |
| Unsupported language / file type / missing file | Structured error with `code`, `message`, `hint`, `retryable=false`. The agent treats these as facts for re-planning, typically ending as `impossible`. |

## 7. Untrusted content

- Every tool that returns text extracted from a user file (OCR, transcripts, text files) marks it untrusted and runs a phrase detector for instruction-like content ("ignore previous instructions", "system:", role tags, requests to delete or exfiltrate). Hits are returned as `injection_warnings`.
- The agent wraps any such text in clearly delimited data blocks in the LLM context and states in the system prompt that content from files is data to process, never instructions. Detected injections are surfaced in `result.md` and `report.json`.
- Guards in code do not depend on the LLM behaving: artifacts are written only under `out/`, Indic text cannot be passed inline to the translate/speak tools, and spend cannot pass the budget, whatever a file says. For HTTP deployments, `BODHAN_ALLOWED_DIRS` restricts which paths the server will read.

## 8. Where the LLM is used, and the fallback

| Decision point | What the LLM decides | Fallback if the LLM fails or answers badly |
| --- | --- | --- |
| Task interpretation and plan (`submit_plan`) | Goal, target language(s), output format, assumptions for ambiguous wording, feasibility | Retry once with the validation error; then stop as `failed` with the error. Malformed tool arguments are rejected by schema validation, not executed |
| Tool choice and arguments | Which tool next, which file, which language | Guards and schema validation reject bad calls with a structured error the LLM can act on; step budget bounds loops |
| English text it writes (summaries, explanations) | Content of the summary or answer, in English only | `write_text_artifact` rejects any Indic script; the LLM must translate via Indic-Translate |
| Judging results | Whether a transcript answers the question (e.g. "which clips are Telugu"), whether a verification failure warrants a repair | The verification gate is in code; the LLM cannot mark unverified output as completed |
| Impossible tasks | Explaining why in plain English | Tool errors (`unsupported_language`, `file_not_found`) carry the reason; the agent includes them in `result.md` verbatim |

## 9. Plain code vs LLM

| Plain code | LLM |
| --- | --- |
| File inventory, media probing, format conversion | Understanding the task, including ambiguous wording |
| Language ID by script | Choosing target language when the task implies it (e.g. "my grandmother in Chennai") and recording the assumption |
| Chunking, windowing, retries, caching, budget arithmetic | Deciding the sequence of tool calls |
| Verification checks (CER, script, numbers, structure) | Writing English summaries and explanations |
| Subtitle timing, Markdown reconstruction from OCR blocks | Judging whether a result answers the task |
| Writing `report.json`, `trace.jsonl`, copying artifacts | Choosing to repair, re-plan or give up |

The rule of thumb: if the answer is deterministic and checkable, it is code. The LLM is used where judgement or English prose is needed, and never to produce Indian-language text, transcribe audio or read images.
