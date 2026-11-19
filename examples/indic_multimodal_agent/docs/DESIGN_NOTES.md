# Bodhan MCP & Task Agent: Architecture & Design Notes

## 1. System Overview

The system is split into two cleanly separated tiers:
1. **`src/bodhan_mcp` (Tool Server Layer)**:
   - Standalone MCP server exposing Bodhan AI Indic models over Model Context Protocol (stdio).
   - Low-level helpers for media inspection, audio pause chunking, speech stitching, Unicode language/script detection, and official voice catalogs.
   - Robust input validation returning structured error JSON payloads (`error_code`, `message`, `retryable`), never crashing on malformed inputs.
2. **`src/bodhan_agent` (Autonomous Task Agent Layer)**:
   - Orchestrates multi-step multimodal Indic workflows.
   - Implements the autonomous cycle: **Plan $\to$ Preflight $\to$ Act $\to$ Verify $\to$ Replan/Retry $\to$ Finalize**.
   - Strict budget tracking in Indian Rupees (INR) and step limits.
   - Comprehensive artifact emission with relative paths and complete auditable traces (`trace.jsonl`, `report.json`, `result.md`).

---

## 2. LLM vs. Plain Code Decision Boundary

A core principle of this project is to avoid paying LLM token costs or introducing non-deterministic failures where reliable plain code algorithms exist:

| Capability / Decision | Handled By | Rationale |
| :--- | :--- | :--- |
| **Media Inspection & Duration** | Plain Python (`soundfile`, `PyMuPDF`) | Fast, local, free, zero network dependency. |
| **Audio Silence & Pause Chunking** | Plain Python (`numpy` RMS energy) | Eliminates mid-sentence cuts; guarantees $<28$s windows. |
| **Pre-flight Cost Estimation** | Plain Python math | Halts micro-budgets honestly before any network calls. |
| **Script & Stop-word Detection** | Plain Python regex & Unicode ranges | Deterministic Unicode script blocks + Devanagari stop-words. |
| **Subtitle Numbering & Alignment** | Plain Python formatting | Verifies line counts; splits cues $\le 2$ lines, $\le 42$ chars. |
| **Verification Checks** | Plain Python rules | Monotonic timestamps, zero overlaps, NFC normalization, numbers preserved. |
| **Disk Response Caching** | Plain Python SHA-256 hashed JSON | Prevents redundant billing on repeated requests. |
| **Speech-to-Text** | Bodhan `Indic-Transcribe` | High-accuracy Indic speech recognition. |
| **Machine Translation** | Bodhan `Indic-Translate` | Context-aware translation across 22 scheduled Indian languages. |
| **Text-to-Speech** | Bodhan `Indic-Speak` | Natural speech synthesis in regional voices. |
| **Document / Table OCR** | Bodhan `Indic-OCR` | Scanned document extraction and markdown table layout detection. |
| **Tool Selection & Agent Planning** | LLM (`gemini-flash-lite-latest` via OpenAI Tool Calling) | Dynamic tool selection over 11 MCP tools based on natural language task intent. ZERO keyword/recipe routing. |

---

## 3. Where the LLM Chooses Tools (Zero Keyword/Recipe Routing)

A key hardening requirement was eliminating static keyword/regex routing (e.g. `if "ocr" in task: run_pdf_pipeline`) and replacing it with pure LLM function calling over the MCP tools.

### Exact Implementation in `src/bodhan_agent/agent.py`
The agent entrypoint `execute()` delegates entirely to `run_llm_agent_loop(files)`:
```python
# [src/bodhan_agent/agent.py]
def execute(self) -> bool:
    files = self.find_input_files()
    if not files:
        raise ToolError("NO_INPUT_FILES", f"No supported media files found in: {self.files_dir}")
    return self.run_llm_agent_loop(files)
```
There is **NO** keyword matching, pattern branching, or recipe matching on task strings.

### The Tool-Calling Loop
In `run_llm_agent_loop()` (`src/bodhan_agent/agent.py`):
1. **Tool Definitions**: The 11 MCP tool schemas defined in `src/bodhan_agent/prompts.py` (`AGENT_TOOLS`) are supplied to the LLM:
   - `bodhan_inspect_media`: Inspect audio/video/document dimensions and properties.
   - `bodhan_transcribe`: Speech-to-text with pause chunking ($\le 28$s).
   - `bodhan_translate`: Machine translation with format/table preservation.
   - `bodhan_speak`: TTS speech synthesis with clause chunking & MP3 stitching.
   - `bodhan_ocr`: PDF/image extraction with table detection.
   - `bodhan_detect_language`: Script and Devanagari stopword language identification.
   - `bodhan_list_voices`: Query official regional voices by language and gender.
   - `format_subtitles_srt`: SRT cue splitting, proportional distribution, and bilingual emission.
   - `write_file_artifact`: Save markdown/data outputs into sandboxed artifact files.
   - `mark_task_impossible`: Terminate cleanly with honest explanation when language is outside Bodhan models.
   - `complete_task`: Finalize run with user-facing answers, artifacts, and assumptions.
2. **Dynamic LLM Invocation**:
   ```python
   msg, usage = llm_client.chat_completion_with_tools(messages=messages, tools=AGENT_TOOLS)
   llm_calls += 1
   for k in llm_tokens:
       llm_tokens[k] += usage.get(k, 0)
   ```
