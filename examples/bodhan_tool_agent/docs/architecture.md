# Architecture: Bodhan MCP Tool Server & Autonomous Task Agent

## High-Level System Architecture

```mermaid
flowchart TD
    User([User / MCP Client]) -->|CLI Flags / Task Prompt| Agent[Bodhan Task Agent]
    User -->|STDIO / HTTP JSON-RPC| MCP[Bodhan FastMCP Server]

    subgraph Bodhan Agent Core
        Inspector[File Inspector & Security Sanitizer]
        Planner[Task Planner & Dependency DAG]
        LLM[OpenAI-Compatible LLM Adapter]
        Executor[Plan Executor & Concurrency Engine]
        Budget[Budget & Cost Manager]
        Cache[SHA-256 Result Cache]
        Verifier[Multi-Vector Verification Engine]
        Checkpoint[State Checkpoint Manager]
        Trace[Structured Event Trace Logger]
    end

    subgraph Bodhan MCP Tool Server
        T1[transcribe_audio]
        T2[translate_text]
        T3[synthesize_speech]
        T4[extract_text_from_document]
        Res[MCP Resources & Prompts]
    end

    subgraph Bodhan Cloud AI APIs
        ASR[POST /v1/audio/transcriptions<br/>indic-transcribe]
        MT[POST /translate<br/>indic-translate]
        TTS[POST /v1/audio/speech<br/>indic-speak]
        OCR[POST /v1/chat/completions<br/>indic-ocr]
    end

    Agent --> Inspector
    Inspector --> Planner
    Planner <--> LLM
    Planner --> Executor
    Executor --> Budget
    Executor --> Cache
    Executor --> Checkpoint
    Executor --> T1 & T2 & T3 & T4
    T1 --> ASR
    T2 --> MT
    T3 --> TTS
    T4 --> OCR
    Executor --> Verifier
    Executor --> Trace
    Trace --> Out[out/result.md<br/>out/trace.jsonl<br/>out/report.json<br/>out/artifacts/]
```

## Architectural Design Principles

1. **Decoupled Tool Services**: The MCP Server and the Agent share the same resilient tool implementations (`bodhan_mcp.tools.*`), ensuring identical validation, chunking, and error handling whether invoked via CLI or by external MCP desktop clients.
2. **Deterministic Fallbacks**: While the companion LLM provides task reasoning and English summarization, all Indic translation, transcription, speech, and OCR operations are strictly routed through official Bodhan models.
3. **Defense-in-Depth Security**: File paths are validated against boundary traversal, extracted text is scanned for prompt injection markers, and credentials are automatically redacted from execution logs.
4. **Idempotency & Cost Defense**: Input files and arguments are hashed using SHA-256 to eliminate redundant API calls. Budgets are checked prior to every step execution.
