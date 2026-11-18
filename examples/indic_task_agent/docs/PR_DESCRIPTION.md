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

<!-- paste outputs/eval/summary.md -->

## Demo video

<!-- link (≤ 5 min, no sign-in) -->

## Trade-offs and known limitations

See [design notes §9](examples/indic_task_agent/docs/design_notes.md#9-trade-offs-and-known-limitations):

- energy-based VAD;
- proportional subtitle timing (no word timestamps from the API);
- script-level language ID only;
- uncalibrated TTS-check thresholds;
- digital PDFs also OCR'd;
- LLM cost reported in tokens.

## Cost and runtime

<!-- per task, from report.json -->

## AI assistance

<!-- e.g. Claude: scaffolding, test fakes and docs drafts; every line reviewed and explained by me. -->

LLM tested with: <!-- provider / model -->
