# Indic task agent

> An MCP server for Bodhan's four Indic models, and a command-line agent that uses it to finish multi-step language tasks.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages shown:** Tamil, Hindi, Marathi, Telugu, Bengali, Kannada, Odia, Gujarati (and English)
**Format:** CLI agent + MCP server (stdio, or Streamable HTTP with a token)
**Demo video:** https://youtu.be/a710wLpPRCk · **Results:** [docs/evaluation.md](docs/evaluation.md)

## What it does

- **`python -m bodhan_mcp`** is an MCP server. It exposes Bodhan's speech-to-text, translation, speech synthesis and OCR as 12 typed tools that any MCP client can use.
- **`python -m bodhan_agent`** takes a plain-language task and input files, plans with an LLM, calls the tools, verifies translations and generated audio (on by default), stays inside step, time and rupee budgets, and writes a report.

The planner can be any OpenAI-compatible endpoint with tool calling. It plans, writes English and judges results. Bodhan models do all speech recognition, translation, speech synthesis and OCR, and code refuses Indic-script text written by the planner (romanized text is not caught).

More detail: [docs/design.md](docs/design.md) (the [architecture diagram](docs/design.md#architecture), the [agent-loop flow chart](docs/design.md#agent-loop) and the design notes), [docs/api_notes.md](docs/api_notes.md) (observed API behaviour) and [docs/evaluation.md](docs/evaluation.md) (every run, with outputs and traces).

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

The demos ran on `e648654`; every main task passed again on `92a3518` ([14/14](evaluation/runs/portability_gpt-6.1-sol/summary.md)). The product changes since `92a3518` are wording-only (field descriptions and messages; the final source `9622a0d` was spot-checked); `c0afd6a` separately tightens scoring ([details](docs/evaluation.md#planner-portability)).

A real human recording, a 60 s Hindi WhatsApp voice note in my own voice, was also run cold: the Hindi transcript has every fact, but the English subtitles lost one negation ("electricity will not be affected") and added a digit to a phone number, and the automated back-check passed both; the summary left those two lines out rather than guess ([hand check](docs/evaluation.md#runs-recorded-for-the-demo-video-and-a-real-human-recording)).

Eight test tasks of my own (prompt injection, unstated language, missing file, budget too small and others), six more written by a separate AI agent (Claude Opus) that had not seen our prompts or code (first attempts: 4/6 with each of two planners, failures and fixes reported), and runs with other planners are in [docs/evaluation.md](docs/evaluation.md).

**Three commands** (details under Setup and Run):

```bash
pip install -r requirements.txt                    # in a Python 3.11+ virtual environment; keys go in .env
python -m bodhan_agent --task "Transcribe this clip and give me English subtitles" --files ../../sample_data/sample_hindi.wav --out ./out
python -m bodhan_mcp                               # the MCP server on stdio
```

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

The image runs as a non-root user (uid 1000) in `/work`. On Linux, that user must be able to write to the `out` folder. In PowerShell, write `${PWD}` for `$PWD` and end continued lines with a backtick (`` ` ``) instead of `\`, or put the command on one line.

## Run

### Run the agent

```bash
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out \
  --max-steps 30 --max-cost-inr 20
```

`--files` takes files, folders or both. The demo inputs are generated (see [Test media](#test-media)); to try the agent straight after setup, use the repository's own 6-second clip (about ₹0.01):

```bash
python -m bodhan_agent --task "Transcribe this clip and give me English subtitles" --files ../../sample_data/sample_hindi.wav --out ./out/smoke
```

Each run writes:

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

For MCP clients that take an `mcpServers` JSON block, set `command` to the virtual environment's Python (`.venv/bin/python`, or `.venv\Scripts\python.exe` on Windows) as an absolute path. A bare `python` from the client's own environment lacks the packages, and the client only reports that the connection closed.

```json
{"mcpServers": {"bodhan": {"command": "/path/to/examples/indic_task_agent/.venv/bin/python", "args": ["-m", "bodhan_mcp", "--artifacts-dir", "/tmp/bodhan", "--allow-dir", "/path/to/your/media"],
                           "env": {"BODHAN_TRANSCRIBE_KEY": "...", "BODHAN_TRANSLATE_KEY": "...", "BODHAN_SPEAK_KEY": "...", "BODHAN_OCR_KEY": "..."}}}}
```

**Tested off-the-shelf client: [MCP Inspector](https://github.com/modelcontextprotocol/inspector) CLI 2.13.0** (needs Node.js; run it with the virtual environment active).

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
5. **Stop:** `finish` passes a gate in code. Each deliverable needs a matching artifact from this run whose checks, and those of its sources, did not fail, and the answer must be in English. A revised plan cannot silently drop an earlier output, and before `completed` the planner model re-reads the original task, without the plan, against what was delivered. Limits, an outage or a crash end the run under the same rule.

The flow chart and the reasons behind it are in [docs/design.md](docs/design.md#agent-loop).

## Tests

```bash
pytest -q                                  # 298 offline tests, no network or keys needed
python scripts/run_suite.py                # live self-evaluation of evaluation/tasks.json (exit 1 if any task fails)
python scripts/run_suite.py --score-only --out evaluation/runs/sol_demos_cold --summary-dir /tmp/rescore   # re-score committed evidence offline
```

The offline tests run against `tests/fakes.py`, a mock of the four endpoints that enforces their limits, with a scripted planner. They cover chunking, retries, billing, budgets under concurrency, the gates, resume, the MCP server as a separate process, and multi-step CLI runs. The `tests/test_*_regressions.py` files each pin a fixed bug or edge case. `scripts/run_suite.py` checks status, deliverables, languages, checks, answer text and budget per task: structural checks, not a quality score.

## Test media

Media stays out of git. `scripts/make_fixtures.py` generates every demo input into `sample_data/` (it needs the Bodhan keys, ffmpeg and Chrome or Edge, and costs about ₹1.6). The content is original English text written for this recipe, which Bodhan translates and speaks. A headless browser renders the pages, which are then degraded to look like scans or phone photos. Every demo audio input is therefore synthetic speech.

Downloads (release `media-v1`): the generated demo inputs as [demo_inputs.zip](https://github.com/burntcookiedough/Bodhan-AI-Cookbook/releases/download/media-v1/demo_inputs.zip), every generated audio file linked from its run's `MEDIA.md`, the real human recording ([hindi_notice_anshul.mp4](https://github.com/burntcookiedough/Bodhan-AI-Cookbook/releases/download/media-v1/hindi_notice_anshul.mp4), my own voice, a WhatsApp voice note) and [SHA256SUMS](https://github.com/burntcookiedough/Bodhan-AI-Cookbook/releases/download/media-v1/SHA256SUMS).

## Limitations

Each item says what falls short, what is delivered, and why a further fix was not made.

- **Romanized Indian-language text is checked by the prompt only.** Code refuses Indic-script text the planner writes, but a romanized line would pass. Catching it would need language ID on short Latin-script text, which is unreliable.
- **Selecting inputs by a condition can miss or add a file** (Gemini on h14). The run delivers the files it chose and names them. Letting the finish-time check read every input could catch it, but would put untrusted document text into that check.
- **The back-translation check misses changes of meaning.** In native review, the d2 English subtitles scored 3/5 for meaning (the name Ravi became "John", a reminder went to the wrong person, a sentence is missing) and every cue had passed the check; on my own recording a lost negation and an extra digit also passed. It catches omissions, truncation, lost numbers, wrong scripts and garbled audio, not substituted names or meanings. Other reviewed outputs scored 4–5, Tamil by an independent native reader ([native review](docs/evaluation.md#native-speaker-review); Bengali, Kannada and Gujarati not reviewed).
- **The completion re-read is the same planner model checking itself.** It catches dropped outputs, but it is not independent; a second model would need configuration beyond the three `LLM_*` variables.
- **Speech and subtitle timing are approximate.** A requested length sets the English text length; missing it by more than ±25 % is a warning, not a new synthesis with the most expensive model. Cue times come from pause cuts, because `indic-transcribe` returns no timestamps.
- **d2 missed its latency target when run cold: 323 s against 300 s.** Subtitles take three requests per cue (transcribe, translate, back-translate): its 30 cues needed 90 requests plus 41 rate-limited attempts. All 30 cues were delivered and verified; dropping the back-translation to save time was rejected.
- **No waste, with one exception.** An identical request is not paid twice in a run, except that an uncertain timeout or a retry may resend the same input; `report.json` counts retries and uncertain attempts.
- **Language ID is script-based.** Single-language scripts such as Telugu and Tamil are reliable; Devanagari languages and short Latin-script lines can be mislabelled, and the result gives a confidence ([details](docs/design.md#language-identification)).
- **Planner cost is bounded, not measured in advance.** With planner prices set, each call reserves an upper bound (about 5× the real prompt), so a tight budget ends a run earlier than its exact cost would ([details](docs/design.md#budgets-cost-and-caching)).
- **Indic-Speak dominates latency**, about 20 s per request, so single-step speech tasks often miss the 30 s target (recorded in `report.json`).
- **Planners differ.** On the seven-task portability subset, `gpt-6.1-sol` and `gemini-3.5-flash-lite` passed 7/7 and `claude-sonnet-4-6` 6/7 ([portability table](docs/evaluation.md#planner-portability)). On earlier code, `gpt-5.6-luna` passed 9/10, and `gpt-oss-120b` (medium) passed 4/10, often returning empty turns instead of finishing.
- **Word, Excel, PowerPoint files and archives are not read.** They are refused with a hint to export them to PDF or text, or to extract the archive.
- **Injected text is kept in faithful translations.** In the recorded runs no injected instruction was followed. Messages the planner composes leave it out; a faithful translation, transcript or quote keeps it as content.
- **No dubbed video.** The agent transcribes, translates and subtitles a video's speech, but cannot re-voice the video.

## AI assistance

I ran the work as a small multi-agent loop in T3 Code. One Claude Opus 5.5 session (high effort) wrote the design, code, tests and docs. A second Opus 5.5 session acted as a gatekeeper: it checked each change against the brief, cut scope creep and sent fixes back. Independent reviewers attacked each milestone: GPT-6.1 Sol (through Codex), CodeRabbit, an Astra audit, and a read-only Opus security review. No finding was fixed until it had been reproduced. Each one then got a fix with a regression test, or a line in the limitations, and was logged against the commit that resolved it. I read every result and report, listened to the generated audio, and nothing was published without my sign-off. Native speakers reviewed the Tamil (Madhav) and Marathi (Yuvraj) outputs; I reviewed the Hindi, Telugu and Odia ones myself, which is weaker evidence ([native review](docs/evaluation.md#native-speaker-review)). The agent's planner LLM is a separate, configurable model.

## Optional extras

Every stretch goal in the brief is implemented and tested; the evidence table distinguishes live runs from offline subprocess checks (resuming after a kill is proven by offline subprocess kill/resume tests) ([evidence](docs/evaluation.md#stretch-goals)):

- Streamable HTTP with token authentication: `python -m bodhan_mcp --transport http`.
- MCP resources and a prompt: `bodhan://voices`, `bodhan://languages`, `bodhan://pricing`, `bodhan://errors` and `plan_media_task`.
- A self-evaluation script with a pass rate: `scripts/run_suite.py`.
- Parallel tool calls and resumable tasks: independent calls in one turn run together; `--resume`.
- A thin live web UI: `python -m bodhan_agent.viewer`.
