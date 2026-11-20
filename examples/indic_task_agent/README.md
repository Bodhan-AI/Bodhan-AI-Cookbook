# Indic task agent

> An MCP server for Bodhan's four Indic models, and a command-line agent that uses it to finish multi-step language tasks.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages shown:** Tamil, Hindi, Marathi, Telugu, Bengali, Kannada, Odia, Gujarati (and English)
**Format:** CLI agent + MCP server (stdio, or Streamable HTTP with a token)
**Demo video:** TODO (link added before the PR is opened) · **Results:** [docs/evaluation.md](docs/evaluation.md)

## What it does

- **`python -m bodhan_mcp`** is an MCP server. It exposes Bodhan's speech-to-text, translation, speech synthesis and OCR as 12 typed tools that any MCP client can use.
- **`python -m bodhan_agent`** takes a plain-language task and input files, plans with an LLM, calls the tools, verifies translations and generated audio (on by default), stays inside step, time and rupee budgets, and writes a report.

The planner can be any OpenAI-compatible endpoint with tool calling. It plans, writes English and judges results. Bodhan models do all speech recognition, translation, speech synthesis and OCR, and code refuses Indic-script text written by the planner (romanized text is not caught).

More detail: [docs/design.md](docs/design.md) (diagrams and design reasons), [docs/api_notes.md](docs/api_notes.md) (observed API behaviour) and [docs/evaluation.md](docs/evaluation.md) (every run, with outputs and traces).

## Results at a glance

The brief's six demo tasks, run cold (no cache) with `gpt-6.1-sol` as the planner. Each passed the structural checks of `scripts/run_suite.py`.

| Task | Status | Bodhan ₹ | Time (s) | Files |
|---|---|---|---|---|
| d1: Make a two-minute Tamil audio summary of this chapter (8-page scan) | completed_with_warnings | 2.80 | 258 | [result](evaluation/runs/sol_demos_cold/d1_chapter_tamil_audio/result.md) · [trace](evaluation/runs/sol_demos_cold/d1_chapter_tamil_audio/trace.jsonl) · [audio](evaluation/runs/sol_demos_cold/d1_chapter_tamil_audio/MEDIA.md) |
| d2: Transcribe this Hindi voice note and give me English subtitles (2 min 50 s) | completed | 0.31 | 323 | [result](evaluation/runs/sol_demos_cold/d2_voice_note_srt/result.md) · [trace](evaluation/runs/sol_demos_cold/d2_voice_note_srt/trace.jsonl) |
| d3: Translate this worksheet into Marathi and keep the table | completed | 0.21 | 59 | [result](evaluation/runs/sol_demos_cold/d3_worksheet_marathi/result.md) · [trace](evaluation/runs/sol_demos_cold/d3_worksheet_marathi/trace.jsonl) |
| d4: Which of these clips are in Telugu? Translate those into English | completed | 0.05 | 34 | [result](evaluation/runs/sol_demos_cold/d4_telugu_clips/result.md) · [trace](evaluation/runs/sol_demos_cold/d4_telugu_clips/trace.jsonl) |
| d5: Read this paragraph aloud in Bengali with a female voice | completed_with_warnings | 0.24 | 51 | [result](evaluation/runs/sol_demos_cold/d5_bengali_female_audio/result.md) · [trace](evaluation/runs/sol_demos_cold/d5_bengali_female_audio/trace.jsonl) · [audio](evaluation/runs/sol_demos_cold/d5_bengali_female_audio/MEDIA.md) |
| d6: Translate this video into French (impossible: no French model) | unsupported | 0.00 | 9 | [result](evaluation/runs/sol_demos_cold/d6_video_french/result.md) · [trace](evaluation/runs/sol_demos_cold/d6_video_french/trace.jsonl) |

