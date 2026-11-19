# Indic task agent: Bodhan MCP tool server and task agent

Two programs that share one tool layer:

- **`python -m bodhan_mcp`** is an MCP server on stdio. It exposes Bodhan's four Indic models (speech-to-text, translation, speech synthesis and OCR) as 12 typed tools that any MCP client can use.
- **`python -m bodhan_agent`** is a non-interactive agent. It takes a plain-language task and input files, plans, calls the tools, verifies every translation and audio file, stays inside step, time and rupee budgets, and writes a report.

Every speech-recognition, translation, speech-synthesis and OCR operation goes to a Bodhan model. The planner LLM, any OpenAI-compatible endpoint with tool calling, only plans, writes English and judges results. Code enforces that it never writes Indian-language text.

- Design notes with the architecture diagram and the agent-loop flow chart: [docs/design.md](docs/design.md)
- Observed API behaviour, including where it differs from the published docs: [docs/api_notes.md](docs/api_notes.md)
- Demo and evaluation results with links to every output and trace: [docs/evaluation.md](docs/evaluation.md)

## Setup

You need Python 3.11+ and ffmpeg. If ffmpeg is not on `PATH`, the pinned `imageio-ffmpeg` package supplies a binary. `ffprobe` is optional.

```bash
cd examples/indic_task_agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # pinned; also installs this recipe in editable mode
cp .env.example .env               # then fill in the seven values
```

`.env` holds exactly these variables, and the code reads no other secrets:

