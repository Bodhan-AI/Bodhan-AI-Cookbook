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
```

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

### Run the MCP server

```bash
python -m bodhan_mcp [--artifacts-dir DIR] [--max-cost-inr N] [--allow-dir DIR ...]
```

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

`tests/test_mcp_stdio.py` also drives the server as a separate process through the official MCP Python SDK client. It covers schemas, bad input, unknown tools, missing keys, the path sandbox and resources.

## Tools

| Tool | Bodhan model | What it handles for the caller |
|---|---|---|
| `inspect_files` | none | File type, duration, pages, image size and text language. Free. |
| `transcribe_audio` | indic-transcribe | Any container or length. Cut at pauses into ≤28 s pieces, silence trimmed, language detected from the script. Segment times included. |
| `create_subtitles` | transcribe + translate | Cue-sized `.srt` in the source and target languages, back-translation checked. |
| `translate_text` | indic-translate | Text, Markdown or SRT. Paragraph units, tables kept, truncation split, back-translation verified. |
| `synthesize_speech` | indic-speak | Sentence-sized requests, every segment back-transcribed and repaired once, MP3 or WAV, labelled AI-generated. |
| `ocr_document` | indic-ocr | Photos and multi-page PDFs page by page. Free use of clean PDF text layers. Markdown tables. |
| `verify_translation`, `verify_speech` | translate / transcribe | Independent checks for outputs made elsewhere. |
| `detect_language`, `read_text`, `write_text`, `list_voices` | none | Script-based language ID, paging through artifacts, saving English text, and the voice library. |

Every tool validates its input and returns `{"ok": false, "error": {"code", "message", "hint", "retryable"}}` on failure. Codes include `unsupported_language`, `unsupported_voice`, `file_not_found`, `budget_exceeded`, `rate_limited`, `provenance_violation` and others. See [docs/design.md](docs/design.md) for why the tools have this granularity and how each one handles its model's limits.

## How it works

1. **Preflight (code).** List the inputs with free metadata, record missing files and check which keys exist.
2. **Plan (LLM).** `record_plan` writes steps, assumptions and deliverables. Paid tools are refused until a plan exists.
3. **Act (tools).** Each call reserves its worst-case cost first, then calls Bodhan with retries and adaptive back-off, then verifies its output.
4. **Verify and repair.** Translations are back-translated and structure-checked. Every audio segment is back-transcribed. Failures are repaired once with a different input, or reported.
5. **Stop.** `finish` passes a gate in code. Deliverables must be this run's artifacts, verified and not failing, and the answer must be in English. Step, time and budget limits force a truthful partial report.

## Tests

```bash
pytest -q                                  # 60+ offline tests, no network or keys needed
python scripts/run_suite.py                # live: runs evaluation/tasks.json and scores the outputs (costs about ₹5)
```

The offline tests use `tests/fakes.py`, a mock of the four endpoints that enforces their limits, and a scripted planner. They cover each tool's chunking and limits, retries and billing, budget reservation under concurrency, the gates, the MCP server as a separate process, and multi-step agent runs through the CLI code path. Live behaviour is shown separately in [docs/evaluation.md](docs/evaluation.md).

## Test media

Media stays out of git. `scripts/make_fixtures.py` generates every input used in the demos into `sample_data/`. It needs the Bodhan keys, ffmpeg and Chrome or Edge, and costs about ₹1.6. All content is original English text written for this recipe. Bodhan translates it and speaks it with its fixed voices. Pages are rendered with a headless browser, for correct Indic text shaping, and then degraded to look like scans or phone photos. The repository's own `sample_data/sample_hindi.wav` is used for smoke tests.

## Limitations

- **Subtitle timing is pause-based.** `indic-transcribe` returns no timestamps, so cue times come from where the audio was cut, accurate to about 0.3 s. Cues are at most 7 s.
- **Verification is automatic, not linguistic.** Back-translation chrF and back-transcription similarity catch omissions, truncation, wrong scripts and garbled audio. They cannot judge fluency or terminology. Native-speaker review is still needed.
- **Language ID is script-based.** Telugu, Tamil, Bengali and the other single-language scripts are reliable. Devanagari languages (Hindi, Marathi, Nepali and others) are told apart with function words, suffixes and letters such as ळ. Short or unusual texts may be mislabelled, and the result says so with a confidence value.
- **Tested with one planner model.** The demos ran with `gpt-6.1-sol` behind an OpenAI-compatible local gateway. The code uses only standard `chat.completions` tool calling, and it accepts a JSON tool call written as plain text from weaker models, but other models may plan differently.
- **Latency is dominated by Indic-Speak.** About 20 s per 200-character request, plus planner turns. Single-step speech tasks often miss the 30 s target. `report.json` records each miss.
- **Durations are approximate.** A requested length such as "two minutes" is met by choosing the English summary length before translation, then checked. More than ±25 % is reported as a warning, not retried automatically.
- **No video output.** The agent can transcribe, translate and subtitle a video's speech, but it does not produce dubbed or edited video.

## AI assistance

Written with Claude Code (Claude Opus 5.5) as the coding assistant: design, implementation, tests and docs, with every live run checked by hand. GPT-6.1 Sol (through Codex) did an independent code review. The application's planner LLM is a separate, configurable model.
