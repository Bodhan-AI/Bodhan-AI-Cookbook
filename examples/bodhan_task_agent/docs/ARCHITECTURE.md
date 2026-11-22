# Architecture and workflow

## Components

```mermaid
flowchart TD
    U["User task + input files"] --> S["Stage files into isolated workspace"]
    S --> A["Task agent / LLM tool calling"]
    A --> H["Shared typed ToolHub"]
    H --> V["Validate paths, languages, inputs, budget"]
    V --> C["Exact-input cache"]
    C -->|cache miss| B["Bodhan HTTPS gateway"]
    C -->|cache hit| Q["Results + QA"]
    B --> O["Indic-OCR"]
    B --> R["Indic-Transcribe"]
    B --> T["Indic-Translate"]
    B --> P["Indic-Speak"]
    O --> Q
    R --> Q
    T --> Q
    P --> Q
    Q --> A
    A --> F["result.md + artifacts + trace.jsonl + report.json"]
    M["Independent MCP client"] --> X["bodhan_mcp stdio server"]
    X --> H
```

## Agent loop and recovery

```mermaid
flowchart TD
    A["Read user task and inspect files"] --> B["LLM selects next tool or final answer"]
    B --> C{"Tool requested?"}
    C -->|no| D{"Real artifacts exist?"}
    D -->|yes| E["Write final report and status"]
    D -->|no| Z["Write not_completed; never fabricate output"]
    C -->|yes| F{"Path, tool args and budget valid?"}
    F -->|no| G["Return structured error"]
    F -->|yes| H{"Cached response available?"}
    H -->|yes| J["Use cache"]
    H -->|no| I["Bodhan call, bounded retries"]
    I --> K{"Output valid?"}
    K -->|yes| J
    K -->|no| G
    J --> L["QA: numbers, LaTeX, backtranslation, back-ASR"]
    L --> B
    G --> M{"Budget or step ceiling?"}
    M -->|yes| N["Stop with partial/failed report"]
    M -->|no| B
```

## Boundary of responsibility

- **LLM:** infer user intent, plan, choose typed tools, write *English* summaries and explanations, decide which errors are recoverable.
- **Bodhan models:** all OCR, ASR, translation and TTS. Indian-language content is never generated or rewritten by the LLM.
- **Deterministic Python:** media conversion, document page rasterization, chunk boundaries, timestamped subtitles, normalization, cache hashes, token checks, path validation and report emission.
- **MCP client:** may call the same validated model tools independently of the agent; the server prints only JSON-RPC to stdout.
- **Untrusted sources:** OCR text, transcripts, filenames and source documents are content, not instructions.
