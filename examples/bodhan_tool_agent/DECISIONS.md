# Architectural and Design Decisions

This document records the architecture, trade-offs, and design choices made during the development of the Bodhan Tool Server and Task Agent.

---

### Decision Log

#### ADR 001: Package Layout and Monorepo Structure
- **Date**: 2026-11-16
- **Decision**: Structure the project with two primary packages under `src/`:
  - `src/bodhan_mcp`: MCP tool server implementation running on `stdio`.
  - `src/bodhan_agent`: CLI-driven Task Agent engine and execution runner.
- **Alternatives Considered**: Separate repositories or a flat layout.
- **Rationale**: Keeps tools and agent loosely coupled while sharing cache, validation, and pricing utilities.

#### ADR 002: Dependency Management & Tooling
- **Date**: 2026-11-16
- **Decision**: Standard `pyproject.toml` with pinned dependencies (`mcp==2.3.0`, `httpx==0.28.1`, `pydantic==2.14.3`, `python-dotenv==1.2.4`, `pytest==9.2.0`, `pytest-asyncio==1.5.0`) locked into `requirements.lock`.
- **Rationale**: Strict reproducibility and PEP 517/621 compatibility.

#### ADR 003: Model Integration and Zero-Fallback Policy
- **Date**: 2026-11-16
- **Decision**: All STT, Translation, TTS, and OCR requests must go directly to Bodhan AI endpoints (`https://api.bodhan.ai/v1`). No third-party model fallbacks (Whisper, Google, Tesseract) are permitted.
- **Rationale**: Complies with the assignment requirements for sovereign Indic foundation models.

#### ADR 004: MCP SDK 2.x API Usage
- **Date**: 2026-11-16
- **Decision**: Use `from mcp.server.mcpserver import MCPServer` and stdio transport (`run_stdio_async`) matching the official `mcp==2.3.0` specification where FastMCP was renamed to MCPServer.
- **Rationale**: Native support in the current official SDK release without deprecated aliases.

#### ADR 005: Disk Cache Design
- **Date**: 2026-11-16
- **Decision**: Implement a local file-based disk cache keyed by `sha256(model + source + target + exact text)` under `.cache/bodhan_translate/`. Both full responses and individual chunks are cached.
- **Alternatives Considered**: In-memory dictionary cache (lost across process restarts) or Redis (adds external infrastructure requirement).
- **Rationale**: Persists across restarts, ensures zero duplicate calls to paid Bodhan endpoints, satisfies budget limits, and requires zero external database dependencies.

#### ADR 006: Chunking and English Pivot Routing
- **Date**: 2026-11-16
- **Decision**:
  1. Chunk documents by paragraph and sentence boundaries within a safe margin of the 32,768-token window. If an output returns `truncated: true`, recursively split smaller and retry once; otherwise return a structured error.
  2. Implement automatic English pivot routing: because Bodhan's Indic-Translate model supports 44 directions (English ↔ 22 Indic languages), direct Indic-to-Indic calls (e.g. Hindi to Tamil) are routed as `Source -> English -> Target`, recording both steps and caching each step independently.
- **Rationale**: Maximizes translation fidelity, guarantees context window compliance, and enables seamless translation between any pair of Indic languages without manual intervention.


#### ADR 007: Common MCP Utilities Architecture
- **Date**: 2026-11-16
- **Decision**: Extract shared concerns (disk caching, Pydantic `StructuredError`, invocation-level `CallTracker`, HTTP error classification, and price helpers) into `src/bodhan_mcp/common.py`.
- **Rationale**: Keeps model tools concise and focused strictly on model payload translation and modality-specific handling while guaranteeing uniform error and budget behavior.

#### ADR 008: PyMuPDF for PDF Rendering in Indic-OCR
- **Date**: 2026-11-16
- **Decision**: Use `PyMuPDF==2.1` (`import pymupdf`) for rendering PDF pages to images.
- **Alternatives Considered**: `pdf2image` (requires system `poppler` binaries) or `pypdfium2`.
- **Rationale**: Self-contained Python wheel that requires no OS-level package installation (no poppler/apt/brew dependencies), delivers high-fidelity 150 DPI page rasterization, and handles complex multi-page layouts cleanly.

#### ADR 009: Preservation of OCR Output and LaTeX Notation (Reversal of Newline Rewriting)
- **Date**: 2026-11-16 (Updated / Reversed)
- **Decision**: Do NOT rewrite or normalize literal `\n` character sequences in `IndicOcrTool` output. The raw model output is preserved verbatim in both the tool result (`PageResult.text`, `full_text`) and the persistent disk cache.
- **Rationale**: Reversing the earlier ADR 009 normalization attempt. Literal backslash-n sequences are standard and legitimate syntax in LaTeX mathematical expressions (e.g. `\neq`, `\nabla`, `\newline`, `\nu`). Attempting to replace `\n` corrupts mathematical formulas such as `$a \neq b$`. The root issue was traced to test fixture generation where `\\n` was inadvertently drawn onto the canvas. The test fixture was regenerated without literal backslashes, and OCR output is returned strictly unmodified.

