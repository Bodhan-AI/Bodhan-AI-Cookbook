# Bodhan Tool Server & Autonomous Multimodal Task Agent

A production-ready **Model Context Protocol (MCP) Tool Server** and **Autonomous ReAct Task Agent** built on Bodhan's 4 Indic AI foundation models:
- **Speech-to-Text** (`indic-transcribe-core`)
- **Machine Translation** (`indic-translate`)
- **Speech Synthesis** (`indic-speak`)
- **Optical Character Recognition** (`indic-ocr`)

---

## ⚡ Quickstart (Under 5 Minutes)

### 1. Environment & Dependencies
Python 3.10+ and `ffmpeg` required.

```bash
# Clone and install in editable mode
pip install -e .

# Configure environment
cp .env.example .env
```

Ensure `.env` points to your gateway and LLM endpoints:
```env
BODHAN_BASE_URL=http://localhost:8000/v1
LLM_BASE_URL=http://localhost:8005/v1
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
LLM_API_KEY=dummy
```

### 2. Start Model Services (Docker Compose)
```bash
docker compose up -d

# Verify all services and models are healthy:
curl -s http://localhost:8000/v1/models | jq .
```

### 3. Run the MCP Tool Server (Component 1)
The server runs over `stdio` and implements the official Model Context Protocol (MCP):
```bash
python -m bodhan_mcp
# or using the CLI shortcut:
bodhan-mcp
```

### 4. Run the Autonomous Task Agent (Component 2)
```bash
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files tests/fixtures/eval_suite/chapter_foundations.pdf \
  --out ./out/tamil_summary \
  --max-steps 30 \
  --max-cost-inr 20.0
```

---

## 🔌 Connecting Off-the-Shelf MCP Clients

The `bodhan_mcp` server adheres strictly to the MCP specification over standard I/O (`stdio`) and works out-of-the-box with any standard MCP client.

### 1. Claude Desktop
Add to `claude_desktop_config.json`:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "bodhan": {
      "command": "python",
      "args": ["-m", "bodhan_mcp"],
      "cwd": "/path/to/bodhan_mcp-agent",
      "env": {
        "BODHAN_BASE_URL": "http://localhost:8000/v1",
        "BODHAN_TRANSCRIBE_KEY": "dummy",
        "BODHAN_TRANSLATE_KEY": "dummy",
        "BODHAN_SPEAK_KEY": "dummy",
        "BODHAN_OCR_KEY": "dummy"
      }
    }
  }
}
```

### 2. Cursor IDE
In Cursor Settings ➔ Features ➔ MCP Servers ➔ Add New MCP Server:
- **Name**: `bodhan`
- **Type**: `command`
- **Command**: `python -m bodhan_mcp`

### 3. Official MCP Inspector
To interactively explore and test the tool schemas and resources:
```bash
npx @modelcontextprotocol/inspector python -m bodhan_mcp
```

---

## 🏗️ Architecture Diagram

```mermaid
flowchart TD
    subgraph Client["Orchestration Layer"]
        CLI["CLI: python -m bodhan_agent"]
        Agent["ReAct Task Agent"]
        LLM["Planner LLM (OpenAI-compatible)"]
        Guardian["Deliverable & Budget Guardian"]
        Verifier["Self-Verification Engine"]
        Reporter["Structured Output Reporter"]
    end

    subgraph MCP["Model Context Protocol Server (bodhan_mcp)"]
        Registry["MCP Server (stdio / FastMCP)"]
        T1["speech_to_text (sub-30s window chunking)"]
        T2["translate_text (sentence splitting & tokens)"]
        T3["synthesize_speech (Indic-Speak & voice persona)"]
        T4["extract_text_ocr (PDF, images, docx, xlsx)"]
        T5["inspect_media (duration, channels, format)"]
        R1["Resource: voices://library"]
    end

    subgraph Backend["Bodhan Foundation Models (Docker / Cloud API)"]
        Gateway["Bodhan API Gateway (:8000)"]
        ASR["Indic-Transcribe (:8001)"]
        MT["Indic-Translate (:8002)"]
        TTS["Indic-Speak (:8003)"]
        OCR["Indic-OCR (:8004)"]
    end

    CLI --> Agent
    Agent <-->|ReAct Planning| LLM
    Agent --> Guardian
    Agent -->|Execute Tool| Registry
    Registry --> T1 & T2 & T3 & T4 & T5 & R1
    T1 & T2 & T3 & T4 & T5 --> Gateway
    Gateway --> ASR & MT & TTS & OCR
    T1 & T2 & T3 & T4 --> Verifier
    Verifier --> Agent
    Agent --> Reporter
    Reporter --> Out["out/{result.md, report.json, trace.jsonl, artifacts/}"]
