# Architecture

```mermaid
flowchart TB
  subgraph CLI["python -m bodhan_agent --task … --files … --out …"]
    PRE[Preflight - plain code<br/>expand inputs · inspect_media · language names in task · injection scan]
    LOOP[Agent loop<br/>submit_plan → tool calls → finish]
    GUARD[Guards - plain code<br/>no Indian-script text from the LLM · path allowlist · agent-owned output_dir and budget · memoised duplicate calls]
    BUD[Budget<br/>steps · rupees split across parallel calls · per-call timeout · wall clock]
    VER[Final verification - plain code<br/>every audio + translation · every deliverable]
    REP[Report<br/>status from facts · result.md · report.json]
    TR[(trace.jsonl<br/>every step + every LLM response)]
    LOCAL[Agent-local tools<br/>read_text_file · write_text_file English-only · finish]
  end

  LLM[(LLM<br/>OpenAI-compatible<br/>LLM_BASE_URL / LLM_MODEL / LLM_API_KEY)]

  subgraph MCP["bodhan_mcp - MCP server (stdio / streamable HTTP + token / in-process)"]
    direction TB
    TOOLS[7 tools · typed in/out schemas · JSON errors with hints<br/>3 resources · 1 prompt]
    SVC[BodhanService<br/>model limits: 30 s windows · TTS pieces · translation chunks · PDF pages<br/>verification: back-transcription · CER · script · numbers · tables]
    MEDIA[Media plumbing<br/>ffmpeg decode/encode · energy VAD · pypdfium2 · Pillow]
    CLIENT[BodhanClient<br/>retries + backoff + Retry-After · per-model concurrency limit<br/>timeouts · content-addressed cache · cost ledger]
  end

  API[(api.bodhan.ai<br/>indic-transcribe · indic-translate · indic-speak · indic-ocr)]
  FILES[/input files/]
  OUT[/out: result.md · artifacts/ · trace.jsonl · report.json/]

  PRE --> LOOP
  LOOP <--> LLM
  LOOP --> GUARD --> TOOLS
  GUARD --> LOCAL
  BUD -. max_cost_inr per call .-> TOOLS
  LOOP --> VER --> REP --> OUT
  LOOP --> TR
  TOOLS --> SVC --> MEDIA
  SVC --> CLIENT --> API
  FILES -. by path .-> SVC
  SVC -. outputs by path .-> OUT
```

| Part | Responsibility |
|---|---|
| `bodhan_mcp/server.py` | MCP surface: tool schemas and descriptions written for third-party planners, annotations, resources, a prompt, and stdio and HTTP+token transports. It maps every failure to a structured error and never crashes the client. |
| `bodhan_mcp/service.py` | One coroutine per tool. Handles model limits, chunking, recovery and per-tool verification. |
| `bodhan_mcp/client.py` | The only code that talks to Bodhan: auth per model, retries, concurrency, timeouts, cache and cost. |
| `bodhan_mcp/{audio,documents,text,languages}.py` | Deterministic plumbing: VAD windowing, PDF rasterising, chunkers, script detection, CER. |
| `bodhan_agent/agent.py` | Preflight, the plan → act → finish loop, parallel execution, budget enforcement and finish validation. |
| `bodhan_agent/toolbox.py` | MCP client to the server, agent-local tools, guards, memoisation and context trimming. |
| `bodhan_agent/llm.py` | OpenAI-compatible client with retries and a parameter / JSON-tool-protocol fallback, plus a scripted LLM for replay and tests. |
| `bodhan_agent/verify.py`, `report.py`, `trace.py` | Final checks, the status decision, result.md / report.json, and the JSONL trace. |
