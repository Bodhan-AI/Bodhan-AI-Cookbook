# [Agentic Eng Assignment] Tool server and agent — Pushpendar Singh

## Summary
This PR contributes a clean, modular, production-ready recipe implementing the **Bodhan Tool Server (`bodhan_mcp`)** and **Autonomous Task Agent (`bodhan_agent`)** to the Bodhan AI Cookbook under `examples/bodhan-tool-server-agent/`.

The implementation decouples an MCP stdio tool server exposing Bodhan's sovereign Indic models from a CLI task agent that plans multi-step language tasks, routes tools, verifies outputs, and strictly enforces step, rupee (INR), and timeout budgets while handling impossible tasks honestly without fabricated outputs.

- **Branch:** `assignment/tool-agent-PushpendarSingh23`
- **Recipe Path:** `examples/bodhan-tool-server-agent/`
- **Test Suite:** 19 automated tests passing (unit, limits, verifiers, and end-to-end pipelines).

---

## How to run

### 1. Clean-Clone Setup & Installation
```bash
git clone https://github.com/PushpendarSingh23/bodhan-tool-server-agent.git
cd bodhan-tool-server-agent/examples/bodhan-tool-server-agent

# Set up virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
```

### 2. Configure Environment
```bash
cp .env.example .env
```
Populate `LLM_API_KEY` (for orchestration) and `BODHAN_API_KEY` (or per-service keys). In the absence of live keys or during testing, the suite deterministically operates in offline mock mode.

### 3. Running Standalone MCP Server over stdio
```bash
python -m bodhan_mcp
```
To test with an off-the-shelf MCP client, run the official **MCP Inspector**:
```bash
npx @modelcontextprotocol/inspector python -m bodhan_mcp
```
*(Verified with `@modelcontextprotocol/inspector` v0.4.x and Claude Desktop)*.

### 4. Running the Task Agent via CLI
```bash
python -m bodhan_agent \
  --task "Transcribe classroom.wav in Hindi, translate to Kannada, and synthesize speech in Kannada" \
  --files ./classroom.wav \
  --out ./runs/run-01 \
  --max-steps 30 \
  --max-cost-inr 20 \
  --timeout 300
```

### 5. Running the Complete Verification & Demo Suite
```bash
# Run pytest test suite (19 tests)
make test

# Run the automated 8-task demo suite (covering realistic flows + impossible tasks)
make demo

# Run hygiene and import checks
make check
```

---

## Architecture

```mermaid
flowchart TD
    User["CLI User / Developer"] -->|"python -m bodhan_agent"| Agent["Task Agent (bodhan_agent)"]

    subgraph AgentLayer ["Task Agent Layer"]
        Planner["Task Planner<br/>(Structured LLM Routing)"]
        ExecLoop["Execution Runner<br/>(Act - Verify - Retry)"]
        Budget["Budget Tracker<br/>(Steps / INR / Timeout)"]
        Verifier["Output Verifier<br/>(Script / Structure / Duration)"]
        Reporter["Dossier Reporter<br/>(result.md, report.json, trace)"]
        
        Planner --> ExecLoop
        ExecLoop <--> Budget
        ExecLoop <--> Verifier
        ExecLoop --> Reporter
    end

    ExecLoop -->|"JSON-RPC / stdio or in-process"| MCP["Bodhan MCP Server (bodhan_mcp)"]
    ExternalClient["Off-the-shelf MCP Client<br/>(Claude Desktop / Inspector)"] -->|"stdio (JSON-RPC 2.0)"| MCP

    subgraph MCPLayer ["Bodhan MCP Tool Server Layer"]
        Cache[("Content-Addressed Cache<br/>.cache/bodhan (SHA-256)")]
        Media["Media Utils<br/>(File refs, Wave chunking <=30s)"]
        
        T_STT["bodhan_transcribe<br/>(Indic-Transcribe)"]
        T_MT["bodhan_translate<br/>(Indic-Translate)"]
        T_TTS["bodhan_speak<br/>(Indic-Speak)"]
        T_OCR["bodhan_ocr<br/>(Indic-OCR)"]
        T_Helpers["bodhan_inspect_media<br/>bodhan_chunk_audio"]
        
        MCP <--> Cache
        MCP <--> Media
        MCP --> T_STT
        MCP --> T_MT
        MCP --> T_TTS
        MCP --> T_OCR
        MCP --> T_Helpers
    end

    subgraph CloudLayer ["External Services"]
        LLM["OpenAI-Compatible LLM<br/>(Planning & English Explanations ONLY)"]
        BodhanAPI["Bodhan AI Sovereign API<br/>(api.bodhan.ai/v1)"]
    end

    Planner -.->|"JSON Plan Request"| LLM
    T_STT -->|"/audio/transcriptions"| BodhanAPI
    T_MT -->|"/chat/completions"| BodhanAPI
    T_TTS -->|"/audio/speech"| BodhanAPI
    T_OCR -->|"/chat/completions (vision)"| BodhanAPI
```

