# Design notes

## Tool granularity

I chose capability-level tools that handle model limits, four free helpers, and a language detector
that calls ASR only for audio or video.

| Option | Problem |
|---|---|
| One thin tool per API call (`transcribe_30s_window`, `speak_sentence`, ...) | The planner has to chunk audio, split sentences, join WAVs and track timings: many LLM turns, and every client re-implements the same plumbing badly. |
| Task-level tools (`make_audio_summary`, `dub_video`) | Hides the decisions we want the planner to make (which language, what to summarise, whether to stop), and fails on any task not anticipated. |
| **Capability tools that own model limits** (chosen) | A caller says "transcribe this file" or "speak this file in Bengali"; the tool handles 30-second windows, TTS input size, the translation context, multi-page PDFs, retries and verification. The planner still composes the steps. |

The test I applied: could a third-party agent use each tool correctly from its description alone,
without knowing any model limit? The descriptions state inputs, outputs, limits, cost and the next tool
to call.

| Tool | Model | Limit handled inside |
|---|---|---|
| `inspect_files` | none | kind, duration, pages, script-based language, cost estimates; folders expanded; missing paths reported, not raised |
| `read_text` | none | pages through long files (offset/max_chars) so large text never floods the context |
| `list_voices` | none | voice library, defaults per language and gender, styles |
| `detect_language` | Transcribe (audio probe only) | Unicode script, language-specific letters and common words for text. Audio uses a probe up to 8 seconds, bounded by `max_cost_inr`. |
| `transcribe_audio` | Indic-Transcribe | any length; energy-based VAD cuts at pauses into windows <= 28 s (model limit 30 s); trims silence (billed per second); skips silent audio; video audio extracted with ffmpeg; `language="auto"` detects from a probe then uses it as a hint; reports unsupported spoken languages |
| `translate_text` | Indic-Translate | structure templates (Markdown/HTML tables, lists, segment JSON, SRT/VTT) so only text is sent; paragraphs chunked to <= 1,800 chars; short cells/lines batched with an alignment check and per-line fallback; truncated outputs re-split; identical cells translated once; coverage/script verification |
| `synthesize_speech` | Indic-Speak (+ Transcribe for checks) | Markdown stripped; sentence chunks <= 220 chars; script/language mismatch rejected before spending; parallel synthesis; per-chunk back-transcription; failed chunks regenerated smaller; WAVs joined with pauses; MP3 tagged AI-generated |
| `verify_speech` | Indic-Transcribe | any-length audio vs text; similarity and length ratio (repetition/omission) |
| `ocr_document` | Indic-OCR | PDFs rasterised per page (API takes one PNG/JPEG); EXIF rotation, resize to <= 2400 px; blank pages skipped; 502 retried with larger `max_tokens`; per-page language |
| `make_subtitles` | none | splits long segments into cues with proportional timing, 2 lines x 42 chars |

Errors are one JSON shape, `{"error": {"code", "message", "hint", "retryable", "details"}}`, with
stable codes (`unsupported_language`, `language_mismatch`, `budget_exceeded`, `file_not_found`,
`missing_api_key`, ...). Schema violations are reported by the SDK with the offending field name. A
catch-all turns any unexpected exception into `internal_error`, so a bad input can never crash the
client.

## Media plumbing

Artifact-producing tools write under the server's output folder (`out/artifacts/intermediate/` when
called by the agent) and return paths. Transcription, translation and OCR include a short preview and
inline the full result only for short text. The agent caps tool responses before adding them to
planner context. Base64 is confined to the Indic-OCR HTTP request.

## Where the LLM is used, and where it is not

| Decision | Owner | Fallback |
|---|---|---|
| Understand the task, feasibility, deliverables, assumptions (plan JSON) | LLM | Unparseable plan: the raw text is kept as the plan and the loop continues |
| Which tool next, with which arguments; parallel calls | LLM | Invalid JSON arguments or unknown tool: structured error back to the LLM |
| English summaries, explanations, the final answer | LLM | Provenance guard rejects any Indian-language text it did not copy from a tool |
| Judging tool results, deciding to retry or stop | LLM | Code gate at `finish` re-checks; budgets stop it regardless |
| Input inventory, language guess for text, cost estimates | code | - |
| Chunking, joining, timings, table structure | code (server) | - |
| Budgets, timeouts, cost caps, duplicate suppression | code (agent) | - |
| Verification of deliverables | code (tools + gate) | - |
| Indian-language text, speech, transcripts, OCR | Bodhan models only | - |

The planner receives file references for audio and images. The agent checks Indian-script text in
authored files, inline tool arguments and the final answer against Bodhan outputs and the user's
input text. The planner can quote those sources. New Indian-language content is produced by
Indic-Translate.

## Verification

* **Speech**: always on (the agent forces `verify=true`). Each chunk is transcribed back with Indic-Transcribe and compared with its text (character similarity after normalisation, length ratio, repetition). Bodhan notes Indic-Speak can repeat or omit content; such chunks are regenerated as smaller inputs.
* **Translation**: every unit translated, none empty, none truncated, output mostly in the target script, overall length ratio plausible.
* **Transcription**: per-segment empty/repetition warnings.
* **Gate at finish**: deliverables must exist and pass; planned deliverable kinds must be present; otherwise finish is rejected (twice at most) and then downgraded to `partial` with the problems listed.

## Budgets and cost control

* The agent checks step, cost and elapsed-time budgets before each action turn. It limits a parallel batch to the remaining steps and applies `--call-timeout-s` to each tool call.
* The agent splits the remaining rupees across potentially paid calls. Each tool checks the estimate against its share, including the ASR probe in `detect_language`. A call that cannot fit returns `budget_exceeded` before sending the request.
* The agent syncs reported model usage after each turn from `bodhan://usage`. Successful model requests are counted even if a later request in the same tool fails. Translation falls back to an estimate when the response omits token usage, and planner charges are included when token prices are configured.
* The disk cache keys successful results by model, parameters and input bytes. Identical in-flight calls share a request. Configured retries can resend requests that failed.
* Cheap-first: silence trimmed before ASR, blank pages skipped before OCR, numbers/symbols not sent to translation, already-target-language text copied without a call, script mismatch caught before TTS.

## Unknown and untrusted input

* Language: script-unique languages (ta, te, kn, ml, gu, pa, or, as by its letters) are certain; Devanagari languages are separated by function words and reported with confidence and candidates. Audio uses a short probe. Latin-script speech is checked for French/Spanish/German/Portuguese/Italian function words so unsupported speech is reported rather than mistranslated.
* Prompt injection: transcript, OCR and file text reaches the LLM inside `<untrusted_content>` tags; instruction-like text is flagged in the trace and `report.json`. Even if the LLM were fooled, code limits what it can do: paths are resolved inside the inputs and output folder, costs are capped, and Indian-language text cannot be invented.

## Recovery

See [agent_flow.md](agent_flow.md#retry-layers). Partial OCR failures keep the pages that worked and
report the rest; an all-pages failure is an error. When the budget runs out the run stops cleanly
and `result.md` lists the steps that completed, without presenting their outputs as deliverables.