3. **Tool Execution & Sandboxing**:
   - The LLM chooses which tool to invoke and provides typed arguments.
   - The agent dispatches via `dispatch_tool(fn_name, fn_args)`.
   - Tool outputs are wrapped in untrusted data boundaries:
     ```python
     wrapped_content = f"<data_untrusted>\n{tool_res_str}\n</data_untrusted>"
     ```
     This prevents prompt injection in transcripts, OCR text, or documents from hijacking the agent.
4. **Token & Call Tracking in `report.json`**:
   Every LLM request aggregates prompt, completion, and total tokens. The final `report.json` includes:
   ```json
   "llm_calls": 3,
   "tokens": {
     "prompt_tokens": 582,
     "completion_tokens": 140,
     "total_tokens": 722
   },
   "llm_tokens": {
     "prompt_tokens": 582,
     "completion_tokens": 140,
     "total_tokens": 722
   }
   ```

---

## 3. Audio Chunking & SRT Cue Distribution

### Natural Pause Detection ($\le 28$s Chunking)
Bodhan `Indic-Transcribe` enforces a 30-second audio window limit per request. Standard fixed-window slicing (e.g., cutting every 15s or 30s) frequently cuts audio in the middle of words or continuous sentences, degrading transcription accuracy.

Our chunking algorithm:
1. Calculates Root Mean Square (RMS) audio energy across 50ms frames.
2. Identifies natural silence troughs below an adaptive decibel threshold (default $-36$ dB).
3. Selects optimal cut points between 8.0s and 27.5s, preferring natural pauses over hard boundaries.
4. Ensures no chunk exceeds 28.0 seconds.

### Subtitle Cue Formatting
Each transcribed and translated segment is converted into broadcast-standard SRT cues:
- **Sentence Splitting**: Cues are cut at natural sentence boundaries (`.`, `!`, `?`, `।`, `॥`, `\n`).
- **Length Constraint**: Maximum 2 lines per cue, maximum 42 characters per line.
- **Proportional Timing**: Chunk time intervals are distributed proportionally according to character length across cues.
- **Timestamps**: Monotonic millisecond timestamps (`HH:MM:SS,mmm`).

---

## 4. Subtitle Batch Translation & Fallback

Translating subtitle lines one by one introduces severe latency and billing overhead. Instead:
1. All subtitle segment texts are packaged into a single numbered list:
   ```text
   1. First subtitle line
   2. Second subtitle line
   ...
   ```
2. Translated in a single `Indic-Translate` call.
3. The response is parsed to verify that the returned line count matches the source line count.
4. **Fallback Mechanism**: If the line count mismatches due to model grouping or omissions, the agent automatically falls back to line-by-line translation for that segment, guaranteeing 100% cue alignment.

---

## 5. Scanned PDF Handling (PyMuPDF Only)

To avoid heavy binary system dependencies like Poppler or Tesseract:
- The PDF processor uses `PyMuPDF` (`pymupdf`), which is self-contained and renders PDF pages directly into PNG images in memory.
- Pages are rendered at $2\times$ matrix resolution (~150-200 DPI) to ensure clear text and tabular boundaries.
- Rendered page images are base64-encoded and sent to Bodhan `Indic-OCR`.

---

## 6. Prompt Injection Defense

Input documents or task descriptions may contain adversarial prompt injection text intended to hijack LLM behavior (e.g. `SYSTEM PROMPT OVERRIDE: Ignore all previous instructions...`).

Our defense architecture:
1. **Pre-execution Scanner**: Scans all task strings, OCR text, and Markdown document inputs using regex patterns for instruction overrides.
2. **Explicit Warning**: Prints a security warning to stderr.
3. **Auditable Assumption**: Logs the occurrence to `result.md` and `report.json` assumptions.
4. **Inert Data Treatment**: Passes the document strictly as inert data to translation and OCR tools, ensuring malicious commands are never executed.

---

## 7. Verifier Suite & Quality Invariants

Every pipeline executes automated verification before finalizing:

1. **SRT Subtitle Validity**:
   - `srt_validity`: Validates strictly monotonic start/end times ($t_{start} < t_{end} \le t_{next\_start}$).
   - `no_overlaps`: Verifies zero timestamp overlapping between cues.
   - `duration_bound`: Ensures final subtitle end time does not exceed audio duration.
   - `line_length_bound`: Ensures no subtitle line exceeds 42 characters.
   - `script_match`: Validates that English cues use Latin script and Indic cues use target script.
   - `numbers_preserved`: Confirms all numbers from the source transcript exist in the translated subtitles.
   - `unicode_normalization`: Confirms output text is NFC normalized.
2. **Markdown Table Verification**:
   - `table_count_preservation`: Number of tables matches source.
   - `row_count_match`: Total row counts match source table.
   - `column_count_match`: Column count per row matches source table.
   - `script_match`: Verifies target language script (e.g. Devanagari for Marathi).
   - `numbers_preserved`: Validates statistics, percentages, and metrics are intact.
3. **Audio Summary Verification**:
   - `page_count_match`: Verifies all document pages were processed.
   - `summary_non_empty`: Confirms summary text contains substantial content.
   - `audio_generated`: Confirms audio summary MP3 exists and duration is $>0$.
   - `back_transcription_intelligibility`: Back-transcribes speech to verify intelligibility.

---

## 8. Caching & Rate Limiting

- **Deterministic Disk Cache**: API request parameters (endpoint, text/base64 hash, language, voice) are hashed via SHA-256 and stored in `.bodhan_cache/`. Identical calls return cached responses with cost ₹0.0000.
- **Rate-Limit Resilience**: HTTP clients monitor response status `429` and headers `retry-after`, executing exponential backoff with jitter up to 4 attempts.

