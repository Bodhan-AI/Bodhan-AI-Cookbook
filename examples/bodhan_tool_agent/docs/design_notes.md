# Design notes

## Tool granularity

I chose one tool per **capability**, each owning its model's limits, plus five free helpers. Three
options were on the table:

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
| `detect_language` | Transcribe (audio probe only) | text: Unicode script + script-specific letters + function words; audio: <= 8 s probe |
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

Files move by path. Tools write outputs under the server's output folder (for the agent:
`out/artifacts/intermediate/`) and return `*_path` fields plus a preview of at most 1,200 characters;
full text is inline only below 6,000 characters. Base64 exists only inside the HTTP call to Indic-OCR.

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

The LLM never sees audio or images. Indian-language text reaches the user only from Bodhan outputs:
the agent checks every Indian-script run the LLM writes (files, inline tool text, the final answer)
against a corpus of tool outputs and input files, and rejects anything new.

## Verification

* **Speech**: always on (the agent forces `verify=true`). Each chunk is transcribed back with Indic-Transcribe and compared with its text (character similarity after normalisation, length ratio, repetition). Bodhan notes Indic-Speak can repeat or omit content; such chunks are regenerated as smaller inputs.
* **Translation**: every unit translated, none empty, none truncated, output mostly in the target script, overall length ratio plausible.
* **Transcription**: per-segment empty/repetition warnings.
* **Gate at finish**: deliverables must exist and pass; planned deliverable kinds must be present; otherwise finish is rejected (twice at most) and then downgraded to `partial` with the problems listed.

## Budgets and cost control

* `--max-steps` (tool calls), `--max-cost-inr`, `--max-runtime-s`, `--call-timeout-s`, enforced in code before every LLM turn.
* Each paid call gets `max_cost_inr` = remaining budget (split across parallel calls). Tools estimate before spending and refuse with `budget_exceeded`, so the budget is never overshot by a single call.
* Spend is synced after each turn from the server's `bodhan://usage` ledger, so costs inside failed tool calls are counted.
* No waste: a content-addressed disk cache (key = model + parameters + input bytes) and in-flight de-duplication mean the same input is never sent to the same model twice, within a run or across reruns. The agent also suppresses identical repeated tool calls. Retries after a failed request are the only resends.
* Cheap-first: silence trimmed before ASR, blank pages skipped before OCR, numbers/symbols not sent to translation, already-target-language text copied without a call, script mismatch caught before TTS.

## Unknown and untrusted input

* Language: script-unique languages (ta, te, kn, ml, gu, pa, or, as by its letters) are certain; Devanagari languages are separated by function words and reported with confidence and candidates. Audio uses a short probe. Latin-script speech is checked for French/Spanish/German/Portuguese/Italian function words so unsupported speech is reported rather than mistranslated.
* Prompt injection: transcript, OCR and file text reaches the LLM inside `<untrusted_content>` tags; instruction-like text is flagged in the trace and `report.json`. Even if the LLM were fooled, code limits what it can do: paths are resolved inside the inputs and output folder, costs are capped, and Indian-language text cannot be invented.

## Recovery

See [agent_flow.md](agent_flow.md#retry-layers). Partial OCR failures keep the pages that worked and
report the rest; an all-pages failure is an error. When the budget runs out the run stops cleanly
and `result.md` lists the steps that completed, without presenting their outputs as deliverables.
