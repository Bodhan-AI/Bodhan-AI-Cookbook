## Summary

`examples/indic_task_agent/` has two parts:

1. **An MCP server** (`python -m bodhan_mcp`) that turns Bodhan's four Indic models into eight typed, well-described tools. Each tool hides its model's limits (30 s audio windows, short TTS inputs, the translation context window, multi-page documents) and passes media by file path.
2. **A command-line agent** (`python -m bodhan_agent`) that plans, calls those tools over MCP, verifies its own outputs and stops cleanly within step, rupee and time budgets. It writes `result.md`, `artifacts/`, `trace.jsonl` and `report.json`.

Impossible tasks end in a clear explanation with no output, and the code enforces that.

**Type:** new recipe
**Path:** examples/indic_task_agent/
**Bodhan models used:** indic-transcribe, indic-translate, indic-speak, indic-ocr

## How run

```bash
cd examples/indic_task_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # needs Python 3.12+ and ffmpeg
cp .env.example .env                     # Bodhan key(s) + LLM_BASE_URL / LLM_MODEL / LLM_API_KEY

python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]
python -m bodhan_mcp                     # the server alone, over stdio
python -m pytest -q                      # offline tests (fake Bodhan API + scripted LLM)
```

A Dockerfile (Python 3.12, ffmpeg, Noto fonts) is included.

## Architecture

See [docs/architecture.md](examples/indic_task_agent/docs/architecture.md) (Mermaid).

- **`bodhan_mcp.server`:** 8 tools, 3 resources (`bodhan://catalog`, `bodhan://voices`, `bodhan://errors`) and 1 prompt; stdio, or streamable HTTP with a bearer token.
- **`bodhan_mcp.tools.*`:** one module per capability, each handling its model's limits.
- **`bodhan_mcp.client`:** retries with backoff, a concurrency limit, a content-hash cache, in-flight de-duplication and a per-call rupee ledger.
- **`bodhan_agent`:** a real MCP client (stdio), the plan → act → verify → finish loop, guards in code, budgets, the trace and the reports.

## Agent flow

See [docs/agent_flow.md](examples/indic_task_agent/docs/agent_flow.md) (Mermaid): pre-flight → plan → budget check → act (guards, injected budget) → check results → retry, re-plan or finish → finish validation → verification gate → reports.

## Tool design

| Tool | Limits handled inside |
|---|---|
| `inspect_files` (free) | Types, durations, pages, scripts, estimated cost; directories expanded. |
| `list_capabilities` (free) | Supported languages per model, voices, styles, prices. |
| `transcribe_audio` | ffmpeg decode (audio or video); VAD chunks under 30 s, cut at pauses; silent chunks skipped; `auto` language → detected script; timed segments. |
| `translate_text` | Markdown parsed so tables, headings, lists and code stay literal (cells translated one by one); segments/SRT timings kept; sentence chunks ≤ 1,200 chars; `truncated` → re-split. |
| `synthesize_speech` | 1–2 sentence chunks ≤ 240 chars, joined; per-chunk back-transcription check; failing chunks re-synthesised as smaller pieces; AI-generated metadata. |
| `ocr_document` | PDF pages rendered at 200 dpi; blank pages skipped; 502 → retry with `max_tokens=4096`; partial results kept. |
| `make_subtitles`, `read_text` (free) | Cue splitting and wrapping; paged reading marked untrusted. |

**Why this granularity:** one tool per capability, with limits handled inside, plus free helpers. Raw per-model wrappers would push chunking onto the planner. Task-level tools would hide control and fail on unseen tasks. Details are in [docs/design_notes.md](examples/indic_task_agent/docs/design_notes.md).

## Where the LLM is used, and why

| Decision point | What it decides | Fallback if it fails |
|---|---|---|
| Plan (`submit_plan`) | Interpretation, deliverables, assumptions, feasibility, tool chain | Named `tool_choice` → `auto` + instruction → JSON from text. If there is still no plan, the act loop continues with a note. |
| Each act turn | Next tool(s) and arguments; independent calls in parallel | Schema and guard errors go back as structured errors with hints. |
| English writing | Summaries, explanations, the final answer | Code rejects Indic script written by the LLM (inline `text`, final answer). |
| Judging results | Reads `complete`, `coverage_ok`, `verification`, warnings; retries or stops | Code-level verification gate lowers `success` → `partial`. |
| `finish` | Status, answer, artifacts, assumptions | Validated: artifacts must be tool outputs; impossible/failed → none. If the LLM stops or errors, code finalises honestly. |