Eight held-out tasks (prompt injection, unstated language, missing file, budget too small and others) and runs with other planners are in [docs/evaluation.md](docs/evaluation.md).

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
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | The planner: any OpenAI-compatible `chat.completions` endpoint that supports `tools` |

To use Docker instead, run these commands from `examples/indic_task_agent`. The agent example uses the repository's own `sample_data/sample_hindi.wav`:

```bash
docker build -t indic-task-agent .
docker run --rm -i --env-file .env indic-task-agent    # MCP server on stdio
mkdir -p out
docker run --rm --env-file .env -v "$PWD/../../sample_data:/data:ro" -v "$PWD/out:/work/out" indic-task-agent \
  python -m bodhan_agent --task "Transcribe this clip and give me English subtitles" --files /data/sample_hindi.wav --out ./out/smoke
```

The image runs as a non-root user (uid 1000) in `/work`. On Linux, that user must be able to write to the `out` folder.

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
  result.md      the answer, deliverables with their check results, assumptions, limitations, cost and time
  artifacts/     audio, subtitles, Markdown and transcripts
  trace.jsonl    one line per event: plan, LLM decision with its rationale, tool call with arguments, result summary, latency, cost, and every API attempt
  report.json    status, steps, check results, cost breakdown, time, and each quality target with why it was missed
```

`status` is one of `completed`, `completed_with_warnings`, `partial`, `failed` or `unsupported`. The process exits 0 whenever it wrote a report, including for honest failures.

Other flags:

- `--max-seconds` sets the wall-clock limit for the whole run (default 600). A slow planner may need 900.
- `--call-timeout` sets the limit per Bodhan request (default 60 s), and `--llm-timeout` per planner request (default 120 s).
- `--concurrency` sets parallel requests per model (default 8). The API allows 10, and a 429 halves it.
- `--no-cache` and `--cache-dir` control the result cache.
- `--env-file` loads the variables from another file.
- `--llm-input-inr-per-1m` and `--llm-output-inr-per-1m` count the planner's own spend in the budget.

`--max-steps` counts every planner call, `record_plan` and `finish` included.

### Resume an interrupted run

```bash
python -m bodhan_agent --resume --out ./out
```

The agent checkpoints to `out/.state/` around every planner turn, without keys. `--resume` checks that the inputs are unchanged, re-runs a cut-off turn and continues within the remaining limits; finished paid work comes from the run's cache. Each checkpoint also writes a provisional `report.json` and `result.md`, so a run stopped by Ctrl-C, SIGTERM (`docker stop`) or a crash still has a report ([details](docs/design.md#resume)).

### Watch a run live

```bash
python -m bodhan_agent.viewer --out ./out        # then open http://127.0.0.1:8766/
```

The viewer is a read-only page with the run's plan, steps, check results, cost and artifacts, refreshed once a second. It binds to localhost and refuses requests with any other Host header.

### Run the MCP server

```bash
python -m bodhan_mcp [--artifacts-dir DIR] [--max-cost-inr N] [--allow-dir DIR ... | --allow-any-path]
python -m bodhan_mcp --transport http --port 8765 [--token-file PATH]
```

The first command serves stdio. The second serves Streamable HTTP at `http://127.0.0.1:8765/mcp`; every request needs `Authorization: Bearer <token>`, and a missing token file gets a random token ([details](docs/design.md#http-transport)).

The tools read files only below the folder the server starts in, plus their own generated files. Pass `--allow-dir` for your media folder; a server started in `/` or your home folder reads generated files only until you do. Hidden files such as `.env`, network paths and URLs are always refused. `--allow-any-path` turns the folder limit off.

For MCP clients that take an `mcpServers` JSON block:

```json
{"mcpServers": {"bodhan": {"command": "python", "args": ["-m", "bodhan_mcp", "--artifacts-dir", "/tmp/bodhan", "--allow-dir", "/path/to/your/media"],
                           "env": {"BODHAN_TRANSCRIBE_KEY": "...", "BODHAN_TRANSLATE_KEY": "...", "BODHAN_SPEAK_KEY": "...", "BODHAN_OCR_KEY": "..."}}}}
```