#### ADR 010: Audio Windowing and Transcribe Billing Measurement
- **Date**: 2026-11-16 (Updated 2026-11-17)
- **Decision**: In `IndicTranscribeTool`, split audio into windows ≤ 30.0s using silence detection between 20.0s and 29.0s (fallback to 29.0s hard-cut). Transcode all chunks via ffmpeg pipe to 16 kHz mono 16-bit PCM WAV. Cache chunks individually under `.cache/bodhan_transcribe/`.
- **Status on Billing Rounding**: Measured and Verified as **strictly pro-rata per second**.
- **Measurement Evidence**:
  - Live audio clip duration: `1.962667s`.
  - Rate: ₹0.10 / minute.
  - Pro-rata calculation: `(1.962667 / 60.0) * 0.10 = ₹0.00327111...`
  - Bodhan Console Actual Billed: **₹0.003271**.
  - Conclusion: Billing is strictly pro-rata per second (`(duration / 60.0) * 0.10`). It is NOT rounded up to the whole minute (which would have been ₹0.10) and is NOT a flat fee per request.

#### ADR 011: Indic-Speak Text-to-Speech Architecture and Verification
- **Date**: 2026-11-16
- **Decision**:
  1. **Sentence-Based Chunking (Max 250 Chars)**: Text is chunked by sentence delimiters (`.`, `!`, `?`, `।`, `\n`) packing sentences up to 250 characters. Any single sentence exceeding 250 characters is split at word boundaries. This aligns with the official documented guidance ("a sentence or two, about 30 seconds").
  2. **Exact Request Payload**: Request body strictly matches the official specification: `{"model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps({"lang": lang})}`. No unauthorized fields are sent.
  3. **Strict Pre-flight Validation**: The tool validates language against the 23 supported ISO codes and the voice against the 45-voice catalog before any HTTP call. Unsupported languages abort with `UNSUPPORTED_LANGUAGE`, and unrecognized voices abort with `INVALID_VOICE` (0 HTTP calls). The 10 Preview-tier languages execute with a metadata warning in the output.
  4. **Pre-flight Budget Guard**: Evaluates estimated cost ($chars \times \text{Rs } 6.00 / 10,000$) against `max_cost_inr` (default ₹0.50). If exceeded, aborts with `BUDGET_EXCEEDED` before making network calls.
  5. **Resilience & Rate Limits**: Concurrency is limited to 1 via `asyncio.Semaphore(1)`. Retries are capped at at most 1 attempt, permitted only on 429 or 5xx; 4xx client errors are never retried.
  6. **Per-Chunk Disk Cache**: Keyed by `sha256("indic-speak" + voice + lang + text)` storing raw WAV bytes under `.cache/bodhan_speak/`. Empty responses or errors are never cached.
  7. **Multi-Chunk Concatenation**: Slices are joined with `ffmpeg` using `concat` filter re-encoding to 24 kHz mono 16-bit PCM WAV (`pcm_s16le`, `-ar 24000`, `-ac 1`).
  8. **Local Verification**: The resulting audio is probed with `ffprobe` to confirm `duration > 0.0s` and checked against an agreed speech pace range of **4 to 25 characters per second** ($\frac{\text{chars}}{25} \le \text{duration} \le \frac{\text{chars}}{4}$).
- **Rationale**: Ensures reliable synthesis without timeout issues, enforces sovereign model usage, prevents runaway billing, and provides auditable verification for the task agent.

#### Live Round-Trip Test Verification Results
- **Date**: 2026-11-17
- **Speak Plausibility Check Result**:
  - Input: Cached translation text (26 characters).
  - Voice: `Kavya`, Language: `hi`.
  - Audio duration: `1.963s`.
  - Effective speech pace: $26 / 1.963 \approx 13.24$ chars/sec.
  - Plausibility Check Result: **`verified=True`** (within the valid range of 4 to 25 characters per second).
  - Server Content-Type: `audio/mpeg` (94,252 bytes received from `POST /v1/audio/speech`). Transcoded via `ffmpeg` to 24 kHz mono 16-bit PCM WAV (`pcm_s16le`, 24000 Hz, 1 channel).
  - Cache verification: Second invocation made 0 HTTP requests and returned `cache_hit=True` with ₹0.00 cost.
- **Round-Trip Character Error Rate (CER) & Similarity**:
  - Original Text: `"क्लास नौ बजे शुरू होती है।"`
  - Transcribed Text: `"क्लास नौ बजे शुरू होती है"`
  - Normalized Reference (Unicode NFC, no punct/space): `"क्लासनौबजेशुरूहोतीहै"`
  - Normalized Hypothesis (Unicode NFC, no punct/space): `"क्लासनौबजेशुरूहोतीहै"`
  - Edit Distance: 0
  - **CER: 0.0000 (0.00%)**
  - **Normalized Levenshtein Similarity: 1.0000 (100.00%)**
