# Architecture

```mermaid
flowchart LR
  subgraph Agent["bodhan_agent (CLI)"]
    CLI[--task / --files / --out] --> Run[run.py: preflight · plan · act · verify]
    Run --> LLM[(Companion LLM\nLLM_BASE_URL)]
    Run --> Trace[trace.jsonl · report.json · result.md]
  end

  subgraph Server["bodhan_mcp (stdio MCP server)"]
    Tools[Tools: inspect_media · ocr_document · transcribe_audio\ntranslate_text · translate_segments · synthesize_speech · make_srt\nsupported_languages · list_voices]
  end

  subgraph Core["bodhan_core"]
    Client[HTTP client\nretries · backoff · concurrency limit]
    Cache[(SQLite cache)]
    Ledger[Cost ledger]
    Chunk[Chunkers]
    Langs[Languages · voices · script detection]
  end

  Run -- MCP over stdio --> Tools
  ThirdParty[Any MCP client] -- MCP over stdio --> Tools
  Tools --> Chunk & Langs & Client
  Client --> Cache & Ledger
  Client --> Bodhan[(api.bodhan.ai\nTranscribe · Translate · Speak · OCR)]
```

- **bodhan_agent:** plans and drives the task. It talks to the server over MCP, exactly like a third-party client.
- **bodhan_mcp:** typed tools with model limits handled internally. Media is passed by file path.
- **bodhan_core:** shared plumbing (HTTP, cache, cost, chunking, language data) with no MCP or LLM dependency.
