# Design Notes — Indic Task Compiler

## 1. Core Architectural Thesis

Most multi-step agents run an unconstrained ReAct loop where untrusted OCR/transcript text is appended straight into the planner's chat history, and impossible tasks are only discovered after spending API credit.

This solution enforces two architectural guarantees in code:

1. **CaMeL Privileged / Quarantined LLM Split (`src/bodhan_agent/store.py`, `planner.py`, `quarantined.py`)**:
   - The **Privileged Planner (`P-LLM`)** sees *only* the user's `--task`, free `FileFacts` metadata, typed `ProbeFacts` (ISO codes, confidence floats, ambiguity booleans), and `$step.field` artifact handles.
   - The MCP server (`bodhan_mcp`) returns a bounded `preview` (<=300 chars, `untrusted: true`) for third-party MCP clients that legitimately need to inspect snippets. Our agent's MCP client bridge (`mcp_bridge.py`) **strips every preview/content field at the boundary** before anything can enter `P-LLM` or `trace.jsonl`.
   - Any operation that must read untrusted text (`q_summarize_en`, `q_classify`, `q_extract_number`) executes in a **Quarantined LLM (`Q-LLM`)** with zero tools, a data-only system prompt, strict English-only enforcement on `q_summarize_en`, and code-validated enum/numeric outputs. Free-text English summaries flow only into a new artifact handle for downstream Bodhan tools — never back into `P-LLM`.
2. **Pre-Spend Feasibility Gate & Plan Compiler (`gate.py`, `compiler.py`)**:
   - Before calling any paid Bodhan model, deterministic code checks language support against Bodhan's vendored capability matrix (`bodhan_api_rules.json`), verifies input file existence and kinds, and compares the provable minimum cost floor against `--max-cost-inr`. Provably impossible tasks exit at **₹0** with an honest reason and zero artifacts.
   - Epoch B emits a flat step list (no custom DSL to confuse weaker evaluator LLMs). Deterministic code expands `foreach` filters, validates every tool name and argument against the live MCP `list_tools` JSON Schemas, enforces input/output/language scope locks, blocks any planner-authored Indic script literals, auto-inserts mandatory `verify_speech`, and infers the dependency DAG into parallel execution waves.

## 2. Tool Granularity Rationale

We chose **8 capability-level tools** rather than raw 1:1 HTTP wrappers or opaque single-task macros:

- **Why not raw 1:1 wrappers?** Raw wrappers push <=30 s audio chunking, PDF rasterization, Markdown table preservation, `truncated: true` splitting, <=250-char TTS splitting, and 502 OCR recovery onto the planner — wasting LLM steps and causing brittle failures with third-party MCP clients.
- **Why not mega-tools (e.g. `pdf_to_tamil_audio`)?** Mega-tools hide control, fail to generalize to unseen hidden tasks, and break composability for third-party MCP clients.
- **Why these 8 tools?** Each tool encapsulates *one* clean capability with all model-limit mechanics handled internally:
  1. `inspect_files` (free) — magic-byte + `ffprobe` + `pypdfium2` metadata and cost estimates.
  2. `detect_language` — Unicode script histogram + shared-script stopword disambiguation + <=10 s audio probe.
  3. `transcribe_audio` — ffmpeg 16 kHz mono WAV normalization (supports MP4/M4A/WebM), `silencedetect` <=28 s windowing, pure-silence dropping, timed segment outputs.
  4. `translate_text` — atomic Markdown table/code/LaTeX block chunking, recursive halving on `truncated: true` (duplicating table headers), chunk/table/digit coverage report.
  5. `synthesize_speech` — script-aware sentence chunking (<=250 chars), `ffmpeg` concat + `loudnorm`, MP3/WAV encoding with `"AI-generated (Bodhan Indic-Speak)"` ID3 tags, per-chunk manifest.
  6. `verify_speech` — chunk-by-chunk back-transcription via `indic-transcribe`, normalized CER, n-gram repetition loop detection, duration/char-rate anomaly detection.
  7. `ocr_document` — `pypdfium2` PDF->PNG rasterization (no poppler), reading-order Markdown, 502 recovery cascade (`max_tokens=8192` -> 65% downscale -> vertical top/bottom split), low-confidence block reporting.
  8. `write_subtitles` (free) — deterministic SRT/VTT formatter with proportional cue splitting and monotonicity validation.

## 3. Indic Provenance Guard

Per assignment rules, the LLM must never write or rewrite Indian-language text. We enforce this in **three layers**:
1. **Plan Compiler**: rejects any step argument containing Indic-script characters authored directly by `P-LLM`.
2. **Quarantined Runner**: `q_summarize_en` rejects Indic-script input (requiring `translate_text -> en` first) and rejects any Indic-script output from the LLM.
3. **ArtifactStore (`assert_indic_provenance`)**: before publishing any deliverable into `out/artifacts/`, verifies that every non-empty Indic-script line in the file is byte-identical to a recorded Bodhan tool output (or original user input file) and traces to a trusted provenance source.

## 4. Verification, Recovery, and Budgets

- **Verification Contracts (`contracts.py`)**: Every deliverable is checked in code before the run finishes: non-empty file, Indic provenance, audio playability + ±20% duration tolerance (when duration requested) + `verify_speech` CER/repetition/rate verdicts, target script ratio >= 0.8 (native script), translation chunk/table/digit preservation, and SRT monotonicity.
- **Bounded Re-planning (Epoch C)**: Failing deliverables trigger at most 2 targeted subgraph re-plans using only structured failure metadata (reusing valid upstream handles).
- **Error Recovery (`client.py`, `tools.py`)**:
  - HTTP `429` / `5xx` / timeouts: exponential backoff + jitter (honoring `Retry-After`), bounded by `asyncio.Semaphore(4)`.
  - HTTP `401`: retried once *only* if the same key already succeeded earlier in the run; otherwise fails immediately as `auth_error`.
  - HTTP `4xx` validation errors: non-retryable structured error (`{ok:false, error:{code, message, retryable, hint}}`).
  - OCR `502` overflow: automatic cascade (`max_tokens=8192` -> downscale -> vertical split).
  - Translate `truncated: true`: automatic recursive halving (preserving table headers).
- **Cost & Step Control (`budget.py`, `cache.py`)**:
  - Feasibility gate rejects provably unaffordable tasks at ₹0 (using only a provable *floor*, never falsely refusing doable tasks).
  - Before every paid call at runtime, `BudgetTracker.reserve_cost` checks actual resolved input size against remaining `--max-cost-inr`; exhaustion stops cleanly with `status="partial"` and all already-produced artifacts preserved.
  - Content-addressed SHA-256 disk cache (`~/.cache/bodhan_mcp`) guarantees zero duplicate `(model, exact input)` calls within a run and ₹0 reruns.

## 5. Known Trade-offs and Limitations

- **Audio LID on shared scripts**: Devanagari (`hi`/`mr`/`ne`/`sa`/`mai`) and Bengali (`bn`/`as`) share Unicode blocks; disambiguation uses stopword hit-rates + distinctive glyphs (`ळ`, `ৰ`, `ৱ`) on a <=10 s probe. Short clips can remain ambiguous and are explicitly logged under `assumptions` in `result.md`.
- **SRT timestamps**: `indic-transcribe` returns text without word timestamps, so subtitle cues derive from silence-aware audio windows split proportionally by character count.
- **No framework / no live SSE UI**: Intentionally built with stdlib + official `mcp` SDK + `openai` + `httpx` + `pydantic` + `pypdfium2` + `Pillow` + `ffmpeg`. Visual trace inspection uses a self-contained static `trace.html` rendered from `trace.jsonl`.