**Tested off-the-shelf client: [MCP Inspector](https://github.com/modelcontextprotocol/inspector) CLI 2.13.0.**

```bash
npx @modelcontextprotocol/inspector@2.13.0 --cli python -m bodhan_mcp --method tools/list
npx @modelcontextprotocol/inspector@2.13.0 --cli python -m bodhan_mcp --method tools/call \
  --tool-name translate_text --tool-arg text="The school opens at 9 AM." --tool-arg target_language=ta
```

`python scripts/client_check.py [--paid --env-file .env]` runs a fixed set of Inspector checks over stdio and HTTP and saves the transcript to [evaluation/clients/inspector.md](evaluation/clients/inspector.md).

Resources: `bodhan://voices`, `bodhan://languages`, `bodhan://pricing` and `bodhan://errors` (every error code and what to do). Prompt: `plan_media_task` (arguments `task` and `files`). Each tool has an input schema and a typed output schema (`src/bodhan_mcp/tools/schemas.py`).

## Tools

| Tool | Bodhan model | What it handles for the caller |
|---|---|---|
| `inspect_files` | none | File type, duration, pages, image size and text language. Free. |
| `transcribe_audio` | indic-transcribe | Common audio and video containers up to 60 minutes. Cuts at pauses into ≤28 s pieces, trims silence, and detects the language from the script of the first piece. |
| `create_subtitles` | transcribe + translate | Cue-sized `.srt` files in the source and target languages, back-translation checked. |
| `translate_text` | indic-translate | Text, Markdown or SRT. Paragraph units, tables kept, truncation split, back-translation checked. |
| `synthesize_speech` | indic-speak | Requests of ≤220 characters, every segment back-transcribed and repaired once, MP3 or WAV, labelled AI-generated. |
| `ocr_document` | indic-ocr | Photos (including iPhone HEIC), multi-page TIFFs and PDFs, page by page, with Markdown tables. A clean PDF text layer is used for free. Truncated pages are retried, then reported. |
| `verify_translation`, `verify_speech` | translate / transcribe | The same checks, for outputs made elsewhere. |
| `detect_language`, `read_text`, `write_text`, `list_voices` | none | Script-based language ID, paging through artifacts, saving English text, and the voice library. |

Every tool validates its input and returns `{"ok": false, "error": {"code", "message", "retryable", "hint"?, "details"?}}` on failure, for example `unsupported_language` or `budget_exceeded`. Why the tools have this shape is in [design.md](docs/design.md#tool-granularity).

## How it works

1. **Preflight (code):** list the inputs with free metadata, note missing files, check the keys.
2. **Plan (LLM):** `record_plan` declares steps, assumptions and every deliverable with its kind and language (`answer` when the reply itself is the output). Paid tools are refused until a valid plan exists.
3. **Act (tools):** independent calls in one turn run in parallel. Each call reserves its worst-case cost, calls Bodhan with retries, then checks its output.
4. **Check and repair:** translations are back-translated and checked for script, numbers and tables. Every audio segment is back-transcribed. A failure is repaired with a different input (a speech segment once), or reported.
5. **Stop:** `finish` passes a gate in code. Each deliverable needs a matching artifact from this run whose checks, and those of its sources, did not fail, and the answer must be in English. Limits, an outage or a crash end the run under the same rule.

The flow chart and the reasons behind it are in [docs/design.md](docs/design.md).

## Tests

```bash
pytest -q                                  # 245 offline tests, no network or keys needed
python scripts/run_suite.py                # live self-evaluation of evaluation/tasks.json (exit 1 if any task fails)
```

The offline tests run against `tests/fakes.py`, a mock of the four endpoints that enforces their limits, with a scripted planner. They cover chunking, retries, billing, budgets under concurrency, the gates, resume, the MCP server as a separate process, and multi-step CLI runs. The `tests/test_*_regressions.py` files hold one test per review finding. `scripts/run_suite.py` checks status, deliverables, languages, checks, answer text and budget per task: structural checks, not a quality score.

## Test media

Media stays out of git. `scripts/make_fixtures.py` generates every demo input into `sample_data/` (it needs the Bodhan keys, ffmpeg and Chrome or Edge, and costs about ₹1.6). The content is original English text written for this recipe, which Bodhan translates and speaks. A headless browser renders the pages, which are then degraded to look like scans or phone photos. Every demo audio input is therefore synthetic speech.

## Limitations

- **Subtitles cost three requests per cue** (transcription, translation, back-translation), and cues are about 7 s. d2's 2 min 50 s voice note had 30 cues: 90 requests plus 41 rate-limited attempts, 323 s against a 300 s target.
- **Subtitle timing is approximate.** `indic-transcribe` returns no timestamps, so cue boundaries are the pauses where the audio was cut.
- **The checks are automatic, not linguistic.** Back-translation and back-transcription catch omissions, truncation, wrong scripts and garbled audio. They cannot judge fluency or terminology, and no fluent speaker has reviewed the outputs yet.
- **Language ID is script-based.** Single-language scripts such as Telugu and Tamil are reliable. Devanagari languages and short Latin-script lines can be mislabelled; the result gives a confidence ([details](docs/design.md#language-identification)).
- **Planner cost is bounded, not measured in advance.** With planner prices given, a call is sent only if the budget covers its upper bound, about 5× the real prompt size, so a priced planner stops early ([details](docs/design.md#budgets-cost-and-caching)).
- **Latency is dominated by Indic-Speak**, about 20 s per request, so single-step speech tasks often miss the 30 s target (recorded in `report.json`).
- **Durations are approximate.** A requested length sets the English text length before translation. Missing it by more than ±25 % is a warning, not a retry.
- **Planners differ.** `gemini-3.5-flash-lite` passed 10/10 and `gpt-5.6-luna` 9/10. `gpt-oss-120b` passed 4/10 through an OpenAI-compatible gateway, where it often returned empty turns instead of finishing. On Groq's free tier (8,000 tokens a request, 200,000 a day) `gpt-oss` ran out of quota before the tasks were done ([results](docs/evaluation.md#other-planner-models)).
- **Word, Excel, PowerPoint files and archives are not read.** They are refused with a hint to export them to PDF or text, or to extract the archive.
- **No dubbed video.** The agent can transcribe, translate and subtitle a video's speech, but it cannot re-voice the video.

## AI assistance

Claude Code (Claude Opus 5.5) was the coding assistant for design, implementation, tests and docs. I read every `result.md`, `report.json` and English text artifact of the demo and held-out runs, listened to the generated audio, and checked each audio file's duration and back-transcription in its report. I did not judge the fluency of the Indian-language outputs, and no fluent speaker has reviewed them yet. GPT-6.1 Sol (through Codex) and CodeRabbit reviewed the code in several rounds, including a full audit against the brief, and a separate Claude Opus session did a read-only security review. Each finding was reproduced, then fixed with a regression test or listed as a limitation. The agent's planner LLM is a separate, configurable model.

## Optional extras

Every stretch goal in the brief is implemented, each with a test and a live run or transcript ([evidence](docs/evaluation.md#stretch-goals)):

- Streamable HTTP with token authentication: `python -m bodhan_mcp --transport http`.
- MCP resources and a prompt: `bodhan://voices`, `bodhan://languages`, `bodhan://pricing`, `bodhan://errors` and `plan_media_task`.
- A self-evaluation script with a pass rate: `scripts/run_suite.py`.
- Parallel tool calls and resumable tasks: independent calls in one turn run together; `--resume`.
- A thin live web UI: `python -m bodhan_agent.viewer`.
