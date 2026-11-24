# Design notes

## 1. Tool granularity: why seven tools

**One tool per capability, with that model's limits handled inside it, plus three free helpers.** I didn't go lower or higher, for these reasons:

- **Lower** (one tool per raw API call, e.g. "transcribe one ≤30 s WAV") would push chunking, silence handling, retries and stitching onto the planner. A third-party agent would get these wrong, and each chunk would cost an LLM turn. Model limits are plumbing, not decisions.
- **Higher** (e.g. `summarise_pdf_to_tamil_audio`) would hide every decision the planner should own (languages, voice, what to read aloud, whether a summary is wanted), and it would only fit tasks I predicted. The hidden test set punishes that.

So each tool is **complete for its model** (any length, any format, verified output) but **makes no task decisions**. Language, voice, gender, style, script, page ranges and verification stay explicit parameters, with sensible defaults.

| Tool | Limit handled inside | Verification inside |
|---|---|---|
| `transcribe_audio` | 30 s window: energy VAD finds pauses, regions are grouped into segments of about 15 s, a segment is never over 28 s (an over-long region is cut at its quietest frame), silence is never sent | repeated n-grams flagged; language-hint/script mismatch warns |
| `translate_text` | context window: paragraph-aligned chunks of about 1.5k tokens, run concurrently; pipe tables are translated as units; oversized paragraphs are split at sentences | length ratio, target script share, actually-translated, numbers multiset (across digit scripts), table shapes, headings. On failure: re-send as two halves; on a broken table: rebuild cell by cell |
| `synthesize_speech` | short inputs: sentence pieces of 220 characters or less (danda-aware), packed greedily; Markdown is read naturally | each piece is back-transcribed with Indic-Transcribe and scored by CER, with *repetition* / *omission* / *mismatch* classes (digit-heavy text gets a looser threshold, since numbers are spoken as words). Flagged pieces are re-synthesised as smaller sub-pieces and the better result is kept. Clipping and silence ratios are also checked |
| `ocr_document` | one image per request: PDFs are rasterised at 170 DPI and pages processed concurrently (60 max per call, with a `pages` selector); photos are EXIF-rotated and downscaled | empty pages, low-confidence blocks (conf < 0.6), per-page stats |

`inspect_media`, `make_subtitles` and `list_voices` are free, deterministic helpers. They exist because the planner needs facts before spending money, and because subtitle formatting is plain code, not a model task.