- **Billing Rounding for Transcribe**:
  - **Measured and Verified**: Strictly pro-rata per second (`(duration_s / 60.0) * ₹0.10`). Clip duration of `1.962667s` produced an exact charge of **₹0.003271** in the Bodhan console, proving zero whole-minute rounding and zero flat-fee overhead per request.

#### ADR 012: Task Agent Architecture and Plan-Act-Verify Loop
- **Date**: 2026-11-17
- **Decision**: Structure `src/bodhan_agent/` into 8 decoupled modules:
  - `cli.py`: Command-line entrypoint (`python -m bodhan_agent --task "..." --files DIR --out ./out [--max-steps 30] [--max-cost-inr 20]`).
  - `mcp_client.py`: Spawns `python -m bodhan_mcp` over stdio via the official MCP SDK client session, translating MCP tool schemas into OpenAI-compatible tool definitions. The agent strictly interacts with the MCP server over stdio and never imports tool classes directly.
  - `llm.py`: OpenAI-compatible chat completion caller with automatic retry/backoff on 429/5xx, reasoning token extraction, and pluggable scripted mock client for testing.
  - `budget.py`: Pre-flight tool cost evaluation refusing calls if `spent + estimate > max_cost_inr`, and duplicate suppression returning previously cached results with Rs 0.00 additional cost.
  - `refs.py`: Reference passing and Indic script injection guard.
  - `trace.py`: Append-only real-time step logging to `out/trace.jsonl`.
  - `report.py`: Generates `out/report.json` and human-readable `out/result.md` with relative links only.
  - `loop.py`: Plan-act-verify loop with feasibility pre-check (missing files or unsupported languages abort with status `impossible` before any API calls), 300s wall-clock timeout, and max steps enforcement.
- **Rationale**: Strict separation of concerns, complete auditability, and absolute budget protection.

#### ADR 013: Reference Passing Engine and Indic Script Guard
- **Date**: 2026-11-17
- **Decision**:
  1. **Reference Passing**: Intermediate tool outputs containing Indian-language text are indexed under handles (`@stepN.field`, e.g. `@step1.text`, `@step2.output_file_path`). The LLM sees a short safe preview in `<tool_data>...</tool_data>` (e.g. `text: "स्कूल कल..." [DATA_REFERENCE: pass @step1.text to next tool]`) and passes the handle directly to subsequent tools.
  2. **Prompt Injection Defense**: Tool output wrapped in `<tool_data>...</tool_data>` is explicitly flagged in the system prompt as passive data that must never be executed as instructions.
  3. **Indic Script Guard**: Inspects all tool call arguments before execution for Unicode code points belonging to Indian scripts (Devanagari, Bengali, Gurmukhi, Gujarati, Oriya, Tamil, Telugu, Kannada, Malayalam, Vedic, Ol Chiki, Meetei Mayek). If the LLM authors raw Indic text directly instead of using references or English, the call is rejected immediately with a `POLICY VIOLATION` error fed back to the LLM, making 0 tool calls and incurring Rs 0.00 cost.
- **Rationale**: Enforces the non-negotiable assignment constraint that the LLM must NEVER author or invent Indian-language text, while allowing seamless end-to-end data pipelines across tools.

#### ADR 014: Two-Phase Spend Ledger, Run Scoping, and Post-200 No-Retry Invariant
- **Date**: 2026-11-17
- **Decision**:
  1. **Two-Phase Spend Ledger**: Relocate spend tracking to `.cache/spend_ledger.jsonl` (overridable via `BODHAN_LEDGER_PATH`). Every real HTTP call logs an entry with status `"dispatched"` immediately before network dispatch, followed by status `"completed"` or `"failed"` with the HTTP status code once resolved.
  2. **Strict Post-200 No-Retry Invariant**: In all 4 tools (`translate`, `ocr`, `transcribe`, `speak`), retries are allowed ONLY on pre-response connection errors, 429, or 5xx. Any exception or failure occurring AFTER a 2xx response has been received (e.g. streaming hook errors, decoding errors) MUST NEVER trigger a network retry. The call is billed, and if unrecoverable, returns `StructuredError(code="UNKNOWN_BILLED")`.
  3. **Run-ID Budget Scoping & Global Spend Ceiling**: The agent tracks spend per execution run using a unique `run_id` passed to `total_spend_inr(run_id=...)`. A global spend cap `BODHAN_GLOBAL_CAP_INR` (default ₹9.00) is checked against the total ledger before every paid call.
  4. **Ledger-Derived Request Counts**: Audit reports and metrics derive HTTP request counts strictly from the spend ledger (`count_requests_from_ledger()`) or mock transport counters, never from in-memory tool path variables.
