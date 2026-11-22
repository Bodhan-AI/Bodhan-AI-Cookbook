# Product Requirements Document
# Bodhan Tool Server & Task Agent - Agentic Engineer Take-Home

**Version:** 1.0  
**Author:** Sarthak Pal  
**Date:** 2026-11-21  
**Assignment Contact:** ishvinder@bodhan.ai  
**Deadline:** 2026-11-28 (7 days from 2026-11-21)  
**Submission:** PR to https://github.com/Bodhan-AI/Bodhan-AI-Cookbook  

---

## 1. Purpose

Build two things:
1. **`bodhan_mcp`** - An MCP (Model Context Protocol) server that exposes Bodhan's four Indic models as well-typed, well-documented tools. Any off-the-shelf MCP client must be able to drive it from tool descriptions alone.
2. **`bodhan_agent`** - A CLI task agent that accepts a plain-language `--task` and `--files`, plans, calls the MCP tools, verifies outputs, enforces budgets, and writes results to `--out`.

Both must run **unattended** on tasks they have never seen.

---

## 2. Bodhan Models (Mandatory - cannot use anything else)

| Model | Purpose | HuggingFace | Hosted Pricing |
|---|---|---|---|
| **Indic-Transcribe** (Core or Flex) | Speech-to-text, language detection from audio | [Core](https://huggingface.co/bodhan-ai/indic-transcribe-core) / [Flex](https://huggingface.co/bodhan-ai/indic-transcribe-flex) | ₹0.10 / audio minute |
| **Indic-Translate** | All translation (English ↔ Indian langs, Indian ↔ Indian via English pivot) | [indic-translate](https://huggingface.co/bodhan-ai/indic-translate) | ₹0.20 / 10K output tokens |
| **Indic-Speak** | Speech synthesis / TTS | [indic-speak](https://huggingface.co/bodhan-ai/indic-speak) | ₹6 / 10K input chars |
| **Indic-OCR** | Text extraction from images and scanned PDFs | [indic-ocr](https://huggingface.co/bodhan-ai/indic-ocr) | ₹0.20 / image |

**API Base:** `https://api.bodhan.ai/v1` (OpenAI-compatible)  
**Console:** https://console.bodhan.ai/  
**Docs:** https://console.bodhan.ai/api-docs/

---

## 3. Environment Variables

```bash
# Bodhan model keys
BODHAN_TRANSCRIBE_KEY=...
BODHAN_TRANSLATE_KEY=...
BODHAN_SPEAK_KEY=...
BODHAN_OCR_KEY=...

# LLM (agent brain) - will be swapped by evaluator
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
LLM_API_KEY=...
```

---

## 4. CLI Interfaces (MUST NOT CHANGE)

```bash
# Task agent
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out \
  [--max-steps 30] [--max-cost-inr 20]

# MCP server (stdio)
python -m bodhan_mcp
```

---

## 5. Output Layout (per run)

```
out/
  result.md        # what was done, final answer, assumptions, links to artifacts
  artifacts/       # files produced: audio, subtitles, Markdown, ...
  trace.jsonl      # one line per step: plan, tool, arguments, result summary, latency, cost
  report.json      # status, steps, verification results, total cost and time
```

---

## 6. Functional Requirements

### 6.1 MCP Server (`bodhan_mcp`)

| # | Requirement |
|---|---|
| F-01 | Expose tools for: transcribe (STT), translate, speak (TTS), ocr |
| F-02 | Expose helper tools: audio_chunk, language_detect, media_info |
| F-03 | Each tool has: typed inputs, typed outputs, clear description, explicit actionable errors |
| F-04 | Media passed as file references (paths/URIs) - NEVER inline base64 in LLM context |
| F-05 | Handle model limits internally: 30s audio windows, short TTS inputs, translation context window, multi-page docs |
| F-06 | Return structured errors (never crash client on bad input) |
| F-07 | Run as stdio MCP server independently of the agent |
| F-08 | (Stretch) Streamable HTTP transport with token auth |
| F-09 | (Stretch) Expose voice library as MCP resource |

### 6.2 Task Agent (`bodhan_agent`)

| # | Requirement |
|---|---|
| F-10 | Accept `--task`, `--files`, `--out`, `--max-steps`, `--max-cost-inr` |
| F-11 | Plan multi-step tasks using LLM |
| F-12 | Execute plan by calling MCP tools (or importing tool code directly) |
| F-13 | Detect input language when unstated |
| F-14 | Verify outputs before finishing (back-transcribe audio, check translation coverage) |
| F-15 | Enforce step budget (--max-steps) and cost budget (--max-cost-inr) |
| F-16 | Stop cleanly when budget exhausted; report what was done |
| F-17 | Write trace.jsonl: plan + tool call + args + result summary + latency + cost per step |
| F-18 | Write report.json: status, steps, verification, total cost and time |
| F-19 | Handle impossible tasks: unsupported language, missing file, budget too small → clear explanation, no fake output |
| F-20 | Treat OCR/transcript text as data, never as agent commands (prompt injection guard) |
| F-21 | Cache results: never send same input to same model twice in a run |
| F-22 | Retry with backoff on rate limits / timeouts |
| F-23 | Concurrency limit for parallel API calls |
| F-24 | (Stretch) Parallel tool calls for independent steps |
| F-25 | (Stretch) Resumable tasks with persistent state |
| F-26 | (Stretch) Thin web UI showing plan and trace live |

---

## 7. Quality Targets

| Check | Target |
|---|---|
| Task completion | ≥ 8/10 hidden tasks completed correctly |
| Honest failure | Impossible tasks → clear explanation, no fabricated output |
| Verification | Every generated audio and translation checked before task ends |
| No waste | Same input never sent to same model twice per run |
| Budget | Never exceeds `--max-cost-inr`; cost reported per task |
| Latency | Single-step < 30s; multi-step < 5 min |
| Tool usability | Third-party MCP client completes tool-level checks from descriptions alone |

---

## 8. Auto-Fail Conditions (MUST AVOID)

- Any non-Bodhan ASR, translation, TTS, or OCR system in the code (including Whisper, Tesseract, cloud APIs)
- LLM writing Indian-language text, transcribing audio, or reading text from images
- Fabricated outputs or fake success claims
- Logic hard-coded to demo tasks
- API keys committed to the repo

---

## 9. Engineering Requirements

| Requirement |
|---|
| One-command setup from clean clone with pinned deps |
| Dockerfile recommended |
| ffmpeg as a system dependency (documented) |
| Unit tests for each tool's chunking and limit handling |
| One end-to-end test for a multi-step task |
| No keys in repo - ship `.env.example` |
| Media files out of git - link from README |

---

## 10. Demo Tasks (must all pass before submission)

| Task | Input | Expected Output |
|---|---|---|
| Tamil audio summary of chapter | Scanned PDF, 8 pages | Tamil MP3 + summary text |
| Transcribe Hindi voice note → English subtitles | 3-min WAV | SRT in English |
| Translate worksheet to Marathi, keep table | Photo of worksheet | Marathi Markdown with table |
| Which clips are Telugu? Translate to English | 5 short audio clips | List of Telugu clips + English text |
| Read paragraph aloud in Bengali (female voice) | Text file | Bengali MP3 |
| Translate video to French (impossible) | MP4 | Clear explanation, no fake output |

---

## 11. PR Submission Checklist

- [ ] Fork Bodhan AI Cookbook
- [ ] Branch: `assignment/tool-agent-<github-handle>`
- [ ] Folder: `examples/<recipe-folder>/`
- [ ] Run `make check` before PR
- [ ] PR title: `[Agentic Eng Assignment] Tool server and agent - Sarthak Pal`
- [ ] Architecture diagram (Mermaid or image)
- [ ] Agent flow chart
- [ ] Design notes
- [ ] Demo video ≤ 5 minutes
- [ ] Reply email with PR link + commit SHA

---

## 12. Evaluation Rubric

| Dimension | Weight |
|---|---|
| Versatility on hidden tasks | 25% |
| Output quality | 20% |
| Agent and tool design | 30% |
| Engineering quality | 15% |
| Documentation and PR | 10% |
| Stretch goals | +10 bonus |