Everything else is plain code: inspection, script-based language ID, chunking, subtitle timing, budgets, caching, reports.

## Bodhan models used

Hosted API (`https://api.bodhan.ai`).

- **indic-transcribe:** `language` omitted for auto-detect unless known; ~20 s chunks (5 s for subtitles).
- **indic-translate:** called as `POST /v1/chat/completions` with `model=indic-translate` and `target_language`. The documented `POST /translate` returned 404 for our key on 18 Nov 2026, so the client tries the chat form first, falls back to `/translate`, and remembers which one works. `finish_reason=length` is treated as truncation. Spans are ≤ 1,200 chars.
- **indic-speak:** voice chosen from the library by language and gender; optional `style`; chunks ≤ 240 chars / 2 sentences.
- **indic-ocr:** `table_format=markdown`, `max_tokens` raised to 4096 on 502.

## Results

10 of 10 demo tasks pass with real Bodhan models (hosted) and Groq `openai/gpt-oss-120b`. Tasks 03 and 06 were last run with `gpt-oss-20b` after the 120b's daily quota ran out. Per-task outputs and traces: see the **Demo results** table in [examples/indic_task_agent/README.md](examples/indic_task_agent/README.md#demo-results), with links to every `result.md`, `report.json` and `trace.jsonl`.

The live runs found and fixed four real issues (details in [design notes §8b](examples/indic_task_agent/docs/design_notes.md)):
1. The documented `POST /translate` route returns 404 on the live API; translation now uses `/v1/chat/completions` with `model=indic-translate`, with a fallback.
2. Groq rejects tool calls whose optional fields are `null`; optional fields now allow null, and validation or parse errors are retried.
3. Bodhan returned 429s on bursts of short subtitle chunks; concurrency is now 2 with longer backoff, and an incomplete result can be retried with cached chunks reused.
4. Groq's 8k tokens-per-minute limit is handled by compacting older context and waiting on 429.

`python -m pytest -q` runs 78 offline tests (fake Bodhan API + scripted LLM, plus tests of the real LLM client against a stub).

## Demo video

<!-- paste the link (≤ 5 min, viewable without sign-in) -->

## Trade-offs and known limitations

See [design notes §9](examples/indic_task_agent/docs/design_notes.md#9-trade-offs-and-known-limitations):

- energy-based VAD;
- proportional subtitle timing (no word timestamps from the API);
- script-level language ID only;
- uncalibrated TTS-check thresholds;
- digital PDFs also OCR'd;
- LLM cost reported in tokens.

## Cost and runtime

| Task | Bodhan ₹ | Time |
|---|---|---|
| 01 Tamil audio summary (8-page PDF) | 0.99 | 295 s |
| 02 Hindi voice note → English SRT | 0.03 | 103 s |
| 03 Worksheet → Marathi | 0.20 (₹0 on cached rerun) | 59 s |
| 04 Telugu clips | 0.04 | 287 s |
| 05 Bengali TTS | 0.24 | 58 s |
| 06 French video (impossible) | 0.00 | 15 s |
| 07 Prompt-injection notice → Hindi | 0.20 (₹0 on cached rerun) | 143 s |
| 08 Budget too small | 0.00 | 5 s |
| 09 Missing file | 0.00 | 27 s |
| 10 Unstated language → English | 0.0006 | 88 s |

Most of the wall time is waiting on the free Groq tier's rate limits; Bodhan calls take 0.3–3 s each. All 10 runs stayed within `--max-cost-inr` (`budget_respected: true` in every report).

## AI assistance

<!-- Write this in your own words, and only what is true. Suggested starting point: -->
I used Claude (claude.ai) heavily. From the assignment brief and my design decisions, it generated most of the initial code, tests and documentation. I set the project up and ran it against the live Bodhan APIs and Groq on Windows. I diagnosed the failures from the traces (the translate route returning 404, Groq's schema validation and rate limits, Bodhan 429s, stale outputs) and iterated on the fixes with it. I have reviewed every module and can explain and extend it.

LLM tested with: Groq `openai/gpt-oss-120b` (primary) and `openai/gpt-oss-20b`.