| Variable | Used for |
|---|---|
| `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_OCR_KEY` | One key per Bodhan model, from [console.bodhan.ai](https://console.bodhan.ai/) |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | The planner. Any OpenAI-compatible `chat.completions` endpoint that supports `tools`. |

Docker is an alternative to the local install:

```bash
docker build -t indic-task-agent .
docker run --rm -i --env-file .env indic-task-agent    # MCP server on stdio
docker run --rm --env-file .env -v "$PWD/sample_data:/data" indic-task-agent   python -m bodhan_agent --task "Read this paragraph aloud in Bengali" --files /data/paragraph --out ./out
```

The image runs as a non-root user in `/work`, which that user owns, so a relative `--out ./out` works. Mount a folder there to keep the outputs.

## Run

### Run the agent

```bash
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out \
  --max-steps 30 --max-cost-inr 20
```

`--files` takes files, folders or both. Each run writes:

```text
out/
  result.md      the answer, deliverables with verification results, assumptions, limitations, cost and time
  artifacts/     audio, subtitles, Markdown and transcripts
  trace.jsonl    one line per event: plan, LLM decision with its rationale, tool call with arguments, result summary, latency, cost, plus every API attempt
  report.json    status, steps, verification results, cost breakdown, time and quality-target checks
```

`status` is one of `completed`, `completed_with_warnings`, `partial`, `failed` or `unsupported`. The process exits 0 whenever it wrote a report, including for honest failures.

Optional flags:

- `--max-seconds` sets the wall-clock limit (default 600).
- `--call-timeout` sets the per-request limit (default 60 s).
- `--concurrency` sets parallel requests per model (default 4).
- `--no-cache` and `--cache-dir` control the result cache.
- `--env-file` loads the variables from another file.
- `--llm-input-inr-per-1m` and `--llm-output-inr-per-1m` put the planner's own spend into the budget.
- `--resume` continues an interrupted run in `--out`, with the same task, inputs and limits (see below).

`--max-steps` counts every planner call, `record_plan` and `finish` included. `--max-seconds` counts from the moment the command starts.

### Resume an interrupted run

```bash
python -m bodhan_agent --resume --out ./out
```

The agent writes a checkpoint to `out/.state/` between planner turns. It holds the conversation, plan, step and spend counters, artifact records with their SHA-256, and the provenance record, but no keys. On `--resume`, the agent first checks that every input file is byte-for-byte unchanged. It then re-runs a turn that was cut off, and continues within the remaining steps, time and budget. Paid results are kept in the run's own cache (`out/.state/cache`), so work finished before the interruption is not billed again. A request that was in flight at the moment of interruption may be sent again; its earlier attempt can't be confirmed either way. A finished run can't be resumed.

A run is never left without a report. At every checkpoint the agent writes the `report.json` and `result.md` that an interruption at that moment would leave (marked `"provisional": true`, stop reason `interrupted`, with the resume command). The final report replaces them. Ctrl-C and SIGTERM (`docker stop`) are handled the same way and leave the run resumable.

### Watch a run live

```bash
python -m bodhan_agent.viewer --out ./out        # then open http://127.0.0.1:8766/
```

The viewer is a read-only page that follows the run's plan, steps, verification results, cost and artifacts once a second. It binds to localhost, renders every value as plain text and serves files only from `out/artifacts/`. The agent never needs it.

### Run the MCP server

```bash
python -m bodhan_mcp [--artifacts-dir DIR] [--max-cost-inr N] [--allow-dir DIR ...]
```

To serve over Streamable HTTP with simple token authentication instead of stdio:

```bash
python -m bodhan_mcp --transport http --port 8765 [--token-file PATH]
```

The endpoint is `http://127.0.0.1:8765/mcp`, bound to loopback unless you pass `--allow-remote`. Every request needs `Authorization: Bearer <token>`. If the token file does not exist, a random token is written there, by default `<artifacts-dir>/.mcp_http_token`. It is a local transport token, never a Bodhan or LLM key. Host and Origin checks against DNS rebinding come from the MCP SDK. The server speaks plain HTTP: with `--allow-remote`, put it behind a TLS-terminating proxy so the token is never sent in clear text.

To use it from an MCP client, add a stdio server with this command. Example for clients that take an `mcpServers` JSON block:

```json
{"mcpServers": {"bodhan": {"command": "python", "args": ["-m", "bodhan_mcp", "--artifacts-dir", "/tmp/bodhan"],
                           "env": {"BODHAN_TRANSCRIBE_KEY": "...", "BODHAN_TRANSLATE_KEY": "...", "BODHAN_SPEAK_KEY": "...", "BODHAN_OCR_KEY": "..."}}}}
```

**Tested off-the-shelf client: [MCP Inspector](https://github.com/modelcontextprotocol/inspector) CLI 2.13.0.** For example:

```bash
npx @modelcontextprotocol/inspector@2.13.0 --cli python -m bodhan_mcp --method tools/list
npx @modelcontextprotocol/inspector@2.13.0 --cli python -m bodhan_mcp --method tools/call \
  --tool-name translate_text --tool-arg text="The school opens at 9 AM." --tool-arg target_language=ta
```

`python scripts/client_check.py [--paid --env-file .env]` runs a fixed set of checks through that client and saves the transcript to `evaluation/clients/inspector.md`. Over stdio it lists tools, reads resources, makes a free call and sends four bad inputs. Over Streamable HTTP it checks that a missing or wrong token is refused, then lists tools and makes calls with the token. With `--paid` it adds one real one-sentence Bodhan translation per transport. Keys are read by the server from the env file and never appear on a command line or in the transcript.

`tests/test_mcp_stdio.py` and `tests/test_mcp_http.py` also drive the server as a separate process through the official MCP Python SDK client, over stdio and HTTP. They cover schemas, bad input, unknown tools, missing keys, the path sandbox, resources, unknown-resource errors and token checks.

Resources: `bodhan://voices` (the fixed voice library and styles), `bodhan://languages` (codes per model) and `bodhan://pricing`. An unknown resource URI returns the protocol error `-32602`. Each tool advertises an input schema and a typed output schema (`src/bodhan_mcp/tools/schemas.py`), and tests validate real results against them.

## Tools

| Tool | Bodhan model | What it handles for the caller |
|---|---|---|
| `inspect_files` | none | File type, duration, pages, image size and text language. Free. |
| `transcribe_audio` | indic-transcribe | Any common audio or video container (WAV, MP3, M4A/MP4, OGG/Opus, FLAC, WebM, AAC, AMR and others; playlists and manifests are refused), up to 60 minutes. Cut at pauses into ≤28 s pieces, silence trimmed, language detected from the script. Segment times included. |
| `create_subtitles` | transcribe + translate | Cue-sized `.srt` in the source and target languages, back-translation checked. |
| `translate_text` | indic-translate | Text, Markdown or SRT. Paragraph units, tables kept, truncation split, back-translation verified. |
| `synthesize_speech` | indic-speak | Sentence-sized requests, every segment back-transcribed and repaired once, MP3 or WAV, labelled AI-generated. |
| `ocr_document` | indic-ocr | Photos, multi-page TIFFs and PDFs, page by page. A clean PDF text layer is used for free unless the page also holds a large image. Markdown tables. Truncated pages are retried, then reported as incomplete. |
| `verify_translation`, `verify_speech` | translate / transcribe | Independent checks for outputs made elsewhere. |
| `detect_language`, `read_text`, `write_text`, `list_voices` | none | Script-based language ID, paging through artifacts, saving English text (with `source_paths` recording files it was based on; each must first be read in full with `read_text`, and after `--resume` it is read again), and the voice library. |

Every tool validates its input and returns `{"ok": false, "error": {"code", "message", "hint", "retryable"}}` on failure. Codes include `unsupported_language`, `unsupported_voice`, `file_not_found`, `budget_exceeded`, `rate_limited`, `provenance_violation` and others. See [docs/design.md](docs/design.md) for why the tools have this granularity and how each one handles its model's limits.

## How it works

1. **Preflight (code).** List the inputs with free metadata, record missing files and check which keys exist.
2. **Plan (LLM).** `record_plan` writes steps, assumptions and every deliverable the task asks for, each with its kind and language, or `answer` when the reply itself is the output. Paid tools are refused until a valid plan exists.
3. **Act (tools).** Independent calls in one turn run in parallel. Each call reserves its worst-case cost first, then calls Bodhan with retries and adaptive back-off, then verifies its output.
4. **Verify and repair.** Translations are back-translated and checked for script, numbers and table structure. Every audio segment is back-transcribed. Failures are repaired once with a different input, or reported.
5. **Stop.** `finish` passes a gate in code. Each declared deliverable needs a matching artifact from this run whose checks did not fail, including everything it was made from. The answer must be in English. Step, time and budget limits, an outage or a crash end with the same rule for what counts as usable output.

## Tests

```bash
pytest -q                                  # 120+ offline tests, no network or keys needed
python scripts/run_suite.py                # live self-evaluation: runs evaluation/tasks.json and scores it (exit 1 if any task fails)
```

The offline tests use `tests/fakes.py`, a mock of the four endpoints that enforces their limits, and a scripted planner. They cover:

- each tool's chunking and limits;
- retries and billing;
- budget reservation under concurrency;
- the gates and resume;
- the MCP server as a separate process over stdio and HTTP;
- the viewer;
- multi-step agent runs through the CLI code path.

`tests/test_review_regressions.py` and `tests/test_audit_regressions.py` hold one test per finding from the independent reviews.

`scripts/run_suite.py` is the self-evaluation. A task passes only if the agent exited cleanly, wrote a fresh report for that launch, and met every expectation in `evaluation/tasks.json`. The expectations cover status, deliverable files, kinds and languages, verification, answer text, and budget. These are structural checks, not a quality score; native-speaker review is separate. Live results are in [docs/evaluation.md](docs/evaluation.md).

## Test media

Media stays out of git. `scripts/make_fixtures.py` generates every input used in the demos into `sample_data/`. It needs the Bodhan keys, ffmpeg and Chrome or Edge, and costs about ₹1.6. All content is original English text written for this recipe. Bodhan translates it and speaks it with its fixed voices. Pages are rendered with a headless browser, for correct Indic text shaping, and then degraded to look like scans or phone photos. The repository's own `sample_data/sample_hindi.wav` is used for smoke tests.

## Limitations

- **Subtitle timing is approximate.** `indic-transcribe` returns no timestamps. When `create_subtitles` transcribes the audio itself, cue boundaries are the pauses where it cut the audio (frame resolution 20 ms; the speech inside a cue is not aligned). When it reuses an earlier transcript, long segments are split with times proportional to text length, which is coarser. Sparse speech can give a cue longer than `max_cue_seconds`; the result says so.
- **"Never the same input twice" has one exception.** Identical requests are served from the cache or share one in-flight call. The only repeats are retries of a failed or timed-out attempt; `report.json` counts them (`retried_attempts`), and their possible cost is counted as uncertain.
- **The planner's rupee cost is bounded, not measured in advance.** Bodhan spend is a hard cap. Planner tokens are always reported, and count toward `--max-cost-inr` only if you pass `--llm-*-inr-per-1m` prices. When priced, a call is sent only if the budget covers its upper bound: the request's UTF-8 bytes plus fixed margins as prompt tokens (no byte-level or byte-fallback tokenizer makes a token smaller than a byte), and `max_tokens` as output. On 14 recorded runs that bound was 4.9–5.4× the provider's reported prompt tokens, so a priced planner stops earlier than strictly needed. The bound assumes the provider honours `max_tokens` (including any reasoning tokens) and adds no large hidden prompt. If a reply still costs more, the excess is shown as `llm_over_pre_call_bound_inr` in `report.json` and as an `llm_estimate_exceeded` trace event.
- **Verification is automatic, not linguistic.** Back-translation chrF and back-transcription similarity catch omissions, truncation, wrong scripts and garbled audio. They cannot judge fluency or terminology. Native-speaker review is still needed.
- **Language ID is script-based.** Telugu, Tamil, Bengali and the other single-language scripts are reliable. Devanagari languages (Hindi, Marathi, Nepali and others) are told apart with function words, suffixes and letters such as ळ. Latin script counts as English only with English function words, at least one of which is not shared with French or romanized Hindi ("a", "on", "as", "to"); a short English line made only of such words is left unidentified, so it is translated or flagged rather than passed through. Short or unusual texts may be mislabelled, and the result says so with a confidence value.
- **Tested with one planner model.** The demos ran with `gpt-6.1-sol` behind an OpenAI-compatible local gateway. The code uses only standard `chat.completions` tool calling, and it accepts a JSON tool call written as plain text from weaker models, but other models may plan differently.
- **Latency is dominated by Indic-Speak.** About 20 s per 200-character request, plus planner turns. Single-step speech tasks often miss the 30 s target. `report.json` records each miss.
- **Durations are approximate.** A requested length such as "two minutes" is met by choosing the English summary length before translation, then checked. More than ±25 % is reported as a warning, not retried automatically.
- **No video output.** The agent can transcribe, translate and subtitle a video's speech, but it does not produce dubbed or edited video.

## AI assistance

Claude Code (Claude Opus 5.5) was the coding assistant for design, implementation, tests and docs. I checked every live run's outputs myself. GPT-6.1 Sol (through Codex) did several independent review rounds, including a full audit against the assignment. Each finding was reproduced, fixed, and covered by a regression test, or documented here as a limitation. The application's planner LLM is a separate, configurable model.

## Optional extras

All of the brief's stretch goals are implemented:

| Stretch goal | Where |
|---|---|
| Streamable HTTP with simple token authentication | `python -m bodhan_mcp --transport http` (see above), `tests/test_mcp_http.py` |
| MCP resources | `bodhan://voices`, `bodhan://languages`, `bodhan://pricing` |
| Self-evaluation script with a pass rate | `scripts/run_suite.py`, `evaluation/tasks.json` |
| Parallel tool calls where steps are independent | independent calls in one planner turn run concurrently; `test_independent_calls_*` |
| Resumable tasks | `--resume`, `src/bodhan_agent/state.py`, `test_resume_*` |
| Thin web UI with the plan and trace live | `python -m bodhan_agent.viewer`, `tests/test_viewer.py` |