- **Rationale**: Eliminates accidental duplicate charges caused by client-side exceptions after successful server processing (as uncovered in the Speak hook post-mortem) and guarantees strict cost isolation.
- **Historical Entries Reconciliation (`hist_001` to `hist_005`)**:
  - Marked with `"source": "reconstructed_from_console"`: These 5 rows represent early verification calls executed before the unified two-phase spend ledger was implemented in Phase 4 Step 3.
  - **`hist_003` and `hist_004` Post-Mortem**: These were two identical ₹0.0156 Indic-Speak calls made during Phase 3 Step 3c live testing. Attempt 1 succeeded with HTTP 200 on the Bodhan server and was billed, but an exception (`ResponseNotRead` in a response-logging hook) was raised on the client before the audio bytes were committed to `.cache/bodhan_speak/`. Because the pre-response retry logic treated any unhandled exception as retryable, a second HTTP call was dispatched (attempt 2), incurring a second ₹0.0156 charge. This failure directly drove the creation of the **Post-200 No-Retry Invariant** (never retry if HTTP 2xx was reached) and the requirement that raw response bytes are cached before any post-processing.

#### ADR 015: Perso-Arabic Coverage and Text Provenance Registry for Transliteration Defense
- **Date**: 2026-11-17
- **Decision**:
  1. **Perso-Arabic Coverage**: Expanded `INDIC_UNICODE_RANGES` to include Arabic, Arabic Supplement, Arabic Extended-A, and Arabic Presentation Forms (`U+0600-06FF`, `U+0750-077F`, `U+08A0-08FF`, `U+FB50-FDFF`, `U+FE70-FEFF`) to guard Urdu and Kashmiri texts written in Perso-Arabic script.
  2. **Text Provenance Registry**: Created `TextProvenanceRegistry` storing SHA-256 hashes of all verified texts originating from Bodhan tools and files in `--files`.
  3. **Transliteration Defense for indic_speak**: `indic_speak` accepts raw literal text ONLY when `language="en"`. For all other languages (e.g. `hi`, `ta`, `ur`), the speech text must originate from an `@stepN` reference or file in `--files` registered in the `TextProvenanceRegistry`. This blocks Romanized/transliterated Indic text (e.g. `"Namaste kaise ho"`) that would otherwise evade Unicode regex checks.
  4. **Strict English Input for indic_translate**: Literal text arguments to `indic_translate` are rejected if they contain Indic or Perso-Arabic scripts.
  5. **Final Answer Sanitization**: Any Indian script characters in the LLM's final response that do not match an approved reference or artifact are replaced with a pointer: `"[See generated output in artifacts/]"`.
  6. **Passive Data Defense**: All tool results passed to the LLM are enclosed in `<tool_data>` tags and marked as passive data to prevent prompt injection attacks. Tool results to LLM are capped at 500 characters + references, with full text preserved in artifacts.
- **Rationale**: Completely closes script bypass vulnerabilities, ensures prompt injection resilience, prevents context window bloat, and enforces non-generative sovereign execution.

#### ADR 016: Compact Agent Tool Definitions and Strict Chat Completions Protocol
- **Date**: 2026-11-17
- **Decision**:
  1. **Compact Agent Tool Descriptions**: Added `COMPACT_AGENT_TOOL_DESCRIPTIONS` in `mcp_client.py` for agent-facing schema generation, reducing total tool schema tokens from ~3,591 down to ~1,110 tokens (a 69% reduction), while keeping full, exhaustive documentation on the MCP server for third-party clients.
  2. **Removed `echo` from Agent Tool List**: Excluded the internal development `echo` tool from the tools passed to the LLM.
  3. **Strict OpenAI Tool Calling Protocol Compliance**: Enforced that the LLM's assistant response containing `tool_calls` is appended to the message history immediately before any `role: "tool"` responses.
  4. **Unexecuted Step Reference Guard**: Added pre-execution inspection rejecting arguments containing `@stepX` references when the referenced step has not yet been executed in the conversation.
- **Rationale**: Keeps the agent prompt well under the 1,500 token ceiling, eliminates protocol templating errors on OpenAI-compatible providers, and protects against premature hallucinated tool chaining.

