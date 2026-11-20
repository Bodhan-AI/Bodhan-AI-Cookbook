# Architecture

```mermaid
flowchart LR
    subgraph CLI["python -m bodhan_agent"]
        A1[Inventory<br/>code] --> A2[Planner LLM<br/>plan JSON]
        A2 --> A3[Act loop<br/>LLM tool calls]
        A3 --> A4[Guards<br/>code: budget, paths,<br/>provenance, untrusted text]
        A4 --> A3
        A3 --> A5[Verification gate<br/>code]
        A5 --> A6[result.md / report.json<br/>trace.jsonl / artifacts/]
    end

    LLM[(OpenAI-compatible LLM<br/>LLM_BASE_URL / LLM_MODEL / LLM_API_KEY)]
    A2 <--> LLM
    A3 <--> LLM

    subgraph MCP["python -m bodhan_mcp (stdio, or Streamable HTTP + token)"]
        direction TB
        T1[inspect_files] & T2[read_text] & T3[list_voices] & T4[make_subtitles]
        T5[detect_language]
        T6[transcribe_audio] & T7[translate_text] & T8[synthesize_speech] & T9[verify_speech] & T10[ocr_document]
        R[Resources: bodhan://voices, languages, pricing, usage<br/>Prompt: indic_task]
        M[media.py: ffmpeg decode, VAD chunking,<br/>WAV join, MP3 encode, PDF raster]
        F[formats.py: Markdown/HTML tables,<br/>segments JSON, SRT/VTT templates]
        C[bodhan_api.py: per-model keys, concurrency limit,<br/>retries + backoff, disk cache, in-flight dedup, cost ledger]
        T6 & T8 & T9 --> M
        T7 & T8 --> F
        T5 & T6 & T7 & T8 & T9 & T10 --> C
    end

    A4 <-->|MCP over stdio<br/>file paths, never base64| MCP
    EXT[Any MCP client<br/>e.g. MCP Inspector, Claude Desktop] <-->|stdio| MCP

    C -->|POST /v1/audio/transcriptions| B1[Indic-Transcribe]
    C -->|POST /translate| B2[Indic-Translate]
    C -->|POST /v1/audio/speech| B3[Indic-Speak]
    C -->|POST /v1/chat/completions| B4[Indic-OCR]
```

| Part | One line |
|---|---|
| `bodhan_agent` CLI | Takes `--task`, `--files`, `--out`; owns budgets, trace, guards and reporting. |
| Planner LLM | Reads the task and inventory, writes a JSON plan, picks tool calls, writes English text, judges results. |
| Guards (code) | Per-call cost caps, step/time limits, timeouts, path resolution, duplicate-call suppression, Indian-language provenance check, untrusted-text wrapping. |
| Verification gate (code) | At `finish`, checks every deliverable exists and passed its check (back-transcription, translation coverage); rejects or downgrades otherwise. |
| `bodhan_mcp` server | Ten tools, four resources and one prompt over stdio (or HTTP with a bearer token). Runs alone for any MCP client. |
| `bodhan_api.py` | The only code that talks to Bodhan. Keys, retries, cache, concurrency, exact cost. |
| `media.py` / `formats.py` | Plain-code media and document plumbing; no model calls. |

## How data moves

Media and long text never enter the LLM context. Every tool writes its result to a file and returns
the path plus a short preview; the next tool takes that path (`input_path`, `segments_path`,
`audio_path`). Images go to Indic-OCR as base64 only inside `bodhan_api.py`, which is the API's
required format; no tool accepts or returns base64.

Example: *Hindi voice note to English subtitles*

```mermaid
sequenceDiagram
    participant L as Planner LLM
    participant A as Agent (code)
    participant S as bodhan_mcp
    participant B as Bodhan API
    A->>S: inspect_files(inputs)            (free)
    A->>L: task + inventory -> plan
    L->>A: transcribe_audio(path, language=hi, segment_max_seconds=8)
    A->>S: + max_cost_inr cap
    S->>B: N windows <= 8 s, cut at pauses (cached, deduplicated)
    S-->>A: segments_path, transcript_path, preview
    L->>A: translate_text(input_path=segments_path, target=en)
    S->>B: batched lines, timings kept in the template
    S-->>A: output_path + verification {passed, truncated_units, script ratio}
    L->>A: make_subtitles(segments_path=<translated>)
    S-->>A: subtitle_path (.srt)
    L->>A: finish(deliverables=[srt])
    A->>A: gate: file exists, translation verified -> copy to artifacts/
```
