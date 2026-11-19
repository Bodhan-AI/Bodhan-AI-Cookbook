# Indic Multimodal Task Agent & MCP Tool Server

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![MCP Compliant](https://img.shields.io/badge/MCP-1.0-green.svg)](https://modelcontextprotocol.io/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](../../LICENSE)

An autonomous task agent and Model Context Protocol (MCP) server built specifically for **Bodhan AI Indic Models** (`Indic-Transcribe`, `Indic-Translate`, `Indic-Speak`, `Indic-OCR`). It plans, executes, verifies, and audits complex multimodal Indian language tasks unattended.

- **Demo Video**: [Demo Video Placeholder](https://youtu.be/placeholder_bodhan_agent_demo)
- **Detailed Design Notes**: [docs/DESIGN_NOTES.md](docs/DESIGN_NOTES.md)
- **Architecture Diagram**: [docs/architecture_diagram.md](docs/architecture_diagram.md)
- **Agent Flow Chart**: [docs/agent_flow_chart.md](docs/agent_flow_chart.md)
- **Live Evaluation Suite Results**: [docs/EVAL_RESULTS.md](docs/EVAL_RESULTS.md)
- **PR Description Template**: [docs/PR_DESCRIPTION.md](docs/PR_DESCRIPTION.md)

---

## Architecture Flowchart

```mermaid
flowchart TD
    User(["User Task & Media Input"]) --> Agent["Bodhan Task Agent"]
    
    subgraph Planning & Preflight
        Agent --> Inspect["Inspect Media\n(Duration, Format, Pages)"]
        Inspect --> Preflight{"Preflight Cost Check\n<= max_cost_inr?"}
        Preflight -- "Exceeded" --> FailStop["Stop Early & Honestly\n(Status: failed, Cost: ₹0.00)"]
        Preflight -- "OK" --> Route{"Route Pipeline"}
    end

    subgraph Execution Pipelines
        Route -- "Audio Subtitles" --> VS["Vertical Slice: Subtitles\n- Pause Chunking (<28s)\n- Indic-Transcribe\n- Indic-Translate (Batch)\n- Cue Timing (<42 ch, <=2 lines)"]
        Route -- "Scanned Document" --> PA["Pipeline A: Doc Audio Summary\n- PyMuPDF Page Render\n- Indic-OCR\n- Indic-Translate (Tamil)\n- Indic-Speak (MP3 Stitch)\n- Back-Transcribe Verify"]
        Route -- "Markdown Table" --> PB["Pipeline B: Table Translation\n- Structure Preservation\n- Indic-Translate (Marathi)\n- Row/Column Count Check"]
        Route -- "Language Detection" --> PC["Pipeline C: Auto-Detection\n- Indic-Transcribe (Auto-Detect)\n- Unicode Script & Heuristics"]
        Route -- "Non-Indic Target" --> PImp["Impossible Task\n(Status: impossible, Cost: ₹0.00)"]
    end

    subgraph Verification Suite
        VS --> Verifier["Automated Verifiers\n- Monotonicity & No Overlaps\n- Script & Unicode NFC\n- Numbers Preserved\n- Structure Integrity"]
        PA --> Verifier
        PB --> Verifier
        PC --> Verifier
    end

    subgraph Artifacts & Reporting
        Verifier --> Out["Sandboxed out/ Folder\n- artifacts/* (Relative Paths)\n- result.md (Summary & Markdown)\n- report.json (Machine Readable)\n- trace.jsonl (Step Audit Trail)"]
    end
```

---

## Setup

### Prerequisites
- Python 3.10+
- FFmpeg (for MP3/audio processing and stitching)
- Valid Bodhan API endpoint keys (issued per model from the [Bodhan Console](https://console.bodhan.ai/)):
  - `BODHAN_TRANSCRIBE_KEY`
  - `BODHAN_TRANSLATE_KEY`
  - `BODHAN_SPEAK_KEY`
  - `BODHAN_OCR_KEY`
- OpenAI-compatible LLM endpoint credentials (e.g. Gemini via Google AI Studio, Groq, Ollama, OpenAI):
  - `LLM_BASE_URL`
  - `LLM_MODEL`
  - `LLM_API_KEY`

### Installation
From the recipe directory:
```bash
python -m venv venv
# Linux/macOS:
source venv/bin/activate
# Windows PowerShell:
.\venv\Scripts\Activate.ps1

pip install -e .
cp .env.example .env
# Edit .env with your real API keys
```

*(Note: Cookbook convention references `# BODHAN_API_KEY=...` in `.env.example`, but this recipe strictly uses model-specific keys as issued by the Bodhan console).*

---

## Run

### 1. Running Sample Tasks via CLI
Sample text files are included in `sample_data/`. To generate synthetic audio, PDF, and degraded image samples:
```bash
python scripts/make_samples.py
```

Run CLI tasks:
```bash
# Vertical Slice: Hindi audio note -> English SRT subtitles
python -m bodhan_agent --task "Transcribe this Hindi voice note and give me English subtitles as an SRT file" --files samples/hindi_voicenote.mp3 --out out/run_srt --max-cost-inr 5.0

# Pipeline A: Scanned health report -> Tamil audio summary
python -m bodhan_agent --task "OCR this scanned health report and give me a Tamil audio summary" --files samples/stage_a/health_report_3page.pdf --out out/run_pdf --max-cost-inr 5.0

# Pipeline B: Village census table -> Marathi translated table
python -m bodhan_agent --task "Translate this village census table to Marathi" --files sample_data/village_census.md --out out/run_table --max-cost-inr 5.0
```

### 2. Running the Standalone MCP Server
```bash
python -m bodhan_mcp
```
Compatible with any standard MCP client (e.g. Claude Desktop, Antigravity, MCP Inspector).

### 3. Running Automated Tests (Zero Paid Calls)
```bash
pytest tests/
```
Executes all 51 unit and integration tests completely offline without network calls or paid API charges.

---

## How it works

1. **Autonomous LLM Loop**: The agent discovers candidate files, inspects media metadata, and selects MCP tools dynamically. It never hardcodes keyword routes.
2. **Text Passing by Reference**: When translating text destined for speech synthesis, `bodhan_translate` generates a text reference artifact (`text_artifact_id`). The LLM passes this artifact reference directly to `bodhan_speak` without copying or rewriting Indian-language text.
3. **Natural Pause Chunking**: `bodhan_transcribe` splits audio exceeding 28s at silence boundaries (RMS energy analysis) so sentences are never clipped mid-word.
4. **Prompt Injection Isolation**: Raw text extracted from documents or transcripts is treated strictly as passive data inside `<data_untrusted>`. Injected system commands are logged as assumptions and ignored.
5. **Hard Budget Enforcement**: Stated user budgets (e.g. "5 paise", "under Rs 1") automatically take precedence over CLI `--max-cost-inr` flags, halting execution if estimated costs exceed remaining funds.
6. **Automated Verifiers**: Invariant verification checks timestamp monotonicity, cue length limits ($\le 42$ chars/line, $\le 2$ lines), Devanagari script preservation, and back-transcription speech intelligibility.

---

## Bodhan Indic Models & Pricing Table

| Model | Capability | Hosted Rate | How the Tool Handles Limits |
| :--- | :--- | :--- | :--- |
| **`Indic-Transcribe`** | Speech-to-Text | ₹0.10 / min | Splits audio at natural pauses into $\le 28$s chunks; auto-detects language if omitted. |
| **`Indic-Translate`** | Machine Translation | ₹0.20 / 10k tokens | Batches numbered subtitle lines in 1 call; preserves markdown tables and column alignment. |
| **`Indic-Speak`** | Text-to-Speech | ₹6.00 / 10k chars | Chunks long text at punctuation boundaries; stitches returned MP3 segments. |
| **`Indic-OCR`** | Document OCR | ₹0.20 / page | Renders multi-page PDFs to images via PyMuPDF at $2\times$ resolution; combines text & tables. |
| **Disk Cache** | Repeat calls | ₹0.0000 | SHA-256 payload cache prevents duplicate billing. |

---

## Verification Status: What Has and Has Not Been Verified

- **Verified**:
  - **52 Offline Unit & Integration Tests**: 100% passing offline via `pytest` without network access or paid API calls running specifically on **Python 3.14.1 (Windows 11 x86_64)** (including user-stated budget hard-cap enforcement, reference-passing, audio verification without OCR, transcription cost accounting, LLM provider portability, and explicit `LLM_BASE_URL` requirement tests).
  - **Zero LLM Simulation**: Production agent runs strictly on remote LLM calls (`LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY`). Test doubles exist solely in `tests/test_doubles.py` for offline regression testing.
  - **Provider Portability**: Only Google Gemini (`gemini-flash-lite-latest`) has been live-tested. Standard OpenAI-compatible providers are supported by automated message sanitization in `LLMClient.sanitize_messages()` (stripping Gemini-specific `extra_content` and `thought_signature` fields), but have not been live-tested.
  - **Live Bodhan Indic Endpoints**: Live calls to Indic-Transcribe, Indic-Translate, Indic-Speak, and Indic-OCR verified against live schemas.
  - **Back-Transcription Coverage Honesty**: Verification honestly marks synthesis with $<70\%$ back-transcription coverage as **`WEAK`** (not `PASS`) and renders a prominent quality warning block.
- **Not Verified**:
  - **Python 3.11 and 3.12 Execution**: Tests were executed locally on Python 3.14.1. Local installation and execution on Python 3.11 and 3.12 remain unverified.
  - **GitHub Actions CI Workflow**: No standalone GitHub Actions workflow runs inside `examples/indic_multimodal_agent/` (workflows only run from repo-root `.github/workflows/`); recipe CI relies on repo-level `make check`.
  - **Local Host Docker Runtime**: Dockerfile syntax is provided for containerized deployment, but Docker image build and container runtime have not been executed on the local host machine.
  - **Non-Indic Languages**: German, French, and Spanish are intentionally not supported and are verified to be honestly rejected without hallucinations.

---

## Honest AI-Assistance Disclosure

In accordance with transparent engineering practices:
- **AI Coding Agent Role**: An AI assistant (Antigravity by Google DeepMind) was utilized to accelerate scaffold generation, format conversion, and test authoring under explicit human supervisor approval.
- **Human-in-the-Loop Verification**:
  - Prototyping was strictly prohibited until human approval of the folder structure, MCP schemas, and agent loop design.
  - **Step 0 Verification**: Live minimal API probes ($<₹1.00$) were executed before coding to record verified API response shapes in `docs/bodhan_api_notes.md`, eliminating speculative API hallucinations.
  - **Vertical Slice First**: A single vertical slice (Hindi audio note $\to$ English SRT) was implemented, verified, and reviewed before expanding to PDF OCR and table pipelines.
  - **Test-Driven Rigor**: Every bug identified during testing (e.g. cue wrapping, Devanagari stop-words, budget preflights) was resolved in a general, modular manner and paired with an automated unit test.

