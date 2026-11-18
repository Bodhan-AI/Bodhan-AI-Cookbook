# Bodhan Tool Server & Task Agent

> **Production-grade Model Context Protocol (MCP) Server and Autonomous Task Agent built exclusively for Bodhan AI's sovereign Indic models.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Specification](https://img.shields.io/badge/MCP-1.0-green.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 19 Passed](https://img.shields.io/badge/tests-19%20passed-brightgreen.svg)](tests/)

---

## Architecture Overview

This recipe implements a decoupled, modular architecture adhering to strict separation of concerns:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Task Agent (`bodhan_agent`)                     │
│                                                                        │
│  CLI Command: python -m bodhan_agent --task ... --files ... --out ...  │
│                                                                        │
│   ┌────────────────┐      ┌─────────────────┐     ┌────────────────┐   │
│   │  Task Planner  │ ---> │ Execution Loop  │ --->│ Budget Tracker │   │
│   │ (OpenAI-compat)│      │  (Act & Retry)  │     │ (Steps/INR/sec)│   │
│   └────────────────┘      └────────┬────────┘     └────────────────┘   │
│                                    │                                   │
│                           ┌────────┴────────┐                          │
│                           │ Output Verifier │                          │
│                           │ (Script/Length) │                          │
│                           └────────┬────────┘                          │
└────────────────────────────────────┼───────────────────────────────────┘
                                     │ JSON-RPC / MCP Client or Shared Layer
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        MCP Server (`bodhan_mcp`)                       │
│                                                                        │
│   Exposes Bodhan Models via Stdio (`python -m bodhan_mcp`)             │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │ Content-Addressed Deterministic Cache (.cache/bodhan)          │   │
│   └────────────────────────────────┬───────────────────────────────┘   │
│                                    ▼                                   │
│   ┌───────────────────┬───────────────────┬────────────────────────┐   │
│   │ Indic-Transcribe  │  Indic-Translate  │      Indic-Speak       │   │
│   │  (Core & Flex)    │ (22+ Languages)   │  (22+ Langs & Voices)  │   │
│   │  [<=30s windows]  │ [32K ctx & docs]  │  [AI Disclaimer Tag]   │   │
│   ├───────────────────┴───────────────────┴────────────────────────┤   │
│   │ Indic-OCR (Multi-page PDF & image text extraction)             │   │
│   ├────────────────────────────────────────────────────────────────┤   │
│   │ Media Helpers (inspect_media, chunk_audio, file-ref validation)│   │
│   └────────────────────────────────┬───────────────────────────────┘   │
│                                    ▼                                   │
│                      Bodhan API (api.bodhan.ai/v1)                     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Strict Ground Rules & Constraints

1. **Only Bodhan Models**:
   - Speech-to-Text: `Indic-Transcribe` (Core/Flex)
   - Translation: `Indic-Translate` (22 scheduled Indian languages + English)
   - Speech Synthesis: `Indic-Speak` (Teacher-paced, STEM-aware, code-mixed)
   - Document OCR: `Indic-OCR` (Printed/handwritten multi-script recognition)
   - *Strictly prohibited:* Whisper, Tesseract, cloud vision/translate, or third-party Indic models.
2. **LLM Boundaries**:
   - The LLM (configured via `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`) is strictly an orchestrator: planning, tool routing, English explanations, and verification judgment.
   - The LLM **never** writes or edits Indian-language text directly, transcribes audio, reads images, or synthesizes speech.
3. **Media & Cost Rules**:
   - Media files are referenced strictly by file paths (never serialized inline as base64 in LLM conversation context).
   - All tool calls are content-addressed and cached locally (`.cache/bodhan`) to prevent duplicate spend.
   - Every synthesized audio file is stamped with AI-generated metadata.
   - Billed costs are computed accurately in **INR (₹)** based on official Bodhan pricing:
     - Transcribe: ₹0.10 / min
     - Translate: ₹0.20 / 10,000 output tokens
     - Speak: ₹6.00 / 10,000 characters
     - OCR: ₹0.20 / page
4. **Untrusted Content & Prompt Injection Defenses**:
   - Transcripts, OCR outputs, and user files are treated strictly as inert data enclosed in `<data_payload>` containers.
   - Text inside payloads is never executed as agent instructions.
5. **Honest Handling of Impossible Tasks**:
   - If an unsupported language is requested (e.g., German, French), a file is missing, or the budget is insufficient, the agent halts cleanly and reports `IMPOSSIBLE` or `BUDGET_EXCEEDED` in `result.md` and `report.json` without fake outputs.

---

## Setup

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/PushpendarSingh23/bodhan-tool-server-agent.git
cd bodhan-tool-server-agent/examples/bodhan-tool-server-agent

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```
*(If no keys are provided, the system automatically runs in deterministic mock mode for offline testing).*

---

## Run

### 1. Running the MCP Server (`bodhan_mcp`) over stdio
```bash
python -m bodhan_mcp
```

### 2. Running the Task Agent (`bodhan_agent`) via CLI
```bash
python -m bodhan_agent \
  --task "Transcribe lecture.wav in Hindi, translate to Kannada, and synthesize speech in Kannada" \
  --files ./lecture.wav \
  --out ./runs/run-01 \
  --max-steps 30 \
  --max-cost-inr 20 \
  --timeout 300