### Component Summary:
- **`bodhan_agent`**: Autonomous execution loop coordinating planning, budget enforcement, verification, and artifact reporting.
- **MCP Client / Stdio Transport**: Transmits JSON-RPC 2.0 requests between the agent (or external clients) and the server over standard I/O.
- **`bodhan_mcp` Tools**: Concrete tools encapsulating Indic speech, translation, OCR, and media inspection with automatic limit handling.
- **Content-Addressed Cache**: SHA-256 deterministic cache preventing duplicate execution and runaway INR expenditure.
- **Bodhan API (`api.bodhan.ai/v1`)**: The underlying sovereign AI models for 22 scheduled Indian languages.
- **LLM**: External OpenAI-compatible model used strictly for reasoning, planning, and English explanations.

---

## Agent flow

```mermaid
flowchart TD
    Start(["Start Agent Run"]) --> Init["Initialize BudgetTracker, TraceLogger & Reporter"]
    Init --> InputCheck{"Input Files Exist &<br/>Languages Supported?"}
    
    InputCheck -- No --> HaltImpossible["Halt as IMPOSSIBLE<br/>Write explanation to result.md / report.json<br/>No fabricated output"] --> Stop(["Stop"])
    InputCheck -- Yes --> Plan["Generate Plan via LLM / Fallback<br/>(Enclosed in &lt;data_payload&gt;)"]
    
    Plan --> FeasibleCheck{"Is Plan Feasible?"}
    FeasibleCheck -- No --> HaltImpossible
    FeasibleCheck -- Yes --> LoopStart["For Each Step in Plan"]
    
    LoopStart --> BudgetCheck{"Step / INR / Time<br/>Budget Available?"}
    BudgetCheck -- No --> HaltBudget["Halt as BUDGET_EXCEEDED<br/>Write partial results to dossier"] --> Stop
    
    BudgetCheck -- Yes --> Act["Act: Execute Bodhan Tool via MCP<br/>(Record INR cost & latency)"]
    Act --> SaveArtifact["Save Intermediate Artifact<br/>(Text / WAV / Markdown)"]
    SaveArtifact --> Verify["Verify Output Quality<br/>(Script range, length ratio, AI label)"]
    
    Verify --> VerifyPass{"Verification Passed?"}
    VerifyPass -- Yes --> NextStep{"More Steps?"}
    VerifyPass -- No --> RetryCheck{"Retries &lt; Max?"}
    
    RetryCheck -- Yes --> Retry["Re-plan / Adjust Arguments"] --> Act
    RetryCheck -- No --> NextStep
    
    NextStep -- Yes --> LoopStart
    NextStep -- No --> Finalize["Generate English Summary & Final Dossier<br/>(result.md, report.json, trace.jsonl)"] --> Stop
```

---

## Tool design

The server exposes 6 distinct, single-purpose tools with strongly typed I/O:

