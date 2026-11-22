# Bodhan tool server and task agent 

A minimal end-to-end slice: a LangGraph agent calls one Bodhan tool, `translate_text` (the cheapest model),
over an MCP stdio server, and every run is traced in Langfuse. See `docs/` for the full assignment.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows (use `source .venv/bin/activate` elsewhere)
pip install -e ".[dev]"
cp .env.example .env          # then fill in the keys (a .env at the repo root also works)
```

`LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` (e.g. `openai/gpt-oss-20b`) are all read from the environment.
Langfuse tracing turns on automatically when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set.

## Run

```bash
python -m bodhan_agent --task "Translate 'Good morning, how are you?' into Hindi" --out ./outputs/run1
python -m bodhan_agent --task "Read this paragraph aloud in Bengali" --files path/to/inputs/ --out ./out --max-steps 30 --max-cost-inr 20
python -m bodhan_mcp          # the MCP server on its own (stdio)
pytest                        # unit tests (no network)
```

## How it works

```
CLI -> LangGraph: plan-step (LLM) -> call-tools --MCP stdio--> bodhan_mcp (translate / transcribe / speak / ocr / detect_language / read+write text)
                      ^                  |
                      +---- verify <-----+   (no more tool calls)  -> finish
```

- **CLI.** `--task` and `--files` (files and/or folders). Files are inventoried (kind, size, WAV duration, PDF pages) and
  handed to the planner by path; media never enters the LLM context. `read_text_file` / `write_text_file` let the agent
  read text inputs and write Markdown/SRT outputs; writes are confined to `<out>/artifacts`.
- **Verification** (`verify.py`, runs when the planner has no more tool calls). Every `speak_text` WAV is back-transcribed
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
The cache is per server process (not on disk). Chunks are translated independently, so cross-sentence context is lost at chunk edges.
