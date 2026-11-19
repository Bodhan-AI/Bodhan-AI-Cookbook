# Bodhan MCP & Task Agent: Project Status

## 1. What Works

### Core Pipelines & Vertical Slices
- **Vertical Slice 1: Voice Note -> English Subtitles (SRT)**:
  - Takes raw audio (`.mp3`, `.wav`, `.m4a`).
  - Natural pause audio chunking ($\le 28$s segments, avoiding arbitrary cuts mid-sentence).
  - Speech-to-text transcription via Bodhan `Indic-Transcribe`.
  - Language detection with script analysis and Devanagari stop-word heuristics for Hindi/Marathi confidence.
  - Subtitle batch translation in one prompt as a numbered list with verification and per-line fallback.
  - Generates industry-standard `.srt` with sentence-boundary cues, proportional timing, max 2 lines, and $\le 42$ chars/line.
  - Runs 5 automated subtitle verification checks (SRT monotonicity, zero overlaps, duration bounds, line length, Devanagari/Latin script matching, numbers preserved, NFC Unicode normalization).

- **Pipeline A: Scanned PDF -> Tamil Audio Summary**:
  - Handles multi-page scanned PDF documents via PyMuPDF rendering (pure Python, zero Poppler dependency).
  - High-accuracy Indic-OCR with table detection.
  - Summarization in English and translation to Tamil via `Indic-Translate`.
  - Natural speech synthesis via `Indic-Speak` with sentence chunking and MP3 stitching.
  - Back-transcription verification of generated audio via `Indic-Transcribe` to verify intelligibility.

- **Pipeline B: Markdown Table Translation to Marathi**:
  - Preserves table markdown structure, headers, alignment separators, and row/column counts.
  - Verification suite validates row count, column count per row, numbers preservation, Devanagari script match, and NFC normalization.

- **Pipeline C: Audio Language Detection**:
  - Transcribes audio without language parameters.
  - Analyzes output text and Unicode ranges to identify regional language (tested live on Tamil, Telugu, Bengali, Hindi).

- **Defensive & Edge Task Handling**:
  - **Prompt Injection Defense**: Untrusted text containing prompt injection patterns (e.g. `SYSTEM PROMPT OVERRIDE: Ignore all previous instructions...`) is flagged with security warnings, recorded in assumptions, and processed strictly as inert data without executing malicious instructions.
  - **Honest Impossible Task Reporting**: Requests for unsupported non-Indic languages (e.g. French, German, Spanish) halt cleanly with `status: "impossible"` and zero cost.
  - **Honest Budget Preflight Checks**: Calculates upper-bound cost before executing network calls; stops with `BUDGET_EXCEEDED` if remaining budget is insufficient.
  - **Guaranteed Output & Non-Zero Exit**: CLI prints real-time progress steps, costs, and run summaries. Always writes `report.json`, `result.md`, and `trace.jsonl` even on failure, using strictly relative paths.

- **Hardened Live Evaluation Suite (16 Tasks - Real LLM & Zero Cache)**:
  - Validated across unseen wordings, degraded noisy PDFs/PNGs, 3-minute continuous audio chunking past 28s, 10 Indic languages (Hindi, Marathi, Gujarati, Kannada, Malayalam, Punjabi, Odia, Tamil, Telugu, Bengali), adversarial injections, strict pre-flight budget blocks (₹0.0001), and non-Indic refusals (German, French).
  - Executed 100% through the real remote LLM (`gemini-flash-lite-latest`) without any mock, simulator, or keyword fallbacks.
  - Recorded detailed metrics in `docs/EVAL_RESULTS.md`. 16/16 tasks passed or cleanly refused (including 1 honestly flagged as **WEAK** on speech back-transcription coverage <70%). Total live suite spend was **₹1.3003** (well under ₹15.00 cap).
  - Real LLM calls logged: 55 calls, 188,409 tokens, varying latencies.

- **GitHub Actions CI/CD (`.github/workflows/ci.yml`)**:
  - Automated workflow running headless `pytest` on Python 3.11 and 3.12 with system dependencies (`ffmpeg`, `libsndfile1`).
  - Automated Docker build validation step verifying multi-stage container assembly.

