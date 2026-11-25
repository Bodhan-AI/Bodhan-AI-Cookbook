# Architecture

```mermaid
flowchart LR
    user(["User / grader<br/>CLI: --task --files --out"])
    ext(["Any MCP client<br/>Inspector, Claude Desktop, ..."])
    llm[("OpenAI-compatible LLM<br/>LLM_BASE_URL / LLM_MODEL")]

    subgraph AGENT["bodhan_agent  (python -m bodhan_agent)"]
        direction TB
        pre["Preflight (code)<br/>missing input? unsupported target language?<br/>inspect every input"]
        loop["Agent loop<br/>plan, act, verify, retry, finish"]
        guards["Deterministic guards (plain code)<br/>budget: steps, rupees, time, per-call timeout<br/>pre-spend refusal, in-flight cost reservation<br/>duplicate-call memo, replan rule<br/>Indic-text rule, injection data blocks<br/>forced verification gate"]
        out[/"out/<br/>result.md, artifacts/<br/>trace.jsonl, report.json"/]
        pre --> loop
        loop <--> guards
        guards --> out
    end

    subgraph SERVER["bodhan_mcp  (python -m bodhan_mcp, stdio or HTTP+bearer token)"]
        direction TB
        subgraph FREE["Free tools (no model)"]
            t1["inspect_media"]
            t2["detect_script_language"]
            t3["list_voices"]
            t8["verify_translation"]
        end
        subgraph PAID["Model tools (limits handled inside)"]
            t4["transcribe_audio<br/>28 s windows at silences"]
            t5["translate_text<br/>chunk, tables, SRT, LaTeX"]
            t6["synthesize_speech<br/>short requests, join, loudnorm"]
            t7["ocr_document<br/>image / PDF pages"]
            t9["verify_audio<br/>back-transcribe + CER"]
        end
        client["Shared Bodhan client<br/>per-model keys, retries + backoff<br/>concurrency semaphore, timeouts<br/>cost + latency per call"]
        cache[("On-disk cache<br/>sha256(model, exact input)")]
        res["Resources<br/>bodhan://voices, bodhan://languages"]
        PAID --> client
        client <--> cache
    end

    subgraph BODHAN["Bodhan hosted API  api.bodhan.ai/v1"]
        m1["indic-transcribe"]
        m2["indic-translate"]
        m3["indic-speak"]
        m4["indic-ocr"]
    end

    local["Local tooling<br/>ffmpeg / ffprobe, poppler, Pillow, pypdf"]

    user --> pre
    loop <-->|chat + tool calls| llm
    guards <-->|"MCP over stdio<br/>(paths only, no base64)"| SERVER
    ext <-->|stdio / HTTP| SERVER
    client --> m1 & m2 & m3 & m4
    PAID -.-> local
    FREE -.-> local
```

## One line per part

| Part | Role |
|---|---|
| **MCP server** (`src/bodhan_mcp/`) | Nine typed tools + two resources. Validates input, handles each model's limits, returns structured results or structured errors. Works with any MCP client, with or without the agent. |
| **Shared client** (`client.py`, `cache.py`) | The only code that talks to Bodhan. Per-model keys, retry with exponential backoff, a global concurrency limit, per-call timeouts, a content-addressed cache, and cost/latency on every call. |
| **Agent** (`src/bodhan_agent/`) | An LLM-driven loop around the MCP tools. The LLM decides *what* to do; plain code enforces *what is allowed*. |
| **Guards** (`guards.py`) | Budget, pre-spend refusal, Indic-text rule, injection scanning, unsupported-language refusal, verification gate. None of it can be argued away by the model. |
| **Outputs** | `result.md` (answer, assumptions, artifacts, verification, cost), `artifacts/`, `trace.jsonl` (one line per step), `report.json` (machine-readable summary, targets missed). |

All speech recognition, translation, speech synthesis and OCR go to the four Bodhan models through the server's tools. There is no other model of any of those kinds anywhere in the code, not even as a fallback.
