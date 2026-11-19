# Design notes

## 1. Tool list and granularity

| Tool | Bodhan model | Cost | Why it exists |
|---|---|---|---|
| `inspect_files` | none | free | Lets any planner see types, durations, pages, scripts and estimated cost before spending. |
| `list_capabilities` | none | free | Languages per model, voices, styles, prices. Makes "is French supported?" a lookup, not a guess. |
| `transcribe_audio` | Indic-Transcribe | ₹0.10/min | Speech → text + timed segments, any length, audio or video. |
| `translate_text` | Indic-Translate | ₹0.20/10k out tokens | Text/Markdown/.docx/segments/SRT → target language, structure kept. |
| `synthesize_speech` | Indic-Speak (+ Transcribe to verify) | ₹6/10k chars | Text of any length → one verified audio file. |
| `ocr_document` | Indic-OCR | ₹0.20/page | Images and PDFs of any length → Markdown with tables. |
| `make_subtitles` | none | free | Segments → SRT/VTT cues (timing is plain code, not a model job). |
| `read_text` | none | free | Paged reading of tool outputs, marked as untrusted. |

**Why this granularity.** It is one tool per *capability*, with the model's limits hidden inside, plus small free helpers.

- **Not raw one-call-per-model tools.** A raw wrapper would push 30-second chunking, sentence splitting, page rendering and truncation handling onto the planner. An LLM does that badly and expensively, and a third-party client would have to re-implement it.
- **Not task-level tools** (`summarize_pdf_to_tamil_audio`). They hide control, they can't be recombined for unseen tasks, and they would amount to special-casing the demo tasks.
- **The middle layer gives** a small surface (8 tools). Every unseen task we tried is a 1–5 step chain of these tools. Each tool is independently testable and usable from any MCP client.

Free helpers exist where the work is deterministic: subtitle timing, reading files, inspecting media. An LLM should not spend tokens on these, and a model should not be charged for them.

## 2. How each tool handles model limits

| Limit | Where | How |
|---|---|---|
| Audio ≤ 30 s per request | `transcribe_audio` | ffmpeg decodes anything (incl. MP4 audio tracks) to 16 kHz mono. Energy-based VAD cuts at the pause closest to a target length: 20 s, or 5 s for subtitles. If there is no pause, it cuts at the quietest frame before 28 s. Chunks without speech are skipped, which saves money. Chunks run concurrently and are reassembled with timestamps. |
| Short TTS inputs | `synthesize_speech` | Text is split at sentence marks (`। ॥ . ! ? ۔ ؟`), then clauses, then words, into ≤ 240-character chunks of at most 2 sentences. Clips are joined with 250 ms pauses and encoded to MP3/WAV/OGG with an "AI-generated" metadata tag. |
| Translation context window | `translate_text` | Markdown is parsed into literal structure (pipes, `#`, list markers, code, page comments) and translatable spans. Each table cell is translated on its own, so tables survive by construction. Spans are split at sentence boundaries to ≤ 1,200 characters. If `truncated: true` comes back, that piece is re-sent as two halves (up to 3 levels). |
| Multi-page documents | `ocr_document` | PDFs are rendered at 200 dpi with pypdfium2 (behind a lock, since pdfium is not thread-safe). Images are EXIF-rotated, capped at 2,800 px and sent as PNG, or JPEG if over 4 MB. Near-blank pages are skipped for free. A 502 (block over `max_tokens`) is retried once with `max_tokens=4096`. Other pages are still returned and `complete=false` is reported. |
| Rate limits | `client.py` | A global semaphore (`BODHAN_MAX_CONCURRENCY`, default 4) limits requests. 429 and 5xx get backoff with jitter, honouring `Retry-After`. |

## 3. Media plumbing (keeping the LLM's context small)

- Tools take **paths** and write **files**. Results carry the path, a ~300-character preview, stats and checks. No base64 ever crosses MCP.
- The agent passes `output_dir=out/work`, so every intermediate file is inspectable after the run. Only the files listed in `finish`, which must have been produced by a tool in this run, are copied to `out/artifacts/`.
- The LLM reads full text only when it must write English from it (a summary, say). It does that with `read_text` on the English translation.

## 4. Verification

| Output | Check | Who |
|---|---|---|
| Generated audio | Each chunk is back-transcribed with Indic-Transcribe in the target language and compared on normalised characters: similarity ≥ 0.55, length ratio 0.5–1.8, no new 3× repetition. A failing chunk is re-synthesised as two smaller inputs and re-checked. | tool |
| Translation | Every translatable span has non-empty, non-truncated output (`coverage_ok`), at least 60% of output letters are in the target script (`target_script_share`), table shape and segment timings match the source (`structure_preserved`), and length ratios look sane. | tool |
| Transcripts / OCR | All chunks/pages processed (`complete`), repetition warnings, script detection. | tool |
| Delivery gate | An artifact is "verified" only if its producing tool's checks passed. `success` is lowered to `partial` otherwise, and `impossible`/`failed` runs never deliver files. | agent (code) |

Thresholds are deliberately lenient. ASR normalises numbers and spelling, so a perfect clip rarely scores 1.0. Raw per-chunk scores are kept in the result so a reviewer can judge for themselves.

## 5. Budgets