**Media plumbing.** Every tool reads and writes **files** and returns their paths. Long text comes back as a path plus a 500-character preview (full text inline only when it's under 4,000 characters). Transcription produces a `segments_file` (timed JSON). `translate_text` translates it segment by segment and returns a new one, and `make_subtitles` consumes that. So a 3-minute voice note becomes English subtitles in three calls, without the transcript ever needing to enter the LLM's context.

**Schemas for third parties.** Descriptions say what the tool is for, what it costs, how it chains ("pass `segments_file` to …"), and what to omit when unknown ("omit `language` to auto-detect"). Errors are `{"error": {code, message, hint, retryable, details}}` with stable codes (`unsupported_language`, `file_not_found`, `budget_exceeded`, `unknown_voice`, …). Hints are actionable ("Translate it to 'bn' with translate_text first, then pass the translation's output_file as text_file"). Unsupported languages, bad voices, wrong scripts and over-budget requests are all refused **before any paid request**. Tool annotations mark which tools are read-only, idempotent or open-world.

## 2. Where the LLM is used, and why

| Decision point | What the LLM decides | Fallback if it fails |
|---|---|---|
| `submit_plan` (forced first call) | What the deliverable is; whether the task is feasible; source and target languages; assumptions for ambiguity; step order | If the model doesn't call it, the first action is accepted and an implicit plan is recorded. If its args are invalid, the defaults are `feasible=true` and no assumptions |
| Each act turn | Which tools, which arguments (language, voice, gender, what to summarise), and what can run in parallel | Invalid JSON args or unknown tools produce a structured error fed back; two empty turns lead to a clean stop with status `incomplete` |
| Writing English | Summaries, explanations, lists of results (`write_text_file`, English only) | — |
| Reacting to verification | Retry differently (shorter text, another voice, no language hint) or report the problem | Tool-level recovery has already run; code re-verifies at finish anyway |
| `finish` | The English answer, which files are deliverables, and the claimed status | Code verifies everything, rejects once with reasons, then caps the status. With no finish (steps/time/LLM outage), code writes an honest report itself |

**What is plain code on purpose:** input discovery, media inspection, language names in the task, script-based language candidates, chunking and windowing, retries, caching, budget arithmetic, parallel budget splitting, duplicate suppression, artifact copying, verification, the final status and the report. These are deterministic, cheap and testable, and an LLM would only add variance.

**LLM portability.** Plain `chat.completions` with tools. Optional parameters the provider rejects (`temperature`, `max_tokens`, `tool_choice`, `parallel_tool_calls`) are dropped automatically. If native tool calling is rejected, the client switches to a JSON tool protocol, so the same agent loop runs either way. Rate limits, timeouts and 5xx errors are retried with backoff. Tested with: see the PR description.

## 3. Ground rules enforced in code

- **The LLM never writes Indian-language text.** `write_text_file`, the `text` argument of `translate_text` and `synthesize_speech`, and the final answer are all rejected if they contain any Indian-script character. Indian-language text moves between tools only as files produced by Bodhan models. If the LLM needs to understand Indian-language content, it translates it to English first.
- **No other speech, translation, TTS or OCR system** exists in the dependency tree. The only language identification is script detection.
- **Untrusted content.** Every tool result containing input-derived text is marked as data, and instruction-like text (English and Hindi patterns) adds a `security_warning` and is listed in `report.json` / `result.md`. Containment is what actually protects the run: there are no shell or network tools, reads are allowlisted to the inputs plus the run folder (enforced by both the agent and the server's `BODHAN_MCP_ALLOWED_DIRS`), and every paid call is budget-capped.

## 4. Verification

There are two layers. **Tool-level** checks run on every call (above) and drive local recovery. **Run-level** verification (`bodhan_agent/verify.py`) runs at `finish` and covers *every* audio file and translation generated in the run, not only the deliverables. Each deliverable must exist, be non-empty and decode (audio), parse with monotonic timestamps (SRT/VTT), and be English if the LLM wrote it. Audio is checked against a duration requested in the task ("two-minute", ±40%, as a warning). If verification fails, `finish` is rejected once with the reasons. The second time it's accepted with the status capped at `partial`, so the LLM can never talk its way to `completed`.

## 5. Budgets and cost control

- **Steps:** one step is one LLM turn (which may hold up to 8 parallel tool calls). At one step left, the LLM is told to finish; at zero, code stops and reports.
- **Rupees:** every paid call carries `max_cost_inr` = the remaining run budget, split evenly across calls issued in parallel. The server estimates conservatively and refuses **before** sending a request that could overspend, and its ledger reserves estimates for concurrent chunks inside one call. So `--max-cost-inr` is a hard ceiling, not a target.
- **Time:** per-tool-call timeout (`--call-timeout-s`), per-HTTP-request timeouts per model, and a wall-clock limit (`--max-time-s`).
- **No waste:** a content-addressed disk cache (SHA-256 of model + parameters + exact input bytes) means the same input is never sent to the same model twice, in one run or across runs. Identical tool calls within a run are memoised by the agent and cost no step budget beyond the turn. Silence is never sent for transcription. The prompt tells the LLM speech is about 30× pricier than translation, and to speak only what is asked.

## 6. Error recovery

| Failure | Handling |
|---|---|
| 429 / 5xx / timeout / connection error | Client retries with exponential backoff and jitter, honouring `Retry-After`, up to `BODHAN_MAX_RETRIES` |
| 401/403, 400/422 | Not retried; structured error with a hint (wrong key, bad input) |
| One OCR page or transcription window fails | Partial result plus a warning naming the page or segment; the call only fails if every part fails |
| Translation fails its checks | Re-sent as two smaller requests; a broken table is rebuilt cell by cell |
| Indic-Speak repeats or omits content | That piece is re-synthesised as smaller sub-pieces; the better-scoring audio is kept |
| Budget too small | Refused up front with the estimate; the agent reports `budget_exhausted` and what was done |
| LLM outage | Retries, then a clean stop with an honest report and the partial artifacts listed |
| Agent crash | Caught at the top level; `report.json` and `result.md` are still written |

## 7. What the live API taught us (16 Nov 2026)

Tested against the hosted API with per-model keys. Where it differed from the Cookbook docs, the code follows the live behaviour and keeps a fallback to the documented one:

- **Indic-Translate is served as a chat model.** It's `POST /v1/chat/completions` with `model: indic-translate` and `target_language` / `script` as body fields; the documented `POST /translate` returned 404. The client uses the chat route and falls back to `/translate` on a 404, remembering the choice. Truncation is signalled by `finish_reason: "length"`. `max_tokens` is rejected (decoding is pinned server-side), so we never send it.
- **The model drops Markdown table separator rows** (`|---|---|`). A shape check that ignores separators would have passed broken Markdown, so tables are now compared row by row, separators included, and rebuilt cell by cell when they differ. Seen live on the worksheet demo.
- **Indic-Speak returned HTTP 429 under parallel load.** The client's backoff recovered with no action needed.
- **Keys are strictly per model** (`/v1/models` lists only the key's own model), so a key/model mix-up fails fast with `auth_failed` and a hint naming the right variable.
- **Proper nouns can be swapped in translation.** In the Telugu clips demo, Indic-Transcribe got "Hyderabad" right but Indic-Translate rendered it as "New York". Our checks (script, length ratio, numbers, structure) can't see that, and the agent has no non-Bodhan way to check meaning, so it's reported, not hidden.
- **OpenRouter refuses requests without `max_tokens`** on low-credit keys (it reserves the model's full output window), so the agent caps LLM output at `LLM_MAX_TOKENS` (default 4096) and drops the cap automatically if a provider rejects it.

## 8. Untested or not done

- No live dubbing into video files. "Translate this video" gives a transcript and subtitles in the target language.
- Mid-run checkpoint/resume isn't implemented. Reruns and `--replay` are free thanks to the cache, which covers most of the value.
- No web UI. `trace.jsonl` is flushed per step, so `tail -f out/trace.jsonl` shows a run live.