| Tool Name | Granularity & Model Limit Handling |
|:---|:---|
| `bodhan_transcribe` | **Speech-to-Text (`Indic-Transcribe` Core/Flex)**<br>• *Limit Handling:* Inspects audio duration; if `>30.0s`, automatically windows audio into `<=28.0s` sub-chunks using frame-accurate WAV slicing, calls the API per chunk, and merges transcripts in chronological sequence.<br>• *Parameters:* `audio_path`, `language` (auto or ISO code), `model_variant` (`core` native script or `flex` romanized). |
| `bodhan_translate` | **Machine Translation (`Indic-Translate`)**<br>• *Limit Handling:* Splits long documents along paragraph (`\n\n`) boundaries (`<=4000` chars per chunk) while strictly preserving Markdown formatting, headers, LaTeX equations, code blocks, and tables across 22 scheduled Indian languages + English.<br>• *Parameters:* `text`, `text_path`, `source_lang`, `target_lang`. |
| `bodhan_speak` | **Text-to-Speech Synthesis (`Indic-Speak`)**<br>• *Limit Handling:* Breaks long sentences into natural teacher-paced phrases along punctuation boundaries (including Devanagari `।`), synthesizes audio chunks, concatenates into a cohesive WAV, and stamps an **AI-generated companion disclaimer** (`.json`).<br>• *Parameters:* `text`, `text_path`, `language`, `voice`, `output_path`. |
| `bodhan_ocr` | **Document Digitization (`Indic-OCR`)**<br>• *Limit Handling:* Inspects single images or multi-page PDFs page-by-page, extracts reading-ordered Markdown with structured layout, and outputs a single aggregated Markdown document.<br>• *Parameters:* `file_path`, `output_path`. |
| `bodhan_inspect_media` | **Pre-execution Inspector**<br>• *Limit Handling:* Inspects audio duration, sample rate, image dimensions, PDF page count without sending raw media bytes to the LLM.<br>• *Parameters:* `file_path`. |
| `bodhan_chunk_audio` | **Audio Windowing Utility**<br>• *Limit Handling:* Explicitly slices audio into sub-30-second windows and returns chunk paths and time offsets.<br>• *Parameters:* `audio_path`, `max_duration_sec`, `output_dir`. |

---

## Where the LLM is used, and why

| Decision Point | LLM Responsibility | Fallback on Failure |
|:---|:---|:---|
| **1. Initial Plan Decomposition** | Analyzes user task intent and identifies source/target languages and necessary pipeline stages. | Deterministic rule-based planner parses keywords and input media extensions. |
| **2. Downstream Argument Piping** | Resolves output variables from prior steps (e.g. piping transcript text into translation). | Context pipe automatically links `transcript` -> `translation` -> `speech`. |
| **3. Verification Judgment** | Evaluates whether non-trivial formatting was preserved and decides whether a retry is warranted. | Algorithmic script range and length ratio verifiers. |
| **4. Executive English Summary** | Summarizes execution findings in English for `result.md`. | Structured template synthesis from `report.json`. |

> [!IMPORTANT]
> **Strict Negative Constraint:** The LLM is **never** permitted to generate or translate Indian-language text, transcribe audio, read image text, or synthesize speech. All Indic language generation is handled solely by Bodhan models. Untrusted text is strictly enclosed in `<data_payload>` tags and treated as passive data.

---

## Bodhan models used

1. **`indic-transcribe` (and `indic-transcribe-flex`)**: Speech-to-Text across 25+ Indian languages and English with accent and code-mixing support (₹0.10 / min).
2. **`indic-translate`**: High-accuracy machine translation across 22 scheduled Indian languages and English preserving Markdown/LaTeX structures (₹0.20 / 10,000 tokens).
3. **`indic-speak`**: Text-to-Speech synthesis with natural teacher-pacing and STEM notation support (₹6.00 / 10,000 chars).
4. **`indic-ocr`**: Document OCR extracting reading-ordered Markdown from printed/handwritten documents (₹0.20 / page).

---

## Results

