# Architecture

```mermaid
graph TB
  subgraph AGENT["python -m bodhan_agent"]
    CLI["cli.py<br/>args, run folder, final status"] --> IN["intake.py<br/>resolve files, one probe"]
    IN --> PL["planner.py<br/>LLM to JSON plan, one repair"]
    PL --> EX["executor.py<br/>price, reserve, run, settle"]
    EX --> VF["verifier.py<br/>L1 free checks, L2 back-transcription"]
    VF --> RP["reporters.py<br/>result.md, report.json"]
    TR["trace.py<br/>trace.jsonl"] -.-> EX
  end

  LLM[("OpenAI-compatible LLM<br/>LLM_BASE_URL")]
  PL <--> LLM
  EX -->|write_text, English only| LLM

  subgraph SERVER["python -m bodhan_mcp (stdio, or streamable HTTP + bearer)"]
    SV["server.py<br/>registration only"]
  end

  subgraph TOOLS["bodhan_mcp.tools: one implementation, two entry points"]
    T1[probe_media]
    T2[detect_language]
    T3[transcribe_audio]
    T4[ocr_document]
    T5[translate_text]
    T6[speak_text]
  end

  subgraph CORE["bodhan_mcp.core"]
    C1["client.py<br/>auth per model, retry, concurrency"]
    C2["api.py<br/>request/response shapes"]
    C3["cache.py + costs.py"]
    C4["media.py + langid.py + text.py"]
  end

  MCPC["Any MCP client"] <-->|MCP| SV
  SV --> TOOLS
  EX -->|direct import| TOOLS
  TOOLS --> CORE
  C1 --> API[("api.bodhan.ai<br/>indic-transcribe / translate / speak / ocr")]

  WV["python -m bodhan_agent.webui<br/>read-only run viewer"]
  RP -.->|"result.md, report.json, trace.jsonl"| WV
```

| Part | Responsibility |
|---|---|
| `bodhan_mcp.server` | Registers the six tools and three resources (`bodhan://languages`, `voices`, `pricing`). No behaviour. |
| `bodhan_mcp.tools` | The only implementation of each capability. Validates input, absorbs model limits, raises structured errors. |
| `bodhan_mcp.core` | HTTP path, per-model keys, retries, cache, price estimators, ffmpeg/PDF helpers, script-based language ID. |
| `bodhan_agent` | Plans with an LLM, then executes in Python under a step, cost and timeout budget; verifies; writes the run folder. |

The agent imports the tool functions directly. The server runs on its own and
is what an external MCP client sees. Because both use the same functions, they
cannot drift apart.

## The one invariant

Indian-language text is never a string the agent holds. Every tool takes and
returns file paths; the LLM sees paths, counts, language codes, durations and
costs. Prompt injection inside a scanned page is therefore inert: the text is
transcribed or translated into a file and never reaches a prompt.
