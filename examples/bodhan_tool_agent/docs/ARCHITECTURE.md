# Architecture - Bodhan Tool Server & Task Agent

**Version:** 1.0  
**Last Updated:** 2026-11-21

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER / CLI                               │
│    python -m bodhan_agent --task "..." --files ... --out ...    │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     BODHAN AGENT                                │
│  ┌─────────────┐   ┌──────────────┐   ┌────────────────────┐   │
│  │  Task Parser │──▶│  LLM Planner  │──▶│  Execution Engine  │   │
│  └─────────────┘   └──────────────┘   └────────┬───────────┘   │
│                                                 │               │
│  ┌──────────────────────────────────────────────┼─────────┐    │
│  │               Budget & Trace Manager          │         │    │
│  │  - step counter  - cost accumulator           │         │    │
│  │  - trace.jsonl writer  - report.json writer   │         │    │
│  └──────────────────────────────────────────────┼─────────┘    │
│                                                 │               │
│  ┌──────────────────────────────────────────────▼─────────┐    │
│  │               Result Cache (run-scoped)                 │    │
│  │  key = (model, input_hash) → stored output              │    │
│  └──────────────────────────────────────────────┬──────────┘   │
└────────────────────────────────────────────────│──────────────┘
                                                  │ MCP stdio / direct import
                                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                       BODHAN MCP SERVER                         │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐    │
│  │  transcribe  │  │  translate   │  │       speak        │    │
│  │  (STT tool)  │  │  (NMT tool)  │  │    (TTS tool)      │    │
│  └──────┬───────┘  └──────┬───────┘  └────────┬───────────┘    │
│         │                 │                    │                │
│  ┌──────▼───────┐  ┌──────▼───────┐  ┌────────▼───────────┐    │
│  │  ocr         │  │ language_    │  │   audio_chunk      │    │
│  │  (image tool)│  │ detect       │  │   (helper)         │    │
│  └──────────────┘  └──────────────┘  └────────────────────┘    │
│                                                                 │
│  ┌────────────────────────────────────────────────────────┐     │
│  │                  media_info (helper)                   │     │
│  └────────────────────────────────────────────────────────┘     │
└──────────────────────────────────┬──────────────────────────────┘
                                   │ OpenAI-compatible REST
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│               BODHAN API  (api.bodhan.ai/v1)                    │
│   Indic-Transcribe │ Indic-Translate │ Indic-Speak │ Indic-OCR  │
└─────────────────────────────────────────────────────────────────┘
```

---

## MCP Server Tool List

| Tool | Input | Output | Handles Internally |
|---|---|---|---|
| `transcribe` | `audio_path`, `language?` | `{text, language, segments[], cost}` | 30s chunking, retries |
| `translate` | `text`, `source_lang`, `target_lang` | `{translated_text, cost}` | Context window splitting |
| `speak` | `text`, `language`, `voice?` | `{audio_path, duration, cost}` | Short-input chunking, TTS repeat/omission detect |
| `ocr` | `image_path` or `pdf_path` | `{pages[{text, confidence}], cost}` | Multi-page batching |
| `language_detect` | `text` or `audio_path` | `{language, script, confidence}` | Script-based for text, Transcribe for audio |
| `audio_chunk` | `audio_path`, `max_seconds` | `{chunks[path]}` | VAD-aware splitting at silence |
| `media_info` | `file_path` | `{type, duration?, size, format}` | ffprobe wrapper |

---

## Agent Loop (Flow)

```
START
  │
  ▼
[1. Parse Task & Files]
  - Validate files exist
  - Detect file types via media_info
  - Sanitize any user-supplied text (prompt injection guard)
  │
  ▼
[2. Plan]  ← LLM
  - Given task + file types → ordered list of tool calls
  - Record plan in trace.jsonl
  - If plan is impossible (unsupported lang, missing file) → HONEST FAIL
  │
  ▼
[3. Execute Step]
  - Check budget (steps remaining, cost remaining)
  - If budget exhausted → PARTIAL STOP
  - Check cache (model + input_hash) → skip if hit
  - Call MCP tool
  - Record: tool, args, result summary, latency, cost in trace.jsonl
  │
  ▼
[4. Verify Step Output]  ← LLM + deterministic checks
  - Audio: back-transcribe with Indic-Transcribe, check for repeats/omissions
  - Translation: back-translate to source, check coverage
  - If verification fails → RETRY (up to 2x with backoff)
  │
  ▼