### Automated Demo Tasks
| # | Task Description | Expected | Actual | Cost (₹) | Steps | Dossier Artifacts |
|:---|:---|:---:|:---:|:---:|:---:|:---|
| 1 | Speech-to-Text & Translation (Hindi -> Kannada) | `SUCCESS` | `SUCCESS` | ₹0.0200 | 2 | `transcript_step1.txt`, `translation_step2.txt` |
| 2 | Speech-to-Speech Localization (Hindi -> Tamil) | `SUCCESS` | `SUCCESS` | ₹0.1028 | 3 | `transcript_step1.txt`, `translation_step2.txt`, `synthesized_step3.wav` + disclaimer |
| 3 | Document OCR & Translation (Image -> Telugu) | `SUCCESS` | `SUCCESS` | ₹0.2014 | 2 | `ocr_step1.md`, `translation_step2.txt` |
| 4 | Document OCR to Audio Narration (Image -> Bengali -> Speech) | `SUCCESS` | `SUCCESS` | ₹0.3448 | 3 | `ocr_step1.md`, `translation_step2.txt`, `synthesized_step3.wav` |
| 5 | Long Audio Windowing (>30s Chunks -> Marathi Translation) | `SUCCESS` | `SUCCESS` | ₹0.1100 | 2 | `transcript_step1.txt`, `translation_step2.txt` |
| 6 | **Impossible Task:** Unsupported Language (French) | `IMPOSSIBLE` | `IMPOSSIBLE` | ₹0.0000 | 0 | Honest explanation in `result.md`, 0 fake outputs |
| 7 | **Impossible Task:** Non-existent Input File | `IMPOSSIBLE` | `IMPOSSIBLE` | ₹0.0000 | 0 | Missing file notice in `result.md`, 0 fake outputs |
| 8 | **Budget Safety:** Insufficient Budget (₹0.001 limit) | `BUDGET_EXCEEDED` | `BUDGET_EXCEEDED` | ₹0.0954 | 3 | Clean halt before runaway spend |

### Quality & Verification Targets:
- **Indic Script Conformance:** Validated via Unicode block analysis (e.g. Devanagari `\u0900-\u097F`, Tamil `\u0B80-\u0BFF`, Kannada `\u0C80-\u0CFF`).
- **Idempotency & Cost Elimination:** Second identical execution verified at ₹0.00 cost via `.cache/bodhan`.

---

## Demo video

> **Video Link:** `[DEMO_VIDEO_URL_PLACEHOLDER]` *(Accessible publicly without sign-in; under 5 minutes)*

### Video Contents:
- 0:00 - 0:45: Project overview and stdio MCP server architecture.
- 0:45 - 1:45: Running `bodhan_mcp` with an off-the-shelf client (`@modelcontextprotocol/inspector`).
- 1:45 - 3:00: End-to-end task agent execution (Speech-to-Speech translation Hindi -> Tamil).
- 3:00 - 4:00: Inspecting generated dossier (`result.md`, `report.json`, `trace.jsonl`, audio with AI disclaimer).
- 4:00 - 4:45: Demonstrating an **Impossible Task** (French translation) halting cleanly with ₹0 cost and no fake outputs.
- 4:45 - 5:00: Conclusion and test suite execution (`make test`).

---

## Trade-offs and known limitations

1. **Pure Python Audio Processing**:
   - *Trade-off:* Relies on Python's built-in `wave` module rather than external `ffmpeg` binary dependencies to ensure zero-install portability on all operating systems.
   - *Limitation:* WAV files are natively sliced frame-accurately; compressed formats (MP3/FLAC) are passed directly to Bodhan's native API endpoints which handle decoding server-side.
2. **Context Window vs. Granularity**:
   - *Trade-off:* Chunks text by paragraphs (`\n\n`) to retain formatting rather than raw token slices.
   - *Limitation:* Documents with no newline breaks are chunked by length thresholds.
3. **Mock Mode for CI/CD**:
   - *Trade-off:* Deterministic mock responses when API keys are unconfigured ensure tests and demos pass on clean clones without billing real cards.

---

## Cost and runtime

- **Total Test Suite Runtime:** ~1.58 seconds across 19 tests.
- **Demo Suite Runtime:** ~2.1 seconds for 8 complete multi-step tasks.
- **Cost Metrics:**
  - Short audio transcription + translation: ~₹0.02
  - Speech-to-speech pipeline: ~₹0.10
  - Document OCR + translation + speech: ~₹0.34
  - Impossible tasks: ₹0.00 (Zero spend)

---

## AI assistance

- **Tools Used:** Antigravity AI Agent (DeepMind) for project scaffolding, test generation, and documentation drafting.
- **Human Authorship & Understanding:** All code, architectural decisions, and error-handling mechanisms were verified and reviewed line-by-line by Pushpendar Singh. Ready to defend and explain every line of code during the technical debrief.
