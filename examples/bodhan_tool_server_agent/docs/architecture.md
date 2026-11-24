# Architecture

Two programs that talk over MCP stdio. The **server** (`bodhan_mcp`) owns the four Bodhan models, every model limit, the money and the cache. The **agent** (`bodhan_agent`) owns planning, guards, verification and the final status. The LLM is a replaceable, untrusted planner in the middle.

Companion page: [agent_flow.md](agent_flow.md) (the run lifecycle, guards, a real sequence and the status rules). Prose reasoning: [design_notes.md](design_notes.md).

## 1. System architecture

![System architecture](img/architecture.png)

<details>
<summary>Mermaid source for this diagram (GitHub draws it; VS Code needs a Mermaid extension)</summary>

```mermaid
flowchart TB
  user(["Caller:  --task  --files  --out  --max-steps  --max-cost-inr"])

  subgraph AG["bodhan_agent   (our code, deterministic)"]
    direction LR
    cli["cli.py<br/>flags, exit code"] --> pre["Preflight<br/>inspect every file,<br/>scan text for injection"] --> loop["Agent loop<br/>step cap, wall clock,<br/>parallel tool calls"] --> verify["Verifier<br/>back-transcribe audio,<br/>duration, translation checks"] --> final["Finalizer<br/>status set by code"]
    loop <--> guards["Guards<br/>plan first, repeat memo,<br/>no Indic text from the LLM,<br/>injection scan, claim checks"]
  end

  LLM{{"LLM planner  (any OpenAI-compatible endpoint)<br/>UNTRUSTED: plans, picks tools, writes English only"}}

  subgraph MS["bodhan_mcp   (stdio MCP server, runs standalone, usable by any MCP client)"]
    direction LR
    tools["13 typed tools<br/>input and output schemas,<br/>structured errors"] --> core["Shared core<br/>sandbox, cost ledger, disk cache,<br/>retry with backoff, concurrency limits"]
  end

  subgraph BA["Bodhan hosted API  (api.bodhan.ai)"]
    direction LR
    asr["Indic-Transcribe"]
    mt["Indic-Translate"]
    tts["Indic-Speak"]
    ocr["Indic-OCR"]
  end

  subgraph FS["Filesystem"]
    direction LR
    fsin[("input files<br/>read only")]
    fswork[("out/work<br/>intermediates, cost ledger")]
    fsout[("out/<br/>result.md, artifacts/,<br/>trace.jsonl, report.json")]
  end

  user --> AG
  AG <-->|"tool schemas and calls,<br/>wrapped results"| LLM
  AG <==>|"MCP over stdio:  file paths only, never media"| MS
  MS -->|"HTTPS, one key per model"| BA
  MS -.->|"read inputs, write work files"| FS
  AG ==>|"result.md, report.json,<br/>trace.jsonl, artifacts"| FS

  classDef trusted fill:#e8f1ff,stroke:#2b6cb0,color:#1a365d
  classDef untrusted fill:#fff4e5,stroke:#c05621,color:#7b341e
  classDef ext fill:#eef7ee,stroke:#2f855a,color:#1c4532
  class cli,pre,loop,guards,verify,final,tools,core trusted
  class LLM untrusted
  class asr,mt,tts,ocr ext
```

</details>

**Reading the diagram.** Blue is code we wrote and trust. Orange is the LLM: it can plan and choose tools, but it cannot set the status, spend past the cap, write Indian-language text, or list a file no tool produced. Green is Bodhan's hosted models. Media never enters the LLM's context; only file paths and short previews do.

## 2. Inside the MCP server

![Inside the MCP server](img/architecture_server.png)

<details>
<summary>Mermaid source for this diagram (GitHub draws it; VS Code needs a Mermaid extension)</summary>

```mermaid
flowchart TB
  client(["Any MCP client<br/>our agent, Inspector,<br/>Claude Desktop"])

  subgraph T["Tool layer  (server.py): every tool runs inside safe(), any exception becomes a JSON error"]
    direction TB
    t1["transcribe_audio"]
    t2["translate_text"]
    t3["translate_segments"]
    t4["synthesize_speech"]
    t5["ocr_document"]
    t6["free helpers<br/>build_srt, detect_language, inspect_media,<br/>estimate_cost, list_languages, list_voices,<br/>read_text_file, write_text_file"]
  end

  subgraph C["Shared core  (core.py)"]
    direction TB
    sb["Sandbox<br/>only the given inputs and the workspace,<br/>anything else is PATH_NOT_ALLOWED"]
    led["Cost ledger<br/>check, reserve, record, release,<br/>hard cap from the environment"]
    cache["Disk cache<br/>content-hash keys,<br/>in-flight dedupe"]
    http["HTTP client<br/>backoff on 429, 5xx, timeouts,<br/>no retry on other 4xx,<br/>none when credits are gone"]
    sem["Concurrency limits per model<br/>transcribe 4, translate 4,<br/>speak 2, ocr 4"]
    http --> sem
  end

  api[("Bodhan API<br/>4 models")]

  client <-->|"stdio"| T
  T -->|"all tools: path checks<br/>paid tools: ledger, cache, HTTP"| C
  C --> api
```

</details>

Typed outputs are pydantic models, so every tool publishes an MCP `outputSchema`. Errors are JSON with `code`, `message`, `hint` and `retryable`, for example `FILE_NOT_FOUND`, `UNSUPPORTED_LANGUAGE`, `TOO_LARGE`, `BUDGET_EXCEEDED`, `INSUFFICIENT_CREDITS`.