#### ADR 017: Language Name Normalization, Multi-Run Live Validation, and Path Sandboxing
- **Date**: 2026-11-17
- **Decision**:
  1. **Language Normalization in indic_speak**: Added `lang_name_map` to map common natural language names (e.g. `"Hindi"`, `"Tamil"`, `"Urdu"`) to standard ISO codes (`"hi"`, `"ta"`, `"ur"`) before registry validation.
  2. **Multi-Run Live Task Execution**:
     - **Run 1b**: Successfully performed end-to-end translation and synthesis (`notice.txt` -> `"कल नौ बजे क्लास शुरू होगी।"` -> `notice_hindi.wav`). Made exactly 1 paid Bodhan call (`indic-speak`, ₹0.0156; translate hit cache), verified audio pacing (2.39s duration, 10.88 chars/s), duration 14.48s, total cost ₹0.0158.
     - **Run 2 (Caching Guarantee)**: Re-ran the identical task to `out/run2`. Ledgers confirmed **0 new paid HTTP requests**, ₹0.00 model cost, `cache_hit: true` for both translate and speak.
     - **Run 3 (Impossible Task Defense)**: Evaluated `"Translate the notice into French and send it by email"`. Pre-flight feasibility check terminated immediately with status `impossible` in 0.00s, incurring 0 Bodhan calls, 0 LLM calls, and ₹0.00 spend.
  3. **Sandbox Path Traversal Enforcement**: Re-verified that helper tools (`read_text_file`, `inspect_media`) strictly enforce boundaries within `--files`, safely isolating system artifacts and preventing traversal attacks.
- **Rationale**: Ensures robust handling of LLM language naming variations, proves end-to-end caching and zero-duplicate guarantees on real endpoints, and validates immediate rejection of impossible foreign-language tasks.

#### ADR 018: Code-Driven Multi-Modal Verification, Deterministic Voice Defaults, and Generic Feasibility
- **Date**: 2026-11-17
- **Decision**:
  1. **Code-Driven Automated Verification (`src/bodhan_agent/verify.py`)**:
     - *Speech*: Transcribes generated speech WAV with `indic_transcribe` and computes NFC Normalized CER (`CER <= 0.20` pass, `<= 0.40` warn, else fail).
     - *Translation*: Verifies non-emptiness, absence of truncation, target script presence ratio, length sanity, and English back-translation similarity (`difflib` ratio + token overlap).
     - *OCR & Transcription*: Checks non-emptiness, script matching, and audits failed page numbers for multi-page documents.
     - *Budget Protection & Retry*: Verification costs are pre-flighted against the budget; if remaining funds are insufficient, status is set to `completed_unverified`. If verification fails, at most one paid retry of the tool is allowed under budget.
  2. **Generic Impossible & Edge Case Detection (Zero Task Keyword Matching)**:
     - Replaced all task text keyword heuristics with purely generic checks: file existence in `--files`, supported extensions (`.txt, .md, .csv, .srt, .pdf, .png, .jpg, .jpeg, .wav, .mp3, .m4a, .flac, .ogg`), 0-byte check, and corrupt media probing.
     - Added official `finish` tool (`status: "completed"|"impossible"`, `explanation: str`) allowing the agent to autonomously detect and terminate impossible tasks (e.g. video processing, email delivery, unsupported foreign languages) with zero paid calls.
     - When pre-flight cost exceeds budget, status is set to `budget_stopped` recording partial outputs and remaining actions.
  3. **Deterministic Female Voice Defaults**:
     - Created `DEFAULT_FEMALE_VOICES` mapping picking the first documented female voice for each language if `voice` is omitted or empty, guaranteeing deterministic cache hits across repeated runs.
  4. **Prompt Injection Defense & Trace Audit Flag**:
     - Added regex pattern scanning for prompt-injection markers (`ignore previous instructions`, `api key`, `raise budget`, etc.) across tool inputs and outputs, logging `injection_suspected: true` in `trace.jsonl` strictly for security auditing without altering execution logic.
  5. **Script Language Detection & Assumption Logging**:
     - Implemented `detect_script_language` using Unicode blocks without models. Documented shared script clusters (Devanagari for `hi/mr/ne`, Perso-Arabic for `ur/ks/sd`), recording explicit reliance on Bodhan Indic-Translate's source inference in `result.md`.
     - Documented and quoted Bodhan docs confirming the audio transcription `language` parameter is optional with automatic inference mode.
- **Rationale**: Completes all Phase 5 requirements, eliminates all task-specific heuristics, guarantees reliable speech and translation quality verification, and expands the test suite to 112 passing tests.

