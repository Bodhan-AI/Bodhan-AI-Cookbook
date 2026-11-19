# Pull Request: Indic Multimodal Task Agent & MCP Tool Server

## Summary
A Model Context Protocol (MCP) server and autonomous task agent built for Bodhan AI's four Indic models (`Indic-Transcribe`, `Indic-Translate`, `Indic-Speak`, `Indic-OCR`). It enables unattended, multi-step orchestration across 10+ Indian languages with automated pause chunking, Unicode normalization, invariant verification, and strict INR budget tracking.

## How to run

### Setup from Clean Clone
```bash
cd examples/indic_multimodal_agent
python -m venv venv
# Linux/macOS: source venv/bin/activate
# Windows: .\venv\Scripts\Activate.ps1
pip install -e .
cp .env.example .env
# Edit .env with your real API keys
```

### Agent CLI Command
```bash
python -m bodhan_agent --task "Transcribe this Hindi voice note and give me English subtitles as an SRT file" --files samples/hindi_voicenote.mp3 --out out/subtitles_run --max-cost-inr 5.0
```

### Standalone MCP Server Command
```bash
python -m bodhan_mcp
```

### Run Automated Tests (Zero Paid Calls)
```bash
pytest tests/
```

## Architecture
See detailed diagrams in [`docs/architecture_diagram.md`](docs/architecture_diagram.md) and [`docs/agent_flow_chart.md`](docs/agent_flow_chart.md).

- **`bodhan_mcp` (MCP Server)**: Standards-compliant MCP server exposing Bodhan models as high-level tools with automatic pause-based chunking and token preservation.
- **`bodhan_agent` (Autonomous Agent)**: Pure LLM tool-calling loop over MCP tools without hardcoded keyword routing.
- **`BudgetTracker`**: Preflight cost estimator and real-time INR ledger that enforces user-stated budget caps (e.g. "5 paise").
- **`Verifier`**: Automated invariant checker testing timestamp monotonicity, cue length ($\le 42$ chars), Devanagari/Dravidian script consistency, and back-transcription coverage.
- **`Tracer`**: Sandboxed reporter emitting relative-path `report.json`, `result.md`, and `trace.jsonl`.

## Agent flow
1. **Plan & Inspect**: Agent discovers input files, probes audio/document properties, and estimates cost.
2. **Preflight Budget Check**: Halts immediately if estimated cost exceeds remaining budget cap.
3. **Act**: Calls MCP tools (`bodhan_transcribe`, `bodhan_translate`, `bodhan_speak`, `bodhan_ocr`).
4. **Pass by Reference**: Passes translated text artifacts to speech synthesis via `text_artifact_id` so the LLM never writes Indic text.
5. **Verify**: Runs invariant suites (SRT cues, Markdown table row/cols, back-transcription speech intelligibility).
6. **Report**: Emits machine-readable `report.json` and human-readable `result.md`.

## Tool design
- **`bodhan_transcribe`**: Splits long audio at natural silence boundaries into chunks $\le 28$s to respect the 30s model limit; detects language automatically when omitted.
- **`bodhan_translate`**: Batches subtitle cues as numbered lines to preserve timing; enforces 1:1 line count matching with fallback.
- **`bodhan_speak`**: Splits long text at clause boundaries, synthesizes chunks, and stitches into MP3; accepts `text_artifact_id` references.
- **`bodhan_ocr`**: Renders multi-page PDFs to images at $2\times$ resolution using PyMuPDF; extracts layout blocks and markdown tables.
- **`bodhan_detect_language`**: Disambiguates Hindi vs. Marathi via Devanagari stop-words; computes confidence score.
- **`bodhan_list_voices`**: Filters official Bodhan regional personas by language and gender.

## Where the LLM is used, and why
- **Task Understanding & Tool Selection**: Chooses which MCP tools to call based on input media and user goals.
- **English Text Generation**: Writes summaries and analysis in English only.
- **Prompt Injection Isolation**: Treats extracted document/transcript text as inert data, logging assumptions without executing injected instructions.
- **Refusal**: Honestly refuses non-Indic target languages (e.g. Spanish, French) via `mark_task_impossible` in Step 0.

## Bodhan models used
- `Indic-Transcribe` (Core/Flex hosted API): Speech-to-text with auto-detection.
- `Indic-Translate` (hosted API): Cross-lingual text translation.
- `Indic-Speak` (hosted API): Multi-persona speech synthesis.
- `Indic-OCR` (hosted API): Vision/document table and text extraction.

## Results
- **16-Task Unseen Evaluation Suite**: 100% PASS rate across degraded PDF/images, background noise, multi-language audio detection, and adversarial documents (see [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md)).
- **6 Blind Task Suite**: Validated with zero hardcoded routing:
  - Task a: Hindi voice memo $\to$ English bullet list $\to$ Marathi audio summary (PASS, ₹0.0670).
  - Task b: Audio language screening from shuffled neutral filenames (`memo_final.wav`, etc.) (PASS, ₹0.0000 cached).
  - Task c: Scanned table extraction to CSV + Telugu translation (PASS, ₹0.0051).
  - Task d: Dub into Spanish $\to$ Honest refusal in 3.9s (IMPOSSIBLE, ₹0.0000).
  - Task e: Marathi subtitles with 5 paise budget cap $\to$ Enforced ₹0.05 cap (PASS, ₹0.0000 cached).
  - Task f: Scanned document Tamil audio read-aloud + back-transcription match (PASS, ₹0.2251, 73.5% coverage).

## Demo video
- [Demo Video Placeholder](https://youtu.be/placeholder_bodhan_agent_demo) *(Recording of CLI runs, MCP tools, and verification suite)*

## Trade-offs and known limitations
- Only Google Gemini (`gemini-flash-lite-latest`) has been live-tested; other OpenAI-compatible endpoints are supported via message sanitization.
- Non-Indic languages (German, French, Spanish) are intentionally not supported.

## Cost and runtime
- Cumulative live Bodhan spend across full development and all evaluation suites: **< ₹2.50**.
- Average single-task latency: 8s to 45s depending on audio synthesis length.

## AI assistance
Developed with the assistance of Antigravity (Google DeepMind) under human-in-the-loop oversight. All tool contracts, live response shapes, and invariant verifications were established via minimal live probes (< ₹1.00) before agent coding.