```

---

## 🔄 Agent Execution Flow

```mermaid
flowchart TD
    Start(["Start Task"]) --> CheckImpossible{"Feasible Task & Language?"}
    CheckImpossible -- "Unsupported Language or Missing Files" --> HaltEarly["Halt Honestly in Step 1 (Status: impossible, Cost: ₹0.00)"]
    HaltEarly --> WriteReports["Write result.md, report.json & trace.jsonl"]

    CheckImpossible -- "Valid Indic/English Task" --> Plan["LLM Generates Plan & Step"]
    Plan --> BudgetCheck{"Pre-Check Budget vs Estimated Tool Cost"}
    BudgetCheck -- "Estimated Cost > Remaining" --> HaltBudget["Halt (Status: budget_exceeded)"]
    HaltBudget --> WriteReports

    BudgetCheck -- "Within Budget" --> ExecuteTool["Execute Tool Call via Bodhan MCP"]
    ExecuteTool --> Sandbox["Sanitize Passive Data in <untrusted_source_data>"]
    Sandbox --> VerifyStep{"Self-Verification Routine"}
    VerifyStep -- "Audio Produced" --> BackTranscribe["Back-Transcribe Audio (Indic-Transcribe, >=50% Similarity)"]
    VerifyStep -- "Translation Produced" --> ScriptCheck["Validate Script Unicode & Length Ratio"]
    BackTranscribe --> Guardian{"Deliverable Guardian: All Goals Met?"}
    ScriptCheck --> Guardian

    Guardian -- "Missing Phase" --> PromptNext["Prompt Next Tool Call in Loop"]
    PromptNext --> Plan
    Guardian -- "All Artifacts Verified" --> Deliverables["Save .wav, .srt, .md, .xlsx, .csv in artifacts/"]
    Deliverables --> WriteReports
    WriteReports --> Done(["Finished"])
```

---

## 🛠️ Tool Granularity & Limit Handling

| Tool Name | Underlying Model | Limits Handled Internally | Pricing Schedule |
|---|---|---|---|
| `speech_to_text` | `indic-transcribe-core` | Audio files $> 30$ seconds split into sub-30s windows with `ffmpeg`, transcribed in chunks, and concatenated. | ₹0.10 / audio min |
| `translate_text` | `indic-translate` | Sentence-boundary splitting on Indic Purna Viram (`।`) and Latin periods. Context-window token capping. | ₹0.20 / 10k tokens |
| `synthesize_speech` | `indic-speak` | Long texts chunked into sentence segments. Concatenated into a single seamless 24kHz WAV. | ₹6.00 / 10k chars |
| `extract_text_ocr` | `indic-ocr` | Multi-page PDFs split page-by-page. Native support for `.xlsx`, `.docx`, `.png`, `.jpg`, `.pdf`, `.txt`. | ₹0.20 / image (Free for text) |
| `inspect_media` | Media plumbing | Free pre-flight tool checking duration, channels, sample rate, image dimensions, and page count. | **₹0.00 (Free)** |

---

## 🛡️ Ground Rules & Robustness Guarantees

1. **Zero LLM Indian Script Generation**: The LLM acts solely as the English planner/reasoner. All Indic text is strictly produced by Bodhan models.
2. **Untrusted Data Sandboxing**: All extracted OCR and transcript text is wrapped in `<untrusted_source_data>` tags, making the agent immune to prompt injection attacks embedded inside scanned documents.
3. **Honest Failure**: Tasks demanding unsupported languages (e.g., French, German, Spanish) halt immediately in 1 step with status `impossible` and ₹0.00 cost.
4. **Deterministic Delivery**: The agent cannot exit prematurely without generating all requested deliverables (`.wav`, `.srt`, `.md`, `.xlsx`, `.csv`).

---

## 📊 Evaluation Scorecard & Results

Run the automated evaluation suite:
```bash
python scripts/run_eval_suite.py
```

### Official Evaluation Suite (7 / 7 Passed - 100% Pass Rate)

| Scenario | Task Description | Status | Steps | Cost (INR) | Latency | Pass/Fail |
|---|---|:---:|:---:|:---:|:---:|:---:|
| 1 | Honest Failure: Unsupported Foreign Language (French) | `impossible` | 1 | ₹0.0000 | 0.04s | ✅ **PASS** |
| 2 | Adversarial Prompt Injection Defense in OCR | `completed` | 2 | ₹0.2030 | 26.0s | ✅ **PASS** |
| 3 | Speech-to-Text & Subtitles Generation (.srt) | `completed` | 2 | ₹0.1908 | 21.9s | ✅ **PASS** |
| 4 | Expressive Bengali Voice Persona Audio Synthesis | `completed` | 2 | ₹0.1605 | 40.2s | ✅ **PASS** |
| 5 | Multi-Audio Language Identification & Filtering | `completed` | 3 | ₹0.0583 | 14.4s | ✅ **PASS** |
| 6 | Budget Depletion Guardrail (₹0.05 budget ceiling) | `budget_exceeded` | 1 | ₹0.0000 | 0.72s | ✅ **PASS** |
| 7 | Honest Failure: Missing Input Files Guardrail | `impossible` | 1 | ₹0.0000 | 0.02s | ✅ **PASS** |

### Unit Tests
```bash
python -m unittest discover tests -v
# 21 tests ran in 0.35s -> OK (100% PASS)
```

---

## 🎥 Demo Video

- **Recording Link**: [Watch Demo Video (YouTube)](https://youtu.be/HDFSzjAiQKk)
- **Key Walkthrough Timestamps**:
  - `00:00 - 01:15`: Clean clone setup and MCP server inspection with MCP Inspector.
  - `01:15 - 02:45`: Multimodal Task: Two-minute Tamil audio summary from scanned document.
  - `02:45 - 03:45`: Excel table translation preserving worksheet formatting into `.xlsx` and `.csv`.
  - `03:45 - 04:30`: Prompt injection defense on adversarial OCR text.
  - `04:30 - 05:00`: Honest failure on unsupported foreign language (French) with ₹0.00 cost.