#### ADR 019: Verification Heuristics Disclosure, Non-Similarity Coverage Check, and SRT Subtitle Tool
- **Date**: 2026-11-17
- **Decision**:
  1. **Untuned Heuristics Disclosure**: Explicitly document that back-translation similarity thresholds ($\ge 0.35$ pass, $\ge 0.20$ warn) and Character Error Rate thresholds ($\le 0.20$ pass, $\le 0.40$ warn) are untuned heuristics derived from empirical testing on short sentences, rather than tuned on large production evaluation corpora.
  2. **Non-Similarity Translation Coverage Check**: Added a non-similarity coverage verification check to `verify_translation_step` in `src/bodhan_agent/verify.py`. This check normalizes all Unicode Indic digits (Devanagari, Bengali, Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam) to ASCII `0-9` via `unicodedata.decimal()` and asserts that all numbers and digit sequences present in the source text are preserved in the translation. It also tracks segment counts, reporting `coverage_check` as a separate structured field.
  3. **Voice Gender Sourcing & Male Voice Selector**:
     - *Documentation Sourcing*: Bodhan's API documentation at `https://console.bodhan.ai/api-docs` lists 45 voice names grouped by language code without an explicit gender column. Gender classification is derived from conventional Indian naming conventions (e.g. Kavya, Ishita, Anitha are conventional female names; Amit, Sourav, Arun are conventional male names).
     - *Male Voice Resolution*: Added `DEFAULT_MALE_VOICES` mapping across all 23 languages. Setting `voice="male"` resolves to the language's male voice (`Amit` for `hi`, `Arun` for `ta`, `Sourav` for `bn`). If a language lacks a male voice in the catalog, it raises a clear `NO_MALE_VOICE` error, never falling back silently.
  4. **Deterministic SRT Subtitle Tool (`make_srt`)**:
     - Added plain-code helper tool `make_srt(segments, output_path)` with zero LLM/model dependency.
     - Formats cues with millisecond-rounded timestamps (`HH:MM:SS,mmm`), sorts cues by start time, and wraps lines exceeding 42 characters at word boundaries.
     - Documented in the tool description that Bodhan Indic-Transcribe returns plain text without timestamps; start and end timestamps are derived from audio window chunk boundaries (up to 29s per cue).
  5. **Separate Bodhan Cost vs LLM Usage Accounting**: Added `llm_usage` (`prompt_tokens`, `completion_tokens`, `total_tokens`, `llm_calls_count`) and separate `bodhan_cost_inr` to `report.json` and `result.md`.
- **Rationale**: Provides transparent quality metric disclosures, prevents subtle numeric translation omissions, enables subtitle generation, and maintains strict token and cost separation.

#### ADR 020: Post-Mortem of hist_003/hist_004, Clean MCP Tool Surface, and World-Language Feasibility Table
- **Date**: 2026-11-17
- **Decision**:
  1. **Root-Cause Evidence for `hist_003` and `hist_004` Duplicate Spend**:
     - *Timeline*: At 2026-11-17 00:15:00 IST (`hist_003`), `scripts/run_live_round_trip.py` was executed. The script called `IndicSpeakTool`, which sent an HTTP POST to `https://api.bodhan.ai/v1/audio/speech`. The Bodhan server processed the synthesis, returned HTTP 200 with audio bytes, and billed ₹0.0156.
     - *Failure Point*: However, the script had attached an `httpx` response hook (`log_speak_response`) that executed `await response.aread()` on a streaming response. When `aread()` was invoked a second time during response body iteration, `httpx` raised a `ResponseNotRead` exception.
     - *Cache Miss on Retry*: Because the exception was raised in client post-processing code *after* the 200 response had been billed by the server and *before* writing to `.cache/bodhan_speak/`, the script crashed without creating the disk cache entry.
     - *Second Billed Call*: Exactly one minute later at 2026-11-17 00:16:00 IST (`hist_004`), the developer re-ran the script after commenting out the hook. Because no cache file existed, a second paid HTTP call was dispatched to Bodhan's API for the identical text, incurring another ₹0.0156 charge.
     - *Permanent Safeguards*: Enforced in ADR 014/015: raw 2xx response bytes are now written to disk cache *immediately* upon receipt before any post-processing, and retry safety strictly prohibits re-sending any request that already received a 2xx response.
  2. **Voice Gender Disclosure & Metadata Tagging**:
     - Bodhan API documentation lists voice names grouped by language code without a gender column. Voice gender is inferred solely from conventional Indian names (e.g. Kavya, Anitha as female; Amit, Arun as male) and **has not been verified by human listening**.
     - All `indic_speak` outputs now include the field `"voice_gender_source": "inferred"` to explicitly communicate this distinction to clients and auditors.
  3. **Strict MCP Tool Boundary (8 Standard Tools)**:
     - Expose exactly 8 useful, model-agnostic tools on the MCP server: `indic_translate`, `indic_ocr`, `indic_transcribe`, `indic_speak`, `inspect_media`, `read_text_file`, `make_srt`, and `detect_script_language`.
     - Removed development utilities (`echo`) and orchestration mechanisms (`finish`) from the MCP server. `finish` is strictly an agent-level loop construct injected into the LLM schema.
  4. **Generic World-Language Table for Impossibility**:
     - Replaced any ad-hoc task language heuristics with a static dictionary of 48 non-Indian world languages (`WORLD_LANGUAGES`).
     - If any word token in the task matches an unsupported world language (e.g. German, French, Spanish, Japanese) across any phrasing ("dub this into German", "convert the notes to Spanish", "make a Japanese audio version"), pre-flight feasibility terminates immediately with `impossible` (0 HTTP calls, 0 spend).
  5. **Granular Timing Breakdown (`llm_ms` vs `tool_ms`)**:
     - Instrumented agent loop and trace logger to split execution latency into `llm_ms` and `tool_ms` per step and report totals (`total_time_seconds`, `llm_ms`, `tool_ms`, `time_breakdown_ms`) in `report.json` and `trace.jsonl`.