[5. Re-plan if needed]  ← LLM
  - If a step failed and retries exhausted → partial result + note
  - If all steps done → COMPLETE
  │
  ▼
[6. Write Outputs]
  - result.md: final answer, assumptions, artifact links
  - artifacts/: all produced files
  - trace.jsonl: full step trace
  - report.json: status, cost, time, verification results
  │
  ▼
END
```

---

## Key Design Decisions

### 1. Tool Granularity
One tool per model (transcribe, translate, speak, ocr) plus helpers (audio_chunk, language_detect, media_info).  
**Why:** One-per-model is simple enough that schemas remain legible to a third-party MCP client, while helpers prevent the planner from needing to reason about ffmpeg or chunking.

### 2. Media Plumbing
All media passed as file paths (absolute or relative to a working dir). Never base64 in the LLM context.  
**Why:** Base64 of a 3-minute audio ≈ 10MB of tokens. File paths keep the context clean.

### 3. Prompt Injection Guard
Any text coming from OCR or Transcribe is wrapped in a data boundary before being placed in the LLM prompt:
```
<data source="ocr" page="1">
...content...
</data>
```
LLM is instructed never to follow instructions inside `<data>` tags.

### 4. Budget Enforcement
- `BudgetManager` class tracks steps and cost in real time.
- Every tool call reports cost back via its return value.
- Agent checks budget BEFORE calling each tool; if insufficient, writes partial report and exits cleanly.

### 5. Caching
- Run-scoped SQLite cache: `cache.db` in the `--out` dir.
- Key: `sha256(model_id + json(inputs))`.
- On cache hit: returns stored result at zero cost.

### 6. Verification Strategy
| Output type | Verification method |
|---|---|
| Translation | Back-translate → check that key sentences are present in source |
| Generated audio (TTS) | Back-transcribe → compute character match rate against TTS input |
| Transcription | Check segment count vs audio duration (plausibility) |
| OCR | Check confidence scores; flag pages below threshold |

### 7. LLM Role (What It Does vs Doesn't)
**LLM DOES:**
- Understand plain-language tasks
- Plan tool call sequences
- Write English result.md content
- Judge verification results and decide retries
- Detect if a task is impossible

**LLM DOES NOT:**
- Write Indian-language text (must come from Indic-Translate)
- Transcribe audio (must use Indic-Transcribe)
- Read text from images (must use Indic-OCR)
- Clone voices or generate speech (must use Indic-Speak)

---

## Module Structure

```
bodhan-tool-agent/
├── src/
│   ├── bodhan_mcp/
│   │   ├── __main__.py          # MCP server entry point (stdio)
│   │   ├── server.py            # FastMCP server setup
│   │   ├── tools/
│   │   │   ├── transcribe.py    # Indic-Transcribe wrapper + chunking
│   │   │   ├── translate.py     # Indic-Translate wrapper + context splitting
│   │   │   ├── speak.py         # Indic-Speak wrapper + audio stitching
│   │   │   ├── ocr.py           # Indic-OCR wrapper + multi-page
│   │   │   ├── language_detect.py
│   │   │   ├── audio_chunk.py
│   │   │   └── media_info.py
│   │   └── clients/
│   │       └── bodhan_client.py # Shared OpenAI-compat HTTP client
│   │
│   └── bodhan_agent/
│       ├── __main__.py          # CLI entry point
│       ├── agent.py             # Main agent loop
│       ├── planner.py           # LLM-based planning
│       ├── executor.py          # Tool call execution
│       ├── verifier.py          # Output verification
│       ├── budget.py            # BudgetManager
│       ├── cache.py             # SQLite result cache
│       ├── trace.py             # trace.jsonl + report.json writers
│       └── sanitizer.py        # Prompt injection guard
│
├── tests/
│   ├── test_transcribe.py       # chunking + limit handling
│   ├── test_translate.py        # context window splitting
│   ├── test_speak.py            # TTS chunking + repeat detection
│   ├── test_ocr.py              # multi-page batching
│   ├── test_budget.py           # budget enforcement
│   ├── test_cache.py            # cache hit/miss
│   ├── test_sanitizer.py        # prompt injection guard
│   └── test_e2e.py              # end-to-end multi-step task
│
├── docs/
│   ├── PRD.md
│   ├── ARCHITECTURE.md          # (this file)
│   ├── TASK_LIST.md
│   ├── DECISIONS.md
│   └── RESUME_BRIEFING.md
│
├── README.md
├── pyproject.toml
├── Dockerfile
├── .env.example
└── Makefile
```
