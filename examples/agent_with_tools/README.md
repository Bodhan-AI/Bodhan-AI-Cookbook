# Bodhan tool server and task agent 

An MCP server that exposes Bodhan's four Indic models as tools, and a LangGraph task agent that plans, calls them,
verifies its work and writes a trace. See `docs/` for the assignment, `docs/limits.md` for how model limits are
handled and `docs/hard-part-review.md` for the design review of the hard parts (granularity, plumbing, recovery, cost, untrusted text).

## Tools

| Tool | Does | Notes |
|---|---|---|
| `transcribe_audio` | Indic-Transcribe, any length (audio or video track) | Windows cut at pauses; timed `segments`, `detected_language`, long text by `text_path` |
| `translate_text` | Indic-Translate, up to 200k chars | `text` or `text_path`; long output by `translation_path`; Markdown tables kept |
| `speak_text` | Indic-Speak to WAV (AI-generated notice) | `text` or `text_path`; `regenerate=true` retries a bad take |
| `ocr_document` | Indic-OCR for PNG/JPEG/PDF | Page by page; Markdown by `markdown_path`; `detected_language` |
| `make_subtitles` | Timed SRT from a transcript, optionally translated | No text through the LLM; timings from the audio |
| `inspect_media` | Kind, duration, pages, estimated cost (free) | Unsupported types explained |
| `detect_language`, `read_text_file`, `write_text_file` | Free helpers | Writes confined to the output folder |

Long text moves between tools by file path, never through the LLM's context. The agent ends a run with a local `finish_task`
(`completed` / `partial` / `impossible` + summary + assumptions), recorded in `report.json` and `result.md`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows (use `source .venv/bin/activate` elsewhere)
pip install -e ".[dev]"
cp .env.example .env          # then fill in the keys (a .env at the repo root also works)
```

`LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` (e.g. `openai/gpt-oss-120b`) are all read from the environment.
Langfuse tracing turns on automatically when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set.

## Run

```bash
python -m bodhan_agent --task "Translate 'Good morning, how are you?' into Hindi" --out ./outputs/run1
python -m bodhan_agent --task "Read this paragraph aloud in Bengali" --files path/to/inputs/ --out ./out --max-steps 30 --max-cost-inr 20
python -m bodhan_mcp          # the MCP server on its own (stdio)
python -m bodhan_mcp --transport http   # optional: streamable HTTP at /mcp, needs BODHAN_MCP_TOKEN (agent: --mcp-url)
pytest                        # unit tests (no network)
```

## How it works

```
CLI -> LangGraph: plan-step (LLM) -> call-tools --MCP stdio--> bodhan_mcp (translate / transcribe / speak / ocr / detect_language / read+write text)
                    ^  |   ^             |
                    |  |   +-------------+
                    |  +--finish_task (alone)--> finish-task -> verify -> finish-completed
                    +------- repair (once) ---------------------+
plain-text reply -> nudge "call finish_task" (once) -> plan-step; still none -> verify -> status "partial"
step budget spent (checked before every LLM call) or cost budget hit -> finish-budget-exhausted
```

- **CLI.** `--task` and `--files` (files and/or folders). Files are inventoried (kind, size, WAV duration, PDF pages) and
  handed to the planner by path; media and long text never enter the LLM context (tools pass files by path). `read_text_file` / `write_text_file` let the agent
  read text inputs and write Markdown/SRT outputs; writes are confined to `<out>/artifacts`.
- **Verification** (`verify.py`, runs after `finish_task`, or after the planner stops calling tools). Every `speak_text` WAV is back-transcribed
  with Indic-Transcribe and compared with the spoken text (catches repeated or omitted content). Every `translate_text`
  result is checked for truncation, line/table-row structure, plausible length and target script. A failure gives the
  planner one repair round; after that the failure is reported honestly. Results go to `report.json` and `result.md`.
- **Budgets.** `--max-steps` (planning steps), `--max-cost-inr` (default 5, safe for the Rs 10 trial credit) and `--call-timeout-s` (per tool call,
  default 180). A call whose estimated cost exceeds what is left is refused *before* it is made; a timed-out call becomes
  a retryable error. When a budget runs out the run stops with status `budget_exhausted` and a report of what was done and
  what was left unverified. Cost counts Bodhan calls only, not LLM tokens.
- **Trace.** `trace.jsonl` has one line per step (`plan`, `tool`, `verify`: arguments, result summary, latency, cost) plus a
  final `summary` line; `report.json` has status, steps, verification results, artifacts, total cost and time.
- **MCP server alone.** `python -m bodhan_mcp` speaks stdio; any MCP client works without the agent (covered by
  `tests/test_stdio_and_files.py`, which drives it with the official MCP Python client).
- Langfuse: one trace per run (`bodhan-task-run`), optional (enabled when both keys are set).

## Long input in `translate_text`

The API documents no numeric input limit, so the tool handles size itself (`src/bodhan_mcp/chunking.py`, `tools/translate.py`):

- Text is split losslessly at sentence ends (`.` `!` `?` `।` `॥` `۔`) and line breaks into chunks of at most
  `TRANSLATE_MAX_CHARS` (default 1500), translated one request at a time (`TRANSLATE_CONCURRENCY=1`) and re-joined
  in order with the original whitespace, so paragraphs and table rows keep their layout.
- A chunk the API rejects (HTTP 413 `payload_too_large`) or returns `truncated` is halved and retried.
- Identical chunks are cached in-process and never sent twice; 429/5xx/timeouts retry with exponential backoff.
- Hard ceiling of 200,000 characters per call; a failure on any chunk returns a structured error naming the chunk.

## Limitations (this iteration)

Verification is heuristic (text similarity, structure) and cannot judge translation quality or voice naturalness. Only the final `plan` text is stored in full; tool results are truncated to 300 characters in the trace.
The cache is per server process (not on disk). Subtitle timing is shared out by text length (the API gives no word timestamps). Chunks are translated independently, so cross-sentence context is lost at chunk edges.