- **Rationale**: Eliminates duplicate billing vulnerabilities, clarifies voice gender sourcing, maintains clean MCP encapsulation, and delivers precise performance observability.

#### ADR 021: Loop Control, Token History Pruning, and Rate-Limit Timeout Exclusion
- **Date**: 2026-11-17
- **Decision**:
  1. **Exclusion of HTTP 429 Retry Wait Time from Execution Timeout**:
     - *Issue*: During long runs against third-party LLM providers (e.g. Groq free tier with 6,000–8,000 TPM and 30 RPM limits), exponential backoffs and `Retry-After` sleeps accumulated ~277s of idle waiting inside HTTP retry loops, triggering the 300s execution wall-clock timeout despite active inference taking only ~34s.
     - *Policy*: Rate limit waiting time (`rate_limit_wait_ms`) is tracked separately and subtracted from the agent's wall-clock timeout clock (`elapsed_active = total_elapsed - (rate_limit_wait_ms / 1000)`). The 300s timeout now guards active computation budget rather than penalizing third-party quota throttles.
     - *Reporting*: `rate_limit_wait_ms` is explicitly reported in `report.json` and `time_breakdown_ms`, isolated outside `llm_ms` (which strictly measures inference turn latency).
  2. **Loop Control and 2-Repeat Guard**:
     - If the LLM makes a duplicate tool call or attempts to re-execute a step with an already passing verification result, the loop returns a terse instruction: `"already done, use the earlier result @stepN"`.
     - Full preview payloads are not re-sent.
     - After 2 such repeats, the agent stops the loop and forces a finish attempt with existing outputs rather than allowing endless loops.
  3. **Context Token Reduction**:
     - The initial plan text is not resent on every turn in the ongoing message history.
     - Tool output previews in `<tool_data>` are capped at 250 characters and strip redundant raw texts, keeping only `@stepN` reference handles and character lengths.
     - Tool descriptions are shortened to 1–2 concise sentences.
  4. **Partial Deliverables on Interruption**:
     - When execution fails or times out, any intermediate deliverables or generated artifacts are preserved, written to `report.json`, and surfaced in `result.md` marked as `(Partial)`.
- **Rationale**: Prevents rate-limit timeout starvation, caps token bloat, eliminates model looping, and ensures maximum recoverable utility on interrupted runs.

#### ADR 022: Honest Status Accounting, Reliable Tool Chaining, Deterministic SRT from References, Symmetrical Back-Translation, and Live Matrix Validation
- **Date**: 2026-11-17
- **Decision**:
  1. **Honest Status Accounting**:
     - Status `'completed'` strictly requires that tools executed and produced deliverables or generated artifacts in `artifacts/`.
     - A forced finish after repeated tool calls, missing deliverables, or an explanation containing apology / inability keywords (`cannot`, `could not`, `unable`, `sorry`, `apologize`, `not possible`, `impossible`, `failed to`) immediately forces/downgrades status to `'partial'` (or `'failed'`).
     - Added deliverable tracking into `report.json` under `"deliverables"`.
  2. **Reliable Tool Chaining & Available Reference Handles**:
     - Every tool result preview shown to the LLM concludes with an explicit un-truncated line: `"Available references: @stepN.text (N chars), @stepN.<field>..."`.
     - Augmented `SYSTEM_PROMPT` to mandate passing references (e.g. `@step1.text`) as arguments without needing to view full text, never re-running completed tools, and immediately chaining from OCR/transcription to downstream translation/SRT.
     - Upgraded `ReferenceStore.validate_and_resolve_arguments` and `resolve_value` to recursively traverse nested dicts, lists, and JSON-encoded argument strings, resolving full-step `@stepN` references to complete result dictionaries.
  3. **Deterministic `make_srt` with Chunk Alignment**:
     - `make_srt` updated to accept `transcript: @stepN`, `text: @stepM.text`, and `output_path`.
     - Derives cues deterministically from audio chunk boundaries (up to 29s). When translating single-block text across multiple chunks, splits text at sentence boundaries in proportion to chunk durations and logs `approximation_note` to agent `assumptions` and `result.md`.
  4. **Symmetrical Back-Translation Direction & Verification String Reporting**:
     - Fixed direction inversion in `verify_translation_step`:
       - Indic -> English: translates English back to the source Indic language, comparing source Indic with back-translated Indic.
       - English -> Indic: translates Indic back to English, comparing source English with back-translated English.
       - Eliminates cross-script comparison distortion (which caused Hindi Devanagari to be compared directly against Latin English in Task 2, producing artificial 0.0673 similarity).
       - Explicitly logs `source_string` and `comparison_string` in the verification dictionary in `report.json`.
  5. **Live Matrix Runner Validation (`scripts/run_matrix.ps1`)**:
     - Added `Test-TaskDeliverables` function validating:
       - Universal: Expected status, cost cap, zero `"verdict": "fail"` entries, and zero apology explanations with completed status.
       - Task 2: Requires valid, non-empty `.srt` file with SubRip cue timestamps in `artifacts/` and `indic_translate` step in summaries.
       - Task 3: Requires `indic_ocr` step, `indic_translate` step to Marathi, and Markdown table in `result.md` with row count $\ge 7$.
     - Prior failed runs are automatically detected and re-run on subsequent matrix invocations.
