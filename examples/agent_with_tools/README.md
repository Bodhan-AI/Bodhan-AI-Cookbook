# Bodhan tool server and task agent 

An MCP server that exposes Bodhan's four Indic models (Indic-Transcribe, -Translate, -Speak, -OCR) as tools, and a LangGraph
task agent that plans, calls them, verifies its work, enforces budgets and writes a trace.

**Documentation**

- [Architecture diagram](docs/architecture.md): processes, tools, and how they connect
- [Agent-loop flow chart](docs/agent-flow.md): plan, act, verify, retry, stop
- [Design notes](docs/design-notes.md): tool list and granularity, LLM vs plain code, verification, budgets, recovery, untrusted text

**Requirements:** Python 3.10+. `ffmpeg` on the PATH is needed for non-WAV audio and for video inputs (WAV and images/PDFs work without it).

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

The API documents no numeric input limit, so the tool handles size itself (`src/bodhan_core/chunking.py`, `src/bodhan_mcp/tools/translate.py`):

- Text is split losslessly at sentence ends (`.` `!` `?` `।` `॥` `۔`) and line breaks into chunks of at most
  `TRANSLATE_MAX_CHARS` (default 1500), translated one request at a time (`TRANSLATE_CONCURRENCY=1`) and re-joined
  in order with the original whitespace, so paragraphs and table rows keep their layout.
- A chunk the API rejects (HTTP 413 `payload_too_large`) or returns `truncated` is halved and retried.
- Identical chunks are cached in-process and never sent twice; 429/5xx/timeouts retry with exponential backoff.
- Hard ceiling of 200,000 characters per call; a failure on any chunk returns a structured error naming the chunk.

## Using the server without the agent

`python -m bodhan_mcp` speaks MCP over stdio and needs only the `BODHAN_*` keys. Each tool's description and
schema is meant to be enough on its own. Resources `bodhan://voices`, `bodhan://voices/{language}` and
`bodhan://languages`, and prompts `translate_document`, `speak_in_language` and `subtitle_audio`, are exposed too.

Tested with the official MCP Python SDK client (`mcp` 1.30.0): over stdio in `tests/test_stdio_and_files.py`, and over
streamable HTTP in `tests/test_http_and_resources.py`.

### Optional: streamable HTTP with a bearer token

```bash
export BODHAN_MCP_TOKEN=<long random string>      # the server refuses to start without it
python -m bodhan_mcp --transport http --port 8000  # endpoint: http://127.0.0.1:8000/mcp
# client header:  Authorization: Bearer <token>
python -m bodhan_agent --task "..." --mcp-url http://127.0.0.1:8000/mcp
```

Requests with a missing or wrong token get HTTP 401 (tested). The server process needs its own `BODHAN_*` keys.
stdio remains the default; nothing above is required for the standard commands.

## Demo tasks

Run with `python demo/run_demo.py` (tasks in [demo/tasks.json](demo/tasks.json)). Full table, outputs and traces:
[demo/results/](demo/results/README.md). LLM: `gemini-3-flash-preview`. Generated audio is not committed.

| Task | Result | Bodhan cost (Rs) | Time (s) |
|---|---|---|---|
| Hindi voice note (language unstated) to English SRT | completed, SRT check passed | 0.018 | 25 |
| Read a paragraph aloud in Bengali, female voice | completed, back-transcription passed | 0.077 | 27 |
| ~30 s Tamil audio summary of 2 PDF pages | completed, back-transcription passed | 0.511 | 64 |
| Marathi translation of a note containing a prompt injection | blocked before any LLM call (intended) | 0 | 5 |
| Translate a paragraph into French | impossible, explained, no output | 0 | 6 |
| OCR a Hindi-script image to English (language unstated) | completed, translation check passed | 0.202 | 32 |

**Demo video (5 minutes or less):** <https://drive.google.com/file/d/1UsARBcVIY4PoJ_6I827l0uLDWPLgca0g/view?usp=sharing>

## Evals

A self-evaluation suite ([evals/golden.json](evals/golden.json), 11 cases) runs the real agent on tasks such as a Tamil
PDF summary, Hindi voice note to English SRT, a Marathi worksheet with a table, an injection in a text file, a missing
file, an unsupported language and a budget too small to finish. Each case is scored with deterministic checks: status,
tool order and arguments, no duplicate calls, no artifacts on impossible tasks, verification, injection flagged, cost,
and no Indian-language text written by the LLM. The run prints a pass rate and total cost.

```bash
pip install -e ".[dev,eval]"
python -m evals.run_evals                          # stub mode: paid Bodhan tools return canned results (Rs 0, real LLM)
python -m evals.run_evals --only injection_in_text_file,missing_file_impossible
python -m evals.run_evals --mode live              # real Bodhan calls for cases marked live (small, capped cost)
python demo/run_demo.py                      # the six demo tasks above, live
```

Stub mode tests the agent's decisions (tools, arguments, refusals, budgets), not Bodhan's output quality. Results go to
`outputs/evals/<run>.json`.

## AI assistance

Claude Code was used to draft and refactor code, write tests and write these docs. Every design decision and line is
mine to explain.

## Limitations (this iteration)

Verification is heuristic (text similarity, structure) and cannot judge translation quality or voice naturalness. Only the final `plan` text is stored in full; tool results are truncated to 300 characters in the trace.
The cache is per server process (not on disk). Subtitle timing is shared out by text length (the API gives no word timestamps). Chunks are translated independently, so cross-sentence context is lost at chunk edges.