- **Money.** Every paid tool takes `max_cost_inr` and refuses work that could exceed it *before* spending (`budget_exceeded`, with the estimate in `details`). The agent always passes the remaining budget, split evenly across paid calls issued in the same turn, so parallel calls cannot overspend together. Real cost comes back in `usage.cost_inr`. If a tool fails after spending, the amount is reported in `error.details.spent_inr`, so accounting stays exact.
- **Steps.** `--max-steps` caps tool calls. **Time:** `--max-wall-seconds` and the per-call `--tool-timeout`. **LLM calls:** capped at 2×steps+6.
- **When any budget runs out,** the agent offers the LLM only `finish` for one last turn ("report honestly what was done"). If even that fails, code writes the report.
- **No waste.** A content-hash disk cache serves identical requests, within a run and across runs. In-flight de-duplication covers concurrent identical requests. The agent also returns earlier results for repeated identical tool calls at ₹0. Retries after a content failure always use a different input.

## 6. Where the LLM is used, and the fallback for each

| Decision | What the LLM decides | Fallback if it fails |
|---|---|---|
| Plan | Interpretation, deliverables, assumptions, feasibility, tool chain | If a provider rejects a named `tool_choice`, retry with `auto` plus an instruction, then parse JSON from the text. If there is still no plan, continue to the act loop with a note. |
| Tool choice and arguments | Which tool next, which language/voice/format | Schema validation by MCP and guards in code send back structured errors; the LLM corrects itself. |
| Writing | English summaries and explanations only | Code rejects Indic script in inline `text` and in the final answer, unless the user typed that text in the task. |
| Judging results | Reading checks and warnings, deciding to retry, re-plan or stop | Code-level verification gate and status downgrade. |
| Final answer | `finish(status, answer_en, artifacts, assumptions)` | Validation; a second bad attempt is sanitised. If the LLM stops or errors, code finalises as `failed`/`partial`, with nothing fabricated. |

Everything else is plain code: file inspection, script-based language ID, chunking, subtitle timing, cost accounting, caching, budgets, reports.

## 7. Unknowns, ambiguity and untrusted content

- **Unknown language.** Transcription runs with `language=auto`. The transcript's Unicode script gives `likely_languages`, which is decisive for Tamil, Telugu, Kannada, Malayalam, Gujarati, Gurmukhi, Odia, Ol Chiki and Meetei Mayek, and a candidate set for Devanagari, Bengali and Perso-Arabic scripts. Indic-Translate detects its source itself.
- **Ambiguity.** The run is non-interactive. The planner records assumptions, for example "video → subtitles plus transcript, because no output format was named". They appear in `result.md` and `report.json`.
- **Prompt injection.** File-derived strings (`preview`, `content`) reach the LLM wrapped in `<untrusted_file_content>`. When they look like instructions, a `security_note` is added and the run records a `security_flag`. The defence is mostly structural, so it does not depend on the LLM resisting:
  - the agent has no shell or network tool;
  - output locations and budgets are injected by code and hidden from the LLM;
  - Indian-language text cannot be written by the LLM;
  - artifacts must come from tools.

## 8. Impossible tasks

Unsupported languages are refused by the tools (`unsupported_language`, with the supported list) and are visible to the planner up front via `list_capabilities`. Missing files are reported in pre-flight. Over-budget work is refused before spending. In every case the agent finishes with `impossible`/`failed`, a plain-English reason, and no artifacts. The code enforces that last part.

## 8b. Lessons from the live API runs (18–19 Nov 2026)

- **Translation route.** The documented `POST /translate` returned 404. `POST /v1/chat/completions` with `model=indic-translate` works, so the client uses that and falls back to the documented route.
- **Strict tool-call validation (Groq).** The model sent `null` for an optional plan field, and the provider rejected the whole call. Optional fields now accept `null`, provider validation errors are retried, and a failed plan step no longer ends the run.
- **Bodhan rate limits.** Twenty short subtitle chunks at concurrency 4 hit 429s. Defaults are now concurrency 2 and 6 retries with backoff up to 30 s. An incomplete result can be re-requested: the cache fills in the chunks that succeeded, so only the failed ones are paid for.
- **LLM quotas.** Groq's free tier allows 8k tokens per minute and 200k per day. Older context is compacted to stay under the per-minute limit. A wait longer than 90 s (a daily quota) fails fast and honestly instead of burning the time budget.

## 9. Trade-offs and known limitations

- **VAD is energy-based.** It is simple, dependency-free and deterministic. Speech over loud music or noise may not split at natural pauses; chunks are then cut at the quietest point before 28 s.
- **Subtitle timing** within a segment is proportional to text length, because Indic-Transcribe returns no word timestamps. Segments are kept short (2–12 s) to limit the error.
- **Script ≠ language** for shared scripts. "Is this Hindi or Marathi?" cannot be settled by script alone, and the result says so in `likely_languages`.
- **TTS verification thresholds** were set conservatively without a large calibration set. Expect occasional false alarms on number-heavy text; scores are reported, not hidden.
- **The translation route differs from the docs.** On 18 Nov 2026 the documented `POST /translate` returned 404, while `POST /v1/chat/completions` with `model=indic-translate` worked. The client tries the chat form first and falls back to the documented route (`BODHAN_TRANSLATE_PATH` can force one).
- **Digital PDFs are OCR'd too.** That is simpler and safer under the "all text extraction through Indic-OCR" rule, at ₹0.20/page.
- **LLM cost** is reported in tokens, not rupees (the provider is unknown).
- **The HTTP transport** resolves file paths on the server's machine; it is for co-located clients.
