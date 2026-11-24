# RESUME BRIEFING - Read This First If Context Was Lost
# Bodhan Tool Server & Task Agent

> **Purpose of this file:** If the AI assistant's context limit was hit and a new conversation or model was started, read this file FIRST. It tells you exactly where we are, what was built, what to do next, and all critical decisions already made.

---

## 🎯 What We Are Building

An AI agentic engineering take-home assignment for a job at Bodhan AI.

**Two deliverables:**
1. **`bodhan_mcp`** - An MCP (Model Context Protocol) server exposing Bodhan's 4 Indic AI models as tools
2. **`bodhan_agent`** - A CLI task agent that uses those tools to complete plain-language multi-step tasks

**Deadline:** 2026-11-28  
**Submission:** PR to https://github.com/Bodhan-AI/Bodhan-AI-Cookbook  
**Assignment Contact:** ishvinder@bodhan.ai  
**Assignment PDF:** `C:\Users\LENOVO\Downloads\Agentic Engineer Take-Home Bodhan Tool Server and Task Agent.md`

---

## 📂 Project Location

```
E:\Bodhan\
├── docs\
│   ├── PRD.md              ← Full product requirements
│   ├── ARCHITECTURE.md     ← Full architecture + module map
│   ├── TASK_LIST.md        ← All tasks with ✅/⬜ status
│   ├── DECISIONS.md        ← All design decisions + rationale
│   └── RESUME_BRIEFING.md  ← (this file)
├── src\
│   ├── bodhan_mcp\         ← MCP server code
│   └── bodhan_agent\       ← Task agent code
└── tests\                  ← All tests
```

---

## ⚡ Current Status

**Phases 0-4: DONE** - All docs + all code written and syntax-verified  
**Current Phase: Phase 5** - Run demo tasks (requires Bodhan API keys from console.bodhan.ai)

**What's been built (all files parse clean):**
- `src/bodhan_mcp/clients/bodhan_client.py` - Bodhan API client (retry, semaphore, cost tracking)
- `src/bodhan_mcp/tools/` - All 7 tools: transcribe, translate, speak, ocr, language_detect, audio_chunk, media_info
- `src/bodhan_mcp/server.py` + `__main__.py` - Full FastMCP server
- `src/bodhan_agent/` - Full agent: cache, budget, trace, sanitizer, planner, verifier, executor, agent loop, CLI
- `tests/` - Unit tests for budget, cache, sanitizer, translate splitting, audio chunking + e2e test

**Check `docs/TASK_LIST.md`** for the full status of every task. The status column uses:
- ✅ DONE
- 🔄 IN PROGRESS  
- ⬜ TODO
- ❌ BLOCKED

---

## 🔑 Critical Rules (Auto-Fail if Broken)

1. **ONLY Bodhan models** for STT, translation, TTS, and OCR. No Whisper, Tesseract, cloud APIs, etc.
2. **LLM must NOT** write Indian-language text, transcribe audio, or read text from images
3. **No API keys** in git - use `.env.example`
4. **No fabricated outputs** - impossible tasks must fail honestly with a clear explanation
5. **No hardcoded logic** for demo tasks - must generalize to unseen tasks

---

## 🤖 Bodhan Models to Use

| Model | Key Env Var | Use For | Price |
|---|---|---|---|
| Indic-Transcribe | `BODHAN_TRANSCRIBE_KEY` | All STT | ₹0.10/min audio |
| Indic-Translate | `BODHAN_TRANSLATE_KEY` | All translation | ₹0.20/10K tokens |
| Indic-Speak | `BODHAN_SPEAK_KEY` | All TTS | ₹6/10K chars |
| Indic-OCR | `BODHAN_OCR_KEY` | All OCR | ₹0.20/image |

**API:** `https://api.bodhan.ai/v1` (OpenAI-compatible)

---

## 🧠 Key Design Decisions Already Made

| Decision | What We Chose |
|---|---|
| MCP framework | `mcp` Python SDK with FastMCP |
| Tool granularity | 4 model tools + 3 helpers (see ARCHITECTURE.md) |
| Media passing | File paths only, never base64 in LLM context |
| Cache | Run-scoped SQLite in `--out` dir |
| Prompt injection guard | Wrap OCR/transcript in `<data>` tags |
| Verification | Back-transcribe TTS; back-translate translations |
| Agent↔MCP connection | Direct import (agent), also works as standalone stdio server |
| TTS chunking | 500-char chunks at sentence boundaries, stitch with ffmpeg |
| Plan format | JSON with steps, args, depends_on, assumptions, impossible flag |
| Budget | Pre-flight estimate check + post-call reconciliation |

**Full rationale in `docs/DECISIONS.md`**

---

## 📋 CLI Interfaces (Must Not Change)

```bash
# Task agent (required interface - do not change)
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out \
  [--max-steps 30] [--max-cost-inr 20]

# MCP server on its own (required interface - do not change)
python -m bodhan_mcp
```

---

## 📁 Output Layout (Must Match Exactly)

```
out/
  result.md        # what was done, final answer, assumptions, links to artifacts
  artifacts/       # files produced: audio, subtitles, Markdown, etc.
  trace.jsonl      # one line per step: plan, tool, arguments, result summary, latency, cost
  report.json      # status, steps, verification results, total cost and time
```

---

## 🏗️ Build Order

Always build in this order:
1. `pyproject.toml` + `.env.example` + `Makefile`
2. `bodhan_client.py` (shared Bodhan API client with retry + cost tracking)
3. Tool implementations (transcribe → translate → speak → ocr → helpers)
4. MCP server (`server.py` + `__main__.py`)
5. Agent core (cache → budget → trace → sanitizer → planner → verifier → executor → agent)
6. Agent CLI (`__main__.py`)
7. Tests
8. Demo tasks (run all 6 demo tasks, capture outputs)
9. README + diagrams + demo video
10. PR submission

---

## 🔧 System Dependencies

- Python 3.11+
- ffmpeg (for audio chunking, stitching, loudness normalization)
- poppler (if PDF→image conversion needed for OCR)

---

## 🛑 Open Questions That Need Answering (From API Docs/Testing)

1. What is Indic-Speak's exact max input chars per call?
2. Does Indic-Transcribe return word-level timestamps? (Needed for SRT)
3. What languages does Indic-Speak support (the 13 production-tier list)?
4. Does Indic-OCR accept PDF directly or only images?
5. What does the Bodhan Cookbook's `make check` do?

**How to answer:** Log into https://console.bodhan.ai/ and read https://console.bodhan.ai/api-docs/

---

## 📝 How to Continue

1. Read `docs/TASK_LIST.md` - find the first ⬜ TODO item
2. Read `docs/ARCHITECTURE.md` for the module design
3. Read `docs/DECISIONS.md` for why things are the way they are
4. Continue building from where we left off

**Next immediate task:** Set Bodhan API keys in `.env` → `make setup` → `make test-unit` → `make demo` (Phase 5)