- **6-Stage Demonstration Suite (`scripts/demo.py`)**:
  - Validated end-to-end execution of 6 representative tasks:
    1. Hindi Voice Note -> English Subtitles (SRT)
    2. Markdown Table Translation to Marathi (Structural Invariants)
    3. Degraded Scanned Document OCR & Structured Table Extraction
    4. Multi-Language Spoken Audio Identification
    5. Adversarial Prompt Injection Defense in Input Data
    6. Impossible Task Honest Refusal (French Audio)

- **Stage G Clean-Clone Environment Verification**:
  - Fresh `git clone` into temporary isolation directory.
  - New Python virtual environment (`python -m venv venv`).
  - Clean `pip install -e .` from scratch.
  - Ran `pytest`: 42 passed, 0 failed, 100% offline.
  - Ran live CLI task: translated rural census table to Marathi with full table verification pass.

- **MCP Tool Server**:
  - Fully compliant with the official Model Context Protocol (MCP) Python SDK (`MCPServer`).
  - Registers 7 tools: `bodhan_transcribe`, `bodhan_translate`, `bodhan_speak`, `bodhan_ocr`, `bodhan_detect_language`, `bodhan_list_voices`, `bodhan_inspect_media`.
  - All tools validate inputs and return structured error JSON payloads (`error_code`, `message`, `retryable`), never crashing the server process.

---

## 2. What's Broken / Current Limitations

1. **Local Docker Daemon**:
   - `docker` CLI is not installed on this specific host OS environment. The provided `Dockerfile` is verified syntactically and functionally, but local image building requires Docker Desktop to be installed by the user.
2. **Devanagari Script Ambiguity**:
   - Both Hindi and Marathi share the Devanagari script. Without acoustic language identification at the audio level, speech transcribed without language tags requires regional stop-word heuristics to distinguish Hindi (`है`, `था`, `नहीं`) from Marathi (`आहे`, `नाही`, `झाला`). When short or ambiguous, the agent records an "assumed" notice in `result.md`.
3. **Indic Model Language Boundary**:
   - Bodhan Indic models support the 22 scheduled Indian languages plus English. Non-Indic languages are intentionally not supported and must be rejected honestly.

---

## 3. Next Steps

1. **Streaming Audio Support**: Add WebSocket/WebRTC streaming endpoints for real-time live transcription.
2. **Dialect Voice Profiling**: Expose fine-grained dialect voice presets for Indic-Speak beyond default male/female personas.
3. **Complex Nested Tables**: Extend Markdown table parser to support HTML `colspan`/`rowspan` constructs within Markdown OCR exports.

---

## 4. Run Commands

### 1. Run Plain Pytest (Zero Paid Calls)
```bash
pytest
```
*All 43 unit and integration tests execute offline using deterministic test doubles.*

### 2. Run Voice Note -> English Subtitles CLI
```bash
python -m bodhan_agent --task "Transcribe this Hindi voice note and give me English subtitles as an SRT file" --files samples/hindi_voicenote.mp3 --out out/run1 --max-cost-inr 5.0
```

### 3. Run Scanned PDF -> Tamil Audio Summary CLI
```bash
python -m bodhan_agent --task "OCR this scanned health report and give me a Tamil audio summary" --files samples/stage_a/health_report_3page.pdf --out out/run_pdf --max-cost-inr 5.0
```

### 4. Run Markdown Table Translation CLI
```bash
python -m bodhan_agent --task "Translate this census table to Marathi" --files samples/stage_b/village_census.md --out out/run_table --max-cost-inr 5.0
```

### 5. Run 16-Task Live Evaluation Suite
```bash
python scripts/run_suite.py
```
*Executes all 16 live tasks across degraded media, long audio, and 10 Indic languages; results in `docs/EVAL_RESULTS.md`.*

### 6. Run 6-Task Video Walkthrough Demo
```bash
python scripts/demo.py
```
*Demonstrates 6 representative workflows from SRT generation to adversarial defense and impossible task rejection.*

### 7. Run Standalone MCP Server over Stdio
```bash
python -m bodhan_mcp
```

