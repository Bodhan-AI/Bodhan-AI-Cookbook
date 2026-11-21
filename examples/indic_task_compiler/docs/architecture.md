# Architecture — Indic Task Compiler

```mermaid
flowchart TB
    CLI["CLI: python -m bodhan_agent<br/>(--task, --files, --out, --max-steps, --max-cost-inr)"]

    subgraph AGENT["bodhan_agent (Task Agent)"]
        direction TB
        INTAKE["Intake (code)<br/>Resolve paths, free inspect_files -> FileFacts"]
        PLLM["Privileged Planner (P-LLM)<br/>Sees ONLY --task + FileFacts + ProbeFacts + $handles<br/>NEVER sees file/OCR/ASR/translation text"]
        GATE["Feasibility Gate (code, ₹0)<br/>Language matrix, file kinds, provable cost floor"]
        COMPILER["Plan Compiler (code)<br/>MCP schema check, foreach expand, scope lock,<br/>Indic-literal block, DAG -> parallel waves"]
        EXEC["Wave Executor + BudgetTracker<br/>Pre-call INR reservation, step cap, asyncio.gather"]
        QLLM["Quarantined Reader (Q-LLM, no tools)<br/>q_summarize_en / q_classify / q_extract_number<br/>English-only + schema-validated"]
        STORE["ArtifactStore + Provenance Guard<br/>Splits meta vs content; verifies Indic byte lineage"]
        CONTRACTS["Contracts Verifier (code)<br/>Duration ±20%, script >=0.8, CER, repetition,<br/>table shape, digits, monotonic SRT"]
        WRITERS["Writers<br/>result.md, artifacts/, trace.jsonl, report.json, trace.html"]
    end

    subgraph MCP["bodhan_mcp (Standalone MCP Server: stdio | --http --token)"]
        direction TB
        TOOLS["9 Capability Tools<br/>inspect_files, detect_language, transcribe_audio,<br/>translate_text, synthesize_speech, verify_speech,<br/>ocr_document, transliterate_text, write_subtitles"]
        RES["MCP Resources & Prompt<br/>bodhan://languages, bodhan://voices,<br/>bodhan://pricing, plan_task"]
        HELPERS["Media & Text Helpers<br/>ffmpeg/ffprobe, silencedetect <=28s, pypdfium2,<br/>Pillow, Markdown table chunker, SRT/VTT"]
        CLIENT["BodhanClient (httpx)<br/>Per-model keys, Semaphore(4), backoff+jitter,<br/>SHA-256 DiskCache, Cost Meter, DevSpendLedger"]
    end

    API["Bodhan Hosted API (https://api.bodhan.ai)<br/>indic-transcribe | indic-translate | indic-speak | indic-ocr | indic-transliterate"]

    CLI --> INTAKE --> PLLM
    PLLM --> GATE
    GATE --> COMPILER --> EXEC
    EXEC <--> QLLM
    EXEC <--> STORE
    EXEC --> CONTRACTS
    CONTRACTS --> WRITERS
    EXEC <-->|"MCP stdio (preview stripped at client boundary)"| TOOLS
    TOOLS --> HELPERS --> CLIENT --> API
    RES -.->|"Schema & capability metadata"| PLLM
```

## Component responsibilities

- **`bodhan_mcp.server` (`python -m bodhan_mcp`)**: Standalone MCP server over stdio (or optional Streamable HTTP with Bearer auth). Returns bounded untrusted previews (<=300 chars) for third-party MCP clients.
- **`bodhan_mcp.tools`**: 9 capability-level tools that absorb all model limits internally (<=28 s silence-aware audio windows, table-preserving translation chunking + `truncated` recovery, <=250-char sentence TTS chunking + loudness normalization + AI-generated metadata tags, PDF rasterization via `pypdfium2` + 502 OCR recovery).
- **`bodhan_mcp.client` & `cache`**: Async `httpx` transport with per-model key resolution (`BODHAN_*_KEY` -> `BODHAN_API_KEY`), exponential backoff + jitter on 429/5xx/timeouts, single 401 retry for previously valid keys, concurrency semaphore, and SHA-256 content-addressed disk caching.
- **`bodhan_agent.planner` (`P-LLM`)**: Privileged planner operating in three epochs (Epoch A `TaskSpec`, Epoch B flat step list, Epoch C targeted recovery). Strictly isolated from untrusted file/OCR/transcript/translation text.
- **`bodhan_agent.gate` & `compiler`**: Deterministic Python checks that reject provably impossible tasks at ₹0 and statically validate tool names, MCP JSON Schemas, `$step.field` references, input/output/language scope locks, and dependency waves before spending a rupee.
- **`bodhan_agent.quarantined` (`Q-LLM`)**: Tool-less reader for untrusted English content (`q_summarize_en`, `q_classify`, `q_extract_number`). Enforces English-only input/output and strict enum/numeric validation.
- **`bodhan_agent.store` & `contracts`**: Splits tool responses into metadata vs file handles, tracks SHA-256 provenance of every Indic output so no LLM-authored Indic text can ever be published, and verifies per-deliverable contracts.
