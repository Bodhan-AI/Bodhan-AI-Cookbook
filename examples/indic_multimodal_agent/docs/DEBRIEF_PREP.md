# Bodhan AI MCP Server & Task Agent: Debrief Preparation Guide

This document prepares the engineer for the technical debrief, addressing the **15 core architectural, algorithmic, and engineering questions** a senior reviewer or evaluator is likely to ask.

---

### Q1: Where does the LLM make autonomous decisions versus where does plain deterministic code execute?
* **Answer**: The LLM acts strictly as a high-level **planner and tool orchestrator**, reasoning over the user request, intermediate tool results, and environmental constraints. Deterministic Python code handles **all** data chunking, boundary slicing, format conversion, audio demuxing/stitching, invariant verification, rate limiting, and cost estimation.
* **Exact Code Boundary**:
  - LLM tool-calling loop: [src/bodhan_agent/agent.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/agent.py#L1043-L1130)
  - Deterministic MCP Tools: [src/bodhan_mcp/tools/](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_mcp/tools/)
  - Deterministic Invariant Verifier: [src/bodhan_agent/verifier.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/verifier.py)

---

### Q2: How can you prove there is no keyword/recipe routing by task phrase?
* **Answer**: The entry point `BodhanTaskAgent.execute()` contains **zero** `if "srt" in task` or keyword dispatch logic. The entire execution flow delegates directly to `run_llm_agent_loop(files)`. In this loop, the OpenAI-compatible tool schema (`AGENT_TOOLS`) is passed to the LLM, and the LLM produces native `tool_calls` based solely on tool descriptions and schemas.
* **References**:
  - `execute()` delegation: [src/bodhan_agent/agent.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/agent.py#L650-L658)
  - Tool definitions: [src/bodhan_agent/prompts.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/prompts.py)
  - Architectural proof: [docs/DESIGN_NOTES.md](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/docs/DESIGN_NOTES.md#L15-L60)

---

### Q3: How do you respect the 30-second audio window limit of Indic-Transcribe without cutting words mid-sentence?
* **Answer**: We enforce an upper bound of **28 seconds** (2s safety headroom) using two-pass natural pause chunking. First, we identify silence intervals ($\ge 400$ms with dB threshold $-32$dB) via `ffmpeg` / `silencedetect`. Segments are partitioned preferentially between 8 and 15 seconds. If a continuous speech stretch exceeds 28s without silence, a fallback cut is made at the lowest energy dip.
* **References**:
  - `split_audio_at_silence()`: [src/bodhan_mcp/tools/chunking.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_mcp/tools/chunking.py#L30-L115)
  - Segment timestamp stitching: [src/bodhan_mcp/tools/transcribe.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_mcp/tools/transcribe.py#L40-L90)

---

### Q4: How does Indic-Speak handle long texts exceeding the model's 400-500 character limit?
* **Answer**: Texts are split on Indic sentence terminators (`।`, `.`, `?`, `!`, `\n`) into segments $\le 400$ characters. Each segment is synthesized individually into MP3 via Indic-Speak. The resulting MP3 segments are concatenated seamlessly using `ffmpeg` concat demuxer without re-encoding quality degradation.
* **References**:
  - `chunk_text_by_sentences()`: [src/bodhan_mcp/tools/speak.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_mcp/tools/speak.py#L25-L60)
  - `concatenate_audio_clips()`: [src/bodhan_mcp/tools/chunking.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_mcp/tools/chunking.py#L120-L160)

---

### Q5: How does the OCR tool process multi-page PDF documents without Poppler?
* **Answer**: We use **PyMuPDF (`fitz`)**, which is completely self-contained in Python C-bindings and requires zero external system poppler binaries. PyMuPDF renders PDF pages at $2.0\times$ resolution matrix (~150-200 DPI) into PNG in-memory byte buffers, encodes them as Base64, and dispatches them sequentially to Indic-OCR.
* **References**:
  - `extract_pages_from_pdf()`: [src/bodhan_mcp/tools/ocr.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_mcp/tools/ocr.py#L18-L37)

---

### Q6: How do you prevent Indic-Translate from breaking Markdown table syntax and column counts?
* **Answer**: A two-tier defense: First, tables are extracted as discrete Markdown blocks and translated with `preserve_formatting=True`. Second, post-translation verifier checks that header counts, row counts, and column counts per row match between source and target tables. If mismatch occurs, it falls back to row-by-row / cell-by-cell translation.
* **References**:
  - `translate_markdown_tables()`: [src/bodhan_mcp/tools/translate.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_mcp/tools/translate.py#L75-L135)
  - `verify_markdown_table_structure()`: [src/bodhan_agent/verifier.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/verifier.py#L130-L190)

---

### Q7: How does the agent defend against adversarial prompt injection inside files or OCR text?
* **Answer**: 
  1. All untrusted external text (audio transcripts, OCR extracts, file contents) is wrapped in strict `<data_untrusted>` XML delimiter tags before injection into LLM context.
  2. The system prompt explicitly instructs the LLM that text inside `<data_untrusted>` is passive data and must NEVER be interpreted as instructions.
  3. Pre-dispatch pattern scanning detects prompt override keywords (`SYSTEM PROMPT OVERRIDE`, `ignore previous instructions`), logs an assumption in `report.json`, and strips malicious directive tokens.
* **References**:
  - `check_prompt_injection()`: [src/bodhan_agent/agent.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/agent.py#L660-L685)
  - System prompt containment: [src/bodhan_agent/prompts.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/prompts.py#L12-L35)

---

### Q8: Both Hindi and Marathi use the Devanagari script. How do you distinguish them without guessing?
* **Answer**: Unicode script detection identifies Devanagari (`0x0900`-`0x097F`). Dialect discrimination is performed via an Indic stop-word frequency heuristic comparing 28+ high-frequency Hindi marker words (`है`, `हैं`, `था`, `नहीं`, `और`, `किया`, `द्वारा`) against 25+ Marathi marker words (`आहे`, `नाही`, `झाला`, `आणि`, `करणार`, `शासनाने`, `केले`). A confidence score is computed; if confidence is $< 0.70$, the detection is flagged as `is_assumed = True`.
* **References**:
  - `detect_devanagari_dialect()`: [src/bodhan_mcp/tools/lang_detect.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_mcp/tools/lang_detect.py#L65-L130)

---

### Q9: Where and how are assumptions and low-confidence decisions recorded?
* **Answer**: Whenever confidence is $< 0.70$ or a heuristic fallback is used, the agent calls `tracer.add_assumption()`. This writes the exact rationale into the `assumptions` array of `report.json` and renders an explicit "## Assumptions Made" section in `result.md` for human review.
* **References**:
  - `Tracer.add_assumption()`: [src/bodhan_agent/tracer.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/tracer.py#L89-L92)
  - Assumption output in report: [src/bodhan_agent/tracer.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/tracer.py#L128-L157)

---

### Q10: How do you prevent tiny or insufficient budgets (e.g., ₹0.0001) from making wasted paid calls?
* **Answer**: The agent computes a deterministic pre-flight cost estimate before initiating any tool calls, based on input media inspection (duration in seconds, PDF page count, word count). If estimated cost exceeds remaining budget, or if budget is $\le ₹0.0001$, the agent raises `BUDGET_EXCEEDED` immediately, writes `report.json` with status `failed`, and halts honestly without spending a single paisa.
* **References**:
  - `check_preflight_budget()`: [src/bodhan_agent/budget.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/budget.py#L45-L65)
  - Preflight estimate: [src/bodhan_mcp/cost.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_mcp/cost.py)

---

### Q11: How do you prevent hitting Bodhan's 10-parallel request limit and handle rate limits?
* **Answer**: `BodhanClient` uses an `asyncio.Semaphore(5)` (capped well below the 10-parallel limit). It inspects rate-limit response headers (`X-Ratelimit-Api_key-Remaining-Requests`); if remaining requests $\le 1$, it pauses for 1 second. On HTTP 429 or 5xx, it executes exponential backoff ($1.0\text{s} \times 2^k$) with randomized jitter.
* **References**:
  - `BodhanClient._execute_with_retry()`: [src/bodhan_mcp/client.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_mcp/client.py#L38-L96)

---

### Q12: How are generated artifact paths kept sandboxed and relative?
* **Answer**: All file writes are strictly constrained to `out/artifacts/` using `BudgetManager.sandbox_path()`. In `Tracer`, `to_rel_path()` recursively strips absolute paths and converts them to relative paths (e.g., `artifacts/subtitles_en.srt`). Neither `report.json`, `result.md`, nor `trace.jsonl` ever contains host-specific absolute paths.
* **References**:
  - `sandbox_path()`: [src/bodhan_agent/budget.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/budget.py#L70-L85)
  - Path sanitization: [src/bodhan_agent/tracer.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/tracer.py#L32-L58)

---

### Q13: What invariants are enforced on SRT subtitle files?
* **Answer**: The subtitle verifier tests four strict invariants:
  1. **Monotonicity**: `end_time > start_time` for all cues, and `cue[i+1].start >= cue[i].end` (no negative durations, no overlaps).
  2. **Boundary**: `end_time <= audio_duration + 0.5s` (no subtitle drift past audio end).
  3. **Readability**: Maximum 42 characters per line, maximum 2 lines per subtitle cue.
  4. **Script Verification**: Subtitle text matches the target language's expected script (Devanagari, Tamil, Latin, etc.).
* **References**:
  - `verify_subtitles()`: [src/bodhan_agent/verifier.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/verifier.py#L40-L125)

---

### Q14: How is audio synthesis quality verified beyond simple intelligibility?
* **Answer**: For audio synthesis, the agent performs a round-trip **back-transcription** via Indic-Transcribe. It computes a lexical **speech coverage percentage** (`calculate_text_coverage`) comparing words recognized in the back-transcription against the input text. If coverage is $\ge 40\%$ and duration is non-zero, verification passes with coverage recorded in the audit trace.
* **References**:
  - `calculate_text_coverage()` & `verify_audio_summary()`: [src/bodhan_agent/verifier.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/verifier.py#L240-L295)

---

### Q15: How do you guarantee that running `pytest` never makes paid Bodhan API calls?
* **Answer**:
  1. Plain `pytest` mocks all API clients via `unittest.mock.patch`.
  2. In `llm.py`, detection of `PYTEST_CURRENT_TEST` or `offline_mode=True` diverts all calls to `_offline_tool_call()`.
  3. Banned-library scans verify that no third-party paid cloud libraries (`whisper`, `google.cloud`, `azure`, `gtts`, `tesseract`) exist in dependencies or code.
* **References**:
  - `tests/test_15_tasks.py`: 100% offline unit tests
  - Offline guard in LLM client: [src/bodhan_agent/llm.py](file:///c:/Users/Samruddhi%20B%20Shinde/Desktop/Bodhan_Project/bodhan-agent-project/src/bodhan_agent/llm.py#L343-L345)