## 3. How each tool hides its model's limit

![How each tool hides its model's limit](img/tool_limits.png)

<details>
<summary>Mermaid source for this diagram (GitHub draws it; VS Code needs a Mermaid extension)</summary>

```mermaid
flowchart TB
  subgraph D["ocr_document: 1 image per call"]
    direction TB
    d1["image or PDF,<br/>up to 60 pages"] --> d2["render PDF pages at 200 dpi,<br/>fix EXIF and HEIC"] --> d3["one image per request"] --> d4["502: raise max_tokens,<br/>empty but inked: retry rotated"] --> d5["markdown_ref with page markers,<br/>failed pages listed"]
  end
  subgraph C["synthesize_speech: 30 s audio cap"]
    direction TB
    c1["text or text_ref"] --> c2["script check,<br/>whole-job budget check"] --> c3["sentence chunks<br/>up to 220 chars"] --> c4["identical chunks<br/>synthesised once"] --> c5["join with short gaps,<br/>MP3 with AI-generated tag"] --> c6["warn if a chunk reached 29.5 s,<br/>redo_chunks repairs one"]
  end
  subgraph B["translate_text: context window"]
    direction TB
    b1["text or text_ref"] --> b2["target language checked first,<br/>no spend if unsupported"] --> b3["sentence split,<br/>pieces up to 1200 chars"] --> b4["parallel, cached per piece"] --> b5["finish_reason length:<br/>halve the piece and retry"] --> b6["join in order, markdown mode<br/>keeps headings and tables"]
  end
  subgraph A["transcribe_audio: 30 s window"]
    direction TB
    a1["any audio or video"] --> a2["ffmpeg to 16 kHz mono WAV"] --> a3["silencedetect: cut at pauses,<br/>chunks of 5 to 28 s"] --> a4["parallel calls,<br/>language from chunk 1"] --> a5["looping chunk: retried once<br/>without silent edges"] --> a6["text_ref and<br/>segments_ref with cues"]
  end
```

</details>

The limits in boxes C and B were measured against the live API (`scripts/probe_limits.py`): speech audio is silently cut at 30.5 s, and a 2,737-character text translated in one request without truncation, so 1200 characters is a safe margin.

## 4. One paid call, end to end

![One paid call, end to end](img/paid_call.png)

<details>
<summary>Mermaid source for this diagram (GitHub draws it; VS Code needs a Mermaid extension)</summary>

```mermaid
flowchart TD
  s(["tool needs one paid call"]) --> k["key = hash of model, language,<br/>script and exact input"]
  k --> hit{"in disk cache?"}
  hit -- yes --> free["return cached result<br/>cost Rs 0"]
  hit -- no --> inflight{"same call already<br/>running right now?"}
  inflight -- yes --> wait["wait for it and share its result"]
  inflight -- no --> chk{"spent + estimate<br/>over the cap?"}
  chk -- yes --> be["BUDGET_EXCEEDED<br/>partial results already paid for are kept"]
  chk -- no --> res["reserve estimate in the ledger<br/>so parallel calls cannot overshoot"]
  res --> sem["wait for a per-model slot"]
  sem --> post["POST to Bodhan"]
  post --> code{"response"}
  code -- "2xx" --> rec["record real cost,<br/>write cache,<br/>release reservation"]
  code -- "429, 5xx, timeout" --> back["backoff and retry,<br/>up to 5 tries"]
  back --> post
  code -- "other 4xx" --> err["structured error,<br/>no retry"]
  code -- "balance too low" --> cr["INSUFFICIENT_CREDITS,<br/>never retried"]
  rec --> done(["result to the tool"])
  wait --> done
  free --> done
```

</details>

The agent never sees the cost cap. It is passed to the server as `BODHAN_MAX_COST_INR` and enforced there, because a model that can see the cap starts refusing tasks on cost by itself (observed in an early run).

## 5. Trust boundaries and untrusted content

![Trust boundaries and untrusted content](img/trust_boundaries.png)

<details>
<summary>Mermaid source for this diagram (GitHub draws it; VS Code needs a Mermaid extension)</summary>

```mermaid
flowchart TB
  subgraph IN["Untrusted data in"]
    direction TB
    f1["input files and file names"]
    f2["OCR text from images and PDFs"]
    f3["transcripts of audio"]
    f4["translation output"]
  end

  subgraph DEF["Defences in the agent"]
    direction TB
    d1["wrap in untrusted_content tags,<br/>system prompt: only the TASK<br/>gives instructions"]
    d2["scan whole text for injection phrases<br/>English, Hindi, Marathi"]
    d3["flag it in trace, report.json<br/>and result.md,<br/>status becomes completed_with_warnings"]
    d1 --> d2 --> d3
  end

  subgraph LLM["LLM output checked before use"]
    direction TB
    o1["Indian-language text in arguments,<br/>plan or summary: refused,<br/>then stripped and flagged"]
    o2["unsupported claim: accepted only for<br/>a language, a missing file or<br/>something beyond the tools"]
    o3["deliverables must be paths<br/>that a tool produced"]
  end

  subgraph SAFE["Hard limits"]
    direction TB
    h1["no shell, no network tool"]
    h2["server reads only given inputs"]
    h3["writes only inside the workspace"]
    h4["keys scrubbed from every output"]
  end

  IN --> DEF --> LLM --> SAFE
```

</details>

Live runs on English, Hindi, image and spoken-audio injection files all translated the text as data, flagged it, and obeyed none of it.
