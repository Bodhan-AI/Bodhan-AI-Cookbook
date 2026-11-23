# Design notes

## Goals

The agent must finish tasks it has never seen, and a third-party MCP client must use the tools correctly from their descriptions alone. Both goals push the same way: the tools absorb everything mechanical, and the LLM keeps only decisions that need judgment.

## Tool granularity

There is one tool per capability, not one per model endpoint and not one per task.

- One tool per raw endpoint would push chunking, format conversion, page rendering and retries onto the planner. Every planner, ours or a third party's, would have to do this, and it would get it wrong in different ways.
- One tool per task ("audio summary", "subtitle a video") hides control and fails on the first task that does not fit.
- The middle level is: transcribe any audio, translate any text or Markdown, speak any text, OCR any document, plus subtitles. Each tool is complete for its capability, so a planner composes tasks from four or five calls.

Two free helpers (`inspect_media`, `read_text`) let the planner look at inputs without loading them into context. `make_subtitles` exists because SRT timing and formatting are pure code. An LLM would do it slower, cost more and get it less right.

The tools that were left out are named in the trade-offs below.

## Model limits inside the tools

| Limit | Handling |
|---|---|
| Indic-Transcribe takes 30 s at most and refuses M4A, AAC and WebM | ffmpeg converts any input to 16 kHz mono WAV. `silencedetect` finds pauses; windows of at most 28 s are cut in the middle of the last pause that fits, or hard-cut if there is none. Windows that are fully silent are skipped and not billed. |
| Naming the language matters for accuracy | With no language given, the first window is sent without one. The language is read from the script of that text and passed for every other window. No window is sent twice. |
| Indic-Speak takes a sentence or two | Text is split at sentence ends (including the danda), then at commas and spaces, into pieces of at most 220 characters. The WAVs are joined with a 150 ms pause. |
| Translation context and silent truncation | Markdown is cut into units: lines, list items and table cells. Units are packed to at most 1,500 characters. A response with `truncated: true` or a 413 is split in half at a sentence boundary and redone, up to four levels deep. |
| Indic-OCR takes one PNG or JPEG page | PDFs are rendered with PyMuPDF at 150 DPI, capped at 2,400 px. Other image formats are converted. Pages run in parallel. A 502 (a block over the token limit) is retried once with `max_tokens` 8,192. Failed pages are listed; the good pages are kept. |

## Media plumbing

Media never enters the LLM's context. Inputs are absolute paths. Every tool writes its output to a file and returns the path, a preview of at most 1,500 characters and metadata. The LLM reads more with `read_text` only when it needs to: for example, to write a summary. Base64 exists only inside the OCR tool, on the way to the API.

## Language detection and ambiguity

Language is detected from Unicode script ranges, which the brief allows and which costs nothing. Scripts shared by several languages (Devanagari, Bengali, Perso-Arabic) report `ambiguous_with`, plus a few cheap markers (आहे and ळ for Marathi; ৰ and ৱ for Assamese). Every guess travels to the agent as a note, and the agent records it as an assumption in `result.md`. Runs are non-interactive, so the agent assumes and continues, and it says so.

## Verification

- **Speech:** every chunk of generated audio is transcribed back with Indic-Transcribe and compared with the input text. The score is character similarity after dropping spaces and punctuation, plus a length ratio that catches repeats and omissions. A failing chunk is re-synthesized once in two halves. That is a new input, so it does not break the "never send the same input twice" rule; a chunk that cannot be split is retried with the cache bypassed. The tool reports the lowest score and every failing chunk.
- **Translation:** a coverage check confirms that every unit came back, none was truncated, each is in the target script and has a plausible length, and none came back untranslated.
- **The finish gate (agent code):** `finish(status="success")` is refused once if any artifact failed verification, any audio artifact was never verified, a listed artifact does not exist, or the answer contains Indian-language text that no tool produced. A second attempt is accepted, but `success` is downgraded to `partial` and the problems are listed in `result.md`.

## Budgets

- **Steps:** each LLM turn is one step. At `--max-steps` the loop stops, and `result.md` lists the completed tool calls.
- **Rupees:** the agent passes the remaining budget to every paid tool as `max_cost_inr`. Each tool estimates its cost before it calls a model and refuses with `budget_exceeded` if the estimate is over. Actual costs come back in every result and are summed. When the budget is gone, the LLM is told to finish, and further paid calls are refused in code.
- **Time:** every HTTP request has a timeout (`BODHAN_TIMEOUT_S`), every tool call has one (`--call-timeout-s`), and every LLM call has one (`--llm-timeout-s`).

## Recovery

- HTTP 429, 5xx, timeouts and network errors are retried three times with exponential backoff and jitter, and `Retry-After` is respected. Other 4xx errors fail at once with a stable code and a hint.
- A semaphore limits parallel requests to Bodhan (`BODHAN_MAX_PARALLEL`, default 4).
- A disk cache keyed by the request content means a rerun after a crash or timeout pays only for the missing calls, and no input reaches the same model twice.
- Tool errors go back to the LLM as JSON with a hint, so it can fix the arguments or re-plan. Unexpected exceptions become `internal_error` results; the server never crashes the client.
- The LLM call is retried three times. If the provider rejects a forced first tool call, the agent falls back to `tool_choice: auto`.

## Untrusted content

Text from files, OCR and transcripts is data. Three layers protect against instructions hidden in it:

1. The system prompt says so, and every tool result is wrapped in a `<tool_output ... untrusted>` envelope.
2. **Path sandbox (code):** tool paths must be inside the inputs or the run folder, and outputs must go to `artifacts/` or `work/`. Injected text cannot make the agent read or write other files.
3. **Provenance (code):** any Indian-language text the LLM passes inline, writes to an artifact or puts in its answer must appear verbatim in earlier tool output. This blocks the LLM from writing Indian-language text itself, which the brief forbids, whether an injection causes it or not.

## Where the LLM is used, and the fallback

| Decision | What the LLM decides | Fallback if it fails |
|---|---|---|
| Plan | Steps and assumptions for the task | The first call is forced to `update_plan`; with no forcing support, `auto` |
| Feasibility | Unsupported language, missing file, capability gap | The tools also reject unsupported languages and missing files with clear errors |
| Tool choice and arguments | Which tool, on which file, which language and voice | The schemas validate; errors come back with hints |
| English writing | Summaries, lists, explanations | None needed; Indian-language text always comes from Indic-Translate |
| Judging results | Whether to retry or re-plan after a failed check | The finish gate in code |
| Stopping | When to call `finish` | Step and rupee budgets in code; after two turns without tool calls, the run stops |

Everything else is plain code: file inspection, language ID, chunking, joining audio, subtitle timing, cost accounting, verification scoring, sandboxing and the report.

## Trade-offs

- **No separate "detect language" tool.** Detection is built into transcription, OCR, translation and inspection results.
- **No standalone verification tool.** Verification runs inside the tools that create outputs, so a third-party client gets it by default.
- **Script-based language ID only.** It is free and correct for most scripts. Shared scripts are flagged, not guessed silently.
- **The agent talks to the server over MCP,** not by importing it. This costs one subprocess, but the graded server path is the one the agent uses every day.
- **Not done:** streamable HTTP transport, a web UI, resumable runs. The disk cache gives most of the benefit of resuming: a rerun repeats no paid call.