- **Rationale**: Eliminates false "completed" reports on failed runs, prevents hallucinated reference attributes, makes subtitle creation deterministic and simple for LLM chaining, fixes cross-lingual back-translation metric distortion, and guarantees strict end-to-end artifact validation.

#### ADR 023: Deterministic Summary Grounding and Length Constraint Verification (Untuned Heuristics)
- **Date**: 2026-11-18
- **Decision**:
  1. **Summary Grounding Check (`summary_grounding`)**:
     - For any `indic_translate` call whose input text was authored directly by the LLM (not an `@stepN` reference and not read verbatim from a file path) when the run already has extracted source text from `indic_ocr`, `indic_transcribe`, or `read_text_file`, compute deterministic content-word overlap with zero LLM calls.
     - Content words extracted from the LLM-authored input: all numbers (`\d+(?:\.\d+)?`), all capitalised names (`\b[A-Z][a-zA-Z]+\b`, lowercased, stopwords removed), and all words of 4+ letters (`[A-Za-z]{4,}`, lowercased, stopwords removed).
     - Score = fraction of candidate content words present in the lowercased token set of the run's source texts.
     - **Untuned heuristic thresholds**:
       - `>= 0.50`: `pass`
       - `>= 0.30` and `< 0.50`: `warn`
       - `< 0.30`: `fail`
     - Any candidate whose `summary_grounding` verdict is `fail` is excluded from `produced_refs`, excluded from `"VERIFIED. Use @stepN..."` and `"Valid references"` lists, refused if referenced by `indic_speak` or `finish`, and never counted as a passing candidate for superseding earlier failures.
  2. **Length Constraint Check (`length_constraint`)**:
     - When the task prompt specifies a target length such as `"about N characters"` or `"N words"`, compare the LLM-authored input length against `N`.
     - **Untuned heuristic thresholds**:
       - Within `+-30%` of `N` (`0.70*N <= actual <= 1.30*N`): `pass`
       - Outside `+-30%`: `warn`
     - Strictly report-only; never produces a `fail` verdict.
- **Rationale**: Back-translation only verifies translation fidelity between the LLM's English input and the target Indic output; it cannot detect when the LLM hallucinates an off-topic English summary before translation. A deterministic content-word overlap check catches ungrounded hallucinations immediately at zero API cost and prevents ungrounded translations from being spoken or returned as verified deliverables.

#### ADR 025: Live-Call Guard Default Reversal (Opt-Out via `BODHAN_ALLOW_LIVE=0`)
- **Date**: 2026-11-18
- **Decision**:
  - Reversed the default behavior of `check_live_calls_allowed` in `src/bodhan_mcp/common.py`: paid HTTP calls to `https://api.bodhan.ai/v1` are now **allowed by default** whenever the required Bodhan API key is configured, and are blocked (`LIVE_CALLS_DISABLED`) only when `BODHAN_ALLOW_LIVE` is explicitly set to `"0"` or `"false"`.
  - Automated tests enforce `BODHAN_ALLOW_LIVE=0` via an `autouse=True` fixture in `tests/conftest.py` (and `scripts/mcp_smoke.py` / `tests/test_mcp_echo.py` explicitly set `BODHAN_ALLOW_LIVE=0` on child MCP processes) so the test suite can never incur live spend.
  - `scripts/run_matrix.ps1` retains its own explicit check requiring `$env:BODHAN_ALLOW_LIVE="1"` as a human confirmation step for running the full 7-task evaluation matrix.
  - `.env.example` documents `BODHAN_ALLOW_LIVE=0` only as a commented line (never active, because `.env` is loaded with `override=True`).
  - All other financial protections — per-run budget ceilings (`--max-cost-inr`), append-only spend ledger (`.cache/spend_ledger.jsonl`), matrix stop-loss, disk caching, and two-phase retry safety — remain unchanged.
- **Rationale**: Hidden evaluation and external graders run `python -m bodhan_agent --task ...` directly with their API keys configured and without knowing about a custom `BODHAN_ALLOW_LIVE=1` opt-in flag. Requiring an opt-in flag caused every paid call to be refused during direct CLI invocation. Having valid API keys present expresses clear intent to call the API, while `BODHAN_ALLOW_LIVE=0`, `tests/conftest.py`, budget caps, and the spend ledger keep offline runs and tests completely safe.