```

---

## MCP Server (`bodhan_mcp`)

The MCP server runs over standard I/O (`stdio`) conforming to the Model Context Protocol.

### Running Standalone
```bash
python -m bodhan_mcp
```

### Testing with Off-the-Shelf MCP Clients
You can connect and inspect `bodhan_mcp` using any standard MCP client:

1. **Official MCP Inspector (`@modelcontextprotocol/inspector`)**:
   ```bash
   npx @modelcontextprotocol/inspector python -m bodhan_mcp
   ```
   Opens an interactive browser UI on `http://localhost:5173` to list, test, and debug all Bodhan tools.

2. **Claude Desktop**:
   Add to `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):
   ```json
   {
     "mcpServers": {
       "bodhan": {
         "command": "python",
         "args": ["-m", "bodhan_mcp"],
         "cwd": "/path/to/examples/bodhan-tool-server-agent"
       }
     }
   }
   ```

### Registered Tools
| Tool Name | Parameters | Description |
|-----------|------------|-------------|
| `bodhan_transcribe` | `audio_path`, `language`, `model_variant` | Transcribes audio (handles >30s windowing and audio chunk merging) |
| `bodhan_translate` | `text`, `text_path`, `source_lang`, `target_lang` | Translates text across 22 Indian languages preserving Markdown/LaTeX |
| `bodhan_speak` | `text`, `text_path`, `language`, `voice`, `output_path` | Synthesizes speech with short-input chunking & AI disclaimer metadata |
| `bodhan_ocr` | `file_path`, `output_path` | Multi-page PDF and image text extraction in reading-ordered Markdown |
| `bodhan_inspect_media` | `file_path` | Validates file format, audio duration, image dimensions, PDF page count |
| `bodhan_chunk_audio` | `audio_path`, `max_duration_sec`, `output_dir` | Splits audio files into standard <=30s chunks |

---

## Task Agent (`bodhan_agent`)

The CLI agent plans and executes multi-step Indic workflows, enforcing rupee, step, and timeout budgets.

### CLI Syntax
```bash
python -m bodhan_agent \
  --task "Transcribe lecture.wav in Hindi, translate to Kannada, and synthesize speech in Kannada" \
  --files ./lecture.wav \
  --out ./runs/run-01 \
  --max-steps 30 \
  --max-cost-inr 20 \
  --timeout 300
```

### Generated Artifacts Dossier
Each run outputs to the `--out` folder:
- `result.md`: Clear, human-readable summary, execution status, costs, and links.
- `report.json`: Structured, machine-readable run report (Pydantic schema).
- `trace.jsonl`: Step-by-step audit log of plans, tool calls, costs, and verifications.
- `artifacts/`: Intermediate and final output files (transcripts, translations, audio, OCR).

---

## Running Verification & Demo Suites

### 1. Automated Test Suite (pytest)
```bash
make test
# or: pytest -v tests/
```
Runs 19 comprehensive tests validating limits, audio slicing, script matching, and multi-step pipelines.

### 2. Comprehensive 8-Task Demo Suite
```bash
make demo
# or: python scripts/run_demo_suite.py
```
Executes 8 end-to-end tasks and prints a formatted execution summary:
1. Speech-to-Text & Translation (Hindi -> Kannada)
2. Speech-to-Speech Localization (Hindi -> Tamil)
3. Document OCR & Markdown Translation (Image -> Telugu)
4. Document OCR to Speech Narration (Image -> Bengali -> Speech)
5. Long Audio Windowing (>30s Chunks -> Marathi Translation)
6. **Impossible Task:** Unsupported Language (French -> Clean Halt, 0 Fake Output)
7. **Impossible Task:** Missing Input File (Clean Halt, 0 Fake Output)
8. **Budget Limit Safety:** Insufficient Budget (₹0.001 limit -> Clean Halt)

### 3. Repository Hygiene & Import Check
```bash
make check
```

---

## Demo Video Script & Submission Checklist

### Demo Video Recording Script (<= 5 Minutes)
Record your screen showing:
1. **0:00 - 0:45:** Brief introduction to `bodhan_mcp` and `bodhan_agent` folder layout.
2. **0:45 - 1:45:** Launching `npx @modelcontextprotocol/inspector python -m bodhan_mcp` to show the tools over stdio.
3. **1:45 - 3:00:** Running `python -m bodhan_agent` with an audio task; showing the terminal output and cost tracking.
4. **3:00 - 4:00:** Opening `result.md`, `report.json`, and the synthesized `.wav` audio and companion `.json` disclaimer.
5. **4:00 - 4:45:** Running an **Impossible Task** (French translation); showing clean halt, ₹0 cost, and honest explanation.
6. **4:45 - 5:00:** Running `make test` to show 19 passing tests.

### Final PR Submission Checklist
- [x] Recipe cleanly organized under `examples/bodhan-tool-server-agent/`.
- [x] Branch named `assignment/tool-agent-PushpendarSingh23`.
- [x] All 19 tests pass (`make test`).
- [x] `make check` passes with clean git hygiene.
- [x] No `.env`, secrets, or large binary media committed to version control.
- [x] `PR_DESCRIPTION.md` prepared following the required PR template.
- [ ] Record and attach your <=5 minute demo video link in `PR_DESCRIPTION.md`.
- [ ] Open PR against `Bodhan-AI/Bodhan-AI-Cookbook` titled `[Agentic Eng Assignment] Tool server and agent — Pushpendar Singh`.
- [ ] Reply on the assignment email thread with the PR link and final commit SHA.
