# Architecture

```mermaid
flowchart LR
    subgraph CLI["python -m bodhan_agent"]
        PRE["Pre-flight (code)<br/>inspect inputs, capabilities,<br/>cost estimate"]
        LOOP["Agent loop<br/>plan → act → verify → finish"]
        GUARD["Guards (code)<br/>no LLM-written Indic text,<br/>untrusted-content wrapper,<br/>artifact provenance + verification gate"]
        BUD["Budgets (code)<br/>steps · ₹ · time · per-call timeout"]
        REP["Reports (code)<br/>result.md · report.json"]
        TR["trace.jsonl"]
    end

    LLM[("LLM<br/>OpenAI-compatible<br/>LLM_BASE_URL / LLM_MODEL")]

    subgraph SRV["python -m bodhan_mcp  (MCP server, stdio or HTTP+token)"]
        direction TB
        T1["inspect_files · list_capabilities<br/>read_text · make_subtitles<br/><i>free, plain code</i>"]
        T2["transcribe_audio<br/><i>decode · VAD chunks &lt;30 s · timestamps</i>"]
        T3["translate_text<br/><i>Markdown/segments/SRT aware · sentence chunks · truncation re-split</i>"]
        T4["synthesize_speech<br/><i>1–2 sentence chunks · join · back-transcribe verify · repair</i>"]
        T5["ocr_document<br/><i>PDF→pages · blank skip · 502 retry</i>"]
        CL["BodhanClient<br/>retries+backoff · semaphore · content-hash cache ·<br/>in-flight de-dup · per-call ledger (₹)"]
        T2 & T3 & T4 & T5 --> CL
    end

    API[("api.bodhan.ai<br/>Indic-Transcribe · Indic-Translate<br/>Indic-Speak · Indic-OCR")]
    FS[("Files on disk<br/>inputs · out/work · out/artifacts")]
    EXT["Any MCP client<br/>(Inspector, Claude Desktop, …)"]

    PRE --> LOOP
    LOOP <--> LLM
    LOOP -- "MCP tools/call over stdio<br/>(paths, never base64)" --> SRV
    LOOP --> GUARD --> REP
    BUD -. limits .-> LOOP
    LOOP --> TR
    CL <--> API
    SRV <--> FS
    EXT -- MCP --> SRV
```

| Part | One line |
|---|---|
| `bodhan_mcp.server` | Registers 8 tools, 3 resources and 1 prompt on an MCP server; stdio by default, streamable HTTP with a bearer token as an option. |
| `bodhan_mcp.tools.*` | One module per capability. Each hides its model's limits and returns typed results with file paths, previews, checks and cost. |
| `bodhan_mcp.client` | The only code that calls Bodhan: retries, backoff, concurrency limit, cache, de-duplication, budget ledger. |
| `bodhan_mcp.media` / `text` | Plain-code plumbing: ffmpeg decode, voice-activity chunking, WAV joins, PDF rendering, script detection, Markdown structure. |
| `bodhan_agent.mcp_bridge` | A real MCP client: the agent uses the server exactly like a third party would. |
| `bodhan_agent.agent` | The loop, budgets, argument injection, guards, verification gate, finish validation. |
| `bodhan_agent.llm` | Portable OpenAI-compatible tool calling with fallbacks for providers that lack named `tool_choice`. |
| `bodhan_agent.report` / `trace` | `result.md`, `report.json`, and a flushed-per-step `trace.jsonl`. |
