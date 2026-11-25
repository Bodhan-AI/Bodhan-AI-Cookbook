# Architecture

How the MCP server, its tools and the agent connect at run time. The agent loop is in
[agent-flow.md](agent-flow.md); design reasoning is in [design-notes.md](design-notes.md).
Diagrams are Mermaid (rendered by GitHub).

```mermaid
flowchart LR
    user["CLI<br/>python -m bodhan_agent<br/>--task --files --out"] --> agent

    subgraph AP["Agent process (bodhan_agent)"]
        agent["LangGraph loop<br/>plan-step, call-tools,<br/>verify, finish<br/>(flow: agent-flow.md)"]
        guard["budgets, timeouts,<br/>injection screen, verification<br/>(plain Python)"]
        rec["RunRecorder<br/>trace.jsonl, report.json, result.md"]
        offline["bodhan_core offline helpers<br/>file inventory, script language ID<br/>(no network)"]
        agent --- guard
        agent --> rec
        guard -.-> offline
    end

    llm[("LLM<br/>LLM_BASE_URL / LLM_MODEL<br/>OpenAI-compatible, tool calling")]
    agent <-->|"sends task, tool schemas, short results, paths<br/>gets back tool calls"| llm

    agent <==>|"MCP: stdio, child process (default)<br/>or streamable HTTP + bearer token<br/>arguments and results carry file paths"| srv

    subgraph SP["MCP server process (bodhan_mcp), also runs on its own"]
        srv["FastMCP tool registry<br/>typed in/out, structured errors"]
        srv --> T1["transcribe_audio"]
        srv --> T2["translate_text"]
        srv --> T3["speak_text"]
        srv --> T4["ocr_document"]
        srv --> T5["make_subtitles"]
        srv --> T6["inspect_media, detect_language,<br/>read_text_file, write_text_file"]
        srv -.-> R["resources: bodhan://voices, bodhan://languages<br/>prompts: translate_document, ..."]
        subgraph LIB["bodhan_core (library, in-process)"]
            c["chunking, 30 s audio windows,<br/>retry + backoff, concurrency limit,<br/>result cache, cost ledger"]
        end
        T1 & T2 & T3 & T4 & T5 --> c
    end

    c -->|"HTTPS, one key per model"| api["Bodhan API<br/>api.bodhan.ai/v1<br/>Indic-Transcribe, -Translate,<br/>-Speak, -OCR"]

    disk[("Disk<br/>input files, out/work, out/artifacts")]
    T1 & T2 & T3 & T4 & T5 & T6 <-.->|"read / write media<br/>and long text by path"| disk
```

**Legend.** Thick arrow: the process boundary (MCP). Solid arrows: calls. Dotted arrows to disk: media and long text,
which never pass through the LLM. Text over 3,000 characters (`output.inline_chars` in `config/config.yaml`) is saved to
a file and the tool returns its path. English text also gets a 600-character preview; Indian-language text gets none, so
the LLM cannot copy a fragment of it.

| Part | Role |
|---|---|
| Agent process | CLI and LangGraph loop. The LLM plans. Plain Python enforces budgets and timeouts, screens untrusted text, verifies outputs and writes the trace. Uses `bodhan_core` only for offline checks; it never calls the Bodhan API directly. |
| MCP server process | `bodhan_mcp`: one tool per Bodhan model plus helpers. The agent spawns it over stdio, and any other MCP client can drive it the same way. |
| `bodhan_core` | Library loaded inside the server: chunking, WAV windows, retries, concurrency limit, cache and cost ledger. Every Bodhan API call goes through it. |
| Bodhan API | The only place any speech-to-text, translation, speech synthesis or OCR happens. |
| Disk | Audio, images, PDFs and long text move between tools as paths. Only short text and paths reach the LLM. |
