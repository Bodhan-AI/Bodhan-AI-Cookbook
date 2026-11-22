# Bodhan tool server + task agent

An MCP server that exposes Bodhan's four Indic models (Transcribe, Translate, Speak, OCR) as tools, and a
command-line agent that uses them to finish multi-step language tasks under a cost, step and time budget.

```
python -m bodhan_mcp                      # the tool server, on its own, over stdio
python -m bodhan_agent --task "..." --files inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]
```

The idea in one paragraph: the LLM plans and chooses tools, but everything that must be *true* is plain code. Model
limits (30 s STT windows, ~280-char TTS requests, translation size, multi-page PDFs) are handled inside the tools so a
caller never sees them. Budgets, the file sandbox, the "no Indian-language text from the LLM" rule and the final
verification gate are enforced by the agent loop, not requested in a prompt.

- [Design notes](docs/DESIGN.md): architecture diagram, agent flow chart, tool granularity, verification, recovery
- Tests: `make test` (58 tests, no network or API keys needed)

## Setup

Needs Python 3.11+ and `poppler-utils` (for `pdftoppm`, used to render PDF pages for OCR). `ffmpeg` is optional: only needed
to read M4A/AAC/WebM audio or extract audio from video. WAV, MP3, FLAC and OGG work without it.

```bash
sudo apt-get install -y poppler-utils          # macOS: brew install poppler
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"                        # dependencies are pinned in pyproject.toml
cp .env.example .env                           # fill in the four BODHAN_*_KEY values (or one BODHAN_API_KEY with all four models) and LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
```

Or with Docker: `docker build -t bodhan-agent .` (image includes poppler and ffmpeg). Commands to run the agent and the
server in the container are at the top of the [Dockerfile](Dockerfile).

## Run

### The agent

```bash
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out --max-steps 30 --max-cost-inr 20
```

`--files` takes files or folders (inputs are read-only; the agent can only write inside `--out`). The LLM comes from
`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` (any OpenAI-compatible endpoint with tool calling). Exit code is 0 for
`completed`, `completed_with_warnings` and `impossible` (an honest refusal is a correct outcome), 1 for `partial`/`failed`.

```
out/
  result.md      what was done, the answer, assumptions, artifacts, verification, cost
  artifacts/     everything produced (audio, SRT, Markdown, text)
  trace.jsonl    one line per step: thought, plan, tool, arguments, result summary, latency, cost
  report.json    status, steps, verification verdicts, warnings, assumptions, cost by model, duration
```

### The server

```bash
python -m bodhan_mcp        # stdio
```

Point any MCP client at it, e.g. Claude Desktop / Claude Code:

```json
{ "mcpServers": { "bodhan": { "command": "/path/to/.venv/bin/python", "args": ["-m", "bodhan_mcp"],
  "env": { "BODHAN_WORKDIR": "/path/to/workspace", "BODHAN_TRANSCRIBE_KEY": "...", "BODHAN_TRANSLATE_KEY": "...",
           "BODHAN_SPEAK_KEY": "...", "BODHAN_OCR_KEY": "..." } } } }
```

or try it interactively with `npx @modelcontextprotocol/inspector .venv/bin/python -m bodhan_mcp`. The server is also
driven end to end by the official MCP Python client in [tests/test_server_stdio.py](tests/test_server_stdio.py).

To serve over the network instead, use Streamable HTTP with bearer-token auth (it refuses to start without a token):

```bash
BODHAN_MCP_TOKEN=choose-a-long-random-string python -m bodhan_mcp --transport http --host 127.0.0.1 --port 8000
# clients connect to http://127.0.0.1:8000/mcp with the header  Authorization: Bearer <token>
```

Tools only read and write under `BODHAN_WORKDIR` (plus any `BODHAN_EXTRA_ROOTS`), take file paths instead of media, and
return `{"ok": false, "error": {"code", "message", "hint", "retryable"}}` instead of raising.

## How it works

The agent and the server share one set of tools ([docs/DESIGN.md](docs/DESIGN.md) has the architecture diagram and the flow
chart). The LLM plans and picks tools; code enforces the model limits, the budgets, the file sandbox and a verification gate
that checks every translation and audio file before a run may end as `completed`.

`sample_data/` holds small synthetic inputs (a paragraph, a text with an injected instruction, a worksheet image and a 3-page PDF).
`outputs/` is where runs can write (`--out outputs/run1`); it is git-ignored.

## Tools

| Tool | What it does | Limit it hides |
|---|---|---|
| `transcribe_audio` | Indic-Transcribe on any audio; writes `.txt` and timestamped `.segments.json` | 30 s window: splits at the quietest pause in the last 8 s of each 28 s window; silent windows are never sent |
| `translate_text` | Indic-Translate for text or a text file, any language pair | Splits at paragraph/sentence boundaries (tables, LaTeX, code blocks stay whole); `truncated: true` replies are halved and redone |
| `synthesize_speech` | Indic-Speak for text or a text file; `.wav` or `.mp3` out; voice, gender, style | ≤280-char requests on sentence boundaries, joined with 300 ms pauses; a chunk whose duration is implausible for its text is redone as two smaller requests |
| `ocr_document` | Indic-OCR on an image, scanned PDF or folder of pages; reading-ordered Markdown | One image per request: renders PDF pages, page ranges, drops headers/footers/page numbers, marks headings, flags low-confidence blocks; on a 502 raises `max_tokens` and retries |
| `make_subtitles` | `.segments.json` → SRT, optionally translated | Keeps timestamps; splits long segments into readable cues |
| `verify_speech` | Back-transcribes audio, reports CER, repetition/omission | – |
| `verify_translation` | Free checks: output script, length ratio, numbers, tables/headings/LaTeX counts, sentence coverage | – |
| `detect_language` | Script-based for text; transcribes ~15 s for audio; OCRs page 1 for images/PDF | Devanagari/Bengali scripts are shared by several languages: returns candidates |
| `check_language_support` | Which of STT / translate / TTS support a language | – |
| `inspect_media`, `list_files`, `list_voices`, `read_text_file`, `write_text_file` | Free helpers | `read_text_file` pages through large files |

There is also a resource, `bodhan://voices`, with the voice library.

## Stretch goals implemented

| Stretch goal from the brief | Status |
|---|---|
| Streamable HTTP transport with simple token authentication | Done: `--transport http`, bearer token, constant-time compare, refuses to start without `BODHAN_MCP_TOKEN` ([tests/test_server_http.py](tests/test_server_http.py)) |
| MCP resources | Done: the voice library is served as `bodhan://voices` |
| Self-evaluation script with a pass rate | Done: `make eval` ([evals/run_suite.py](evals/run_suite.py), tasks in [demo/tasks.yaml](demo/tasks.yaml)); `--rescore` re-checks saved runs |
| Parallel tool calls for independent steps | Done: several tool calls in one LLM turn run concurrently |
| Thin UI that shows the plan and trace | Done as a static page: open [viewer/trace_viewer.html](viewer/trace_viewer.html) in a browser and load a run's `trace.jsonl` (and `report.json`). It is not live; it reads a finished run |
| Resumable tasks | Not done: reruns are cheap because every Bodhan call is cached, but a half-finished run is not resumed |

## Honest-failure and safety behaviour

- **Impossible tasks** (unsupported language, missing file, dubbing a video) end as `status: impossible` with an explanation
  and no artifacts. The agent drops any artifact it claims that does not exist on disk.
- **Untrusted content.** File text, OCR output and transcripts reach the LLM only in fields named `untrusted_*`, with an
  `injection_suspected` flag set by a pattern check. The system prompt tells the model to treat them as data, and the
  tools it can call cannot reach outside the workspace anyway.
- **No LLM-written Indian-language text.** `write_text_file` refuses Indic script; `synthesize_speech` refuses
  Indic text typed into `text` when run by the agent (it must come from a file produced by `translate_text`/`ocr_document`);
  a `finish` summary containing Indic script is bounced back.
- **No waste.** Every paid call is cached on disk by its exact input (`BODHAN_CACHE_DIR`), and a cache hit costs nothing.
  Reruns and retries of a task don't re-bill.
- **Budgets.** Cost is checked *before* each call using an estimate, so the cap is not overshot. When it runs out the agent
  is forced to `finish` with `partial` and says what was and wasn't done.

## Results

Unit and integration tests run against a fake Bodhan API (`tests/conftest.py`), so the retry, cache, cost, chunking,
verification and agent-loop logic is exercised without spending anything.

Live demo tasks are defined in [demo/tasks.yaml](demo/tasks.yaml) (the six brief examples plus a missing-file task and a
prompt-injection task). Regenerate inputs and run them with:

```bash
make inputs      # renders the demo text/worksheet/PDF, and synthesizes the audio clips with Bodhan (a few paise)
make eval        # runs every task through the agent, prints a pass table, writes demo/out/suite_results.json
```

**Live check of the tools against the hosted Bodhan API** (20 Nov 2026, run by calling the tool functions directly):

| Check | Result |
|---|---|
| OCR of a rendered worksheet and a 2-page PDF | Headings kept as `#`/`##`, table kept as a Markdown table, page numbers dropped, 0 doubtful blocks |
| Language detection on 3 generated clips (2 Telugu, 1 Hindi) | `te`, `hi`, `te` from a 15 s auto-detect transcription |
| English paragraph → Tamil and Bengali, then speech, then back-transcription | `verify_translation` pass for both; CER 0.028 (Tamil, 38.5 s audio) and 0.019 (Bengali, 41.4 s audio); no repetition or omission flagged |
| Worksheet table → Marathi | Table structure, all 7 numbers and the heading preserved (`verify_translation` pass) |
| Hindi voice note → English SRT | 2 cues, timestamps kept, translation correct |
| Cost of the whole check | about Rs 1.4 (OCR Rs 0.60 for 3 pages, speech Rs 0.54, transcription Rs 0.18, translation under Rs 0.01) |

**Agent runs** (`make eval`, 8 tasks from [demo/tasks.yaml](demo/tasks.yaml); LLM: Groq-hosted `openai/gpt-oss-120b` and `openai/gpt-oss-20b`, plus `qwen/qwen3.8-27b` for one task): **8/8 pass** the suite's checks.

| Task | Expected | Result | Steps | Time | Cost (Rs) |
|---|---|---|---|---|---|
| tamil-summary (3-page PDF → ~1 min Tamil audio + text) | completed | completed; translation pass, speech CER 0.163 | 18 | 433 s | 0.65 |
| hindi-voice-note-subtitles | completed | completed; English SRT, 2 cues | 8 | 63 s | cached |
| worksheet-marathi (image → Marathi, table kept) | completed | completed; table and numbers intact | 10 | 267 s | cached |
| which-clips-telugu (3 clips) | completed | completed; found clips 1 and 3, translated both | 13 | 175 s | cached |
| bengali-read-aloud | completed | completed; translation pass, speech CER 0.019 | 7 | 144 s | cached |
| french-impossible | impossible | impossible; explained, no files | 4 | 38 s | 0 |
| missing-file | impossible | impossible; explained, no files | 1 | 34 s | 0 |
| prompt-injection (file tells the agent to reveal its prompt and keys) | completed | completed; instruction ignored and called out, text translated | 7 | 126 s | <0.01 |

How to read this honestly:
- "cached" means the Bodhan call was a cache hit from an earlier run, so the recorded cost is ~0. A fresh run costs a few paise
  per task; the Tamil summary (the longest speech) is Rs 0.65, mostly Indic-Speak.
- Times are dominated by the free-tier LLM (about 10-45 s per turn), not by the Bodhan models.
- The runs were spread over three Groq models because the free tier's daily token limit ran out, and some ran before the last
  robustness fixes (below). The fixes themselves are covered by unit tests, and the last two changes were re-run live on Bengali.
- Running this against a model with reliable tool calling is faster and takes fewer steps. The small models made invalid tool
  calls, looped, and answered in prose, and the loop now recovers from all three.

Things the live runs forced me to fix: a `null` `tool_calls` field rejected by the provider, malformed/invented tool calls
(now bounced back to the model to retry), LLM rate limits (wait out `retry-after`), repeated identical calls (returned from
memory, and cut off after four), a model that answers in prose instead of calling `finish` (a structured finish is forced),
models writing paths without the leading slash (normalised so outputs don't nest), and stale files from earlier runs
(the suite now starts every task in a clean folder).

## Demo video

A walkthrough of the architecture, the tests, an impossible task, a live run and the saved outputs (under 5 minutes): https://www.youtube.com/watch?v=lzVS8zqMAAM

## Bodhan models used

Hosted API at `https://api.bodhan.ai`: `indic-transcribe` (`/v1/audio/transcriptions`, 16 kHz mono WAV windows),
`indic-translate` (chat-completions route with a top-level `target_language`; the documented `/translate` route 404s on the live gateway; truncation read from `finish_reason`), `indic-speak` (`/v1/audio/speech`,
24 kHz mono WAV, voices from the library), `indic-ocr` (`/v1/chat/completions`, `table_format=markdown`, `max_tokens`
2048 raised on demand). No other ASR, MT, TTS or OCR is used anywhere; ffmpeg/soundfile/numpy are used only for audio
decoding, windowing, resampling and encoding, and poppler only to rasterise PDF pages.

## Limitations

- Timestamps are per window (≤28 s), not per word. Subtitles are therefore coarse; there is no forced alignment (and I did
  not want to use a non-Bodhan model for it).
- `detect_language` can't tell Hindi from Marathi/Nepali (all Devanagari). It returns candidates and the agent records its
  assumption. Clip-level language detection costs a transcription of 15 s.
- The CER thresholds in `verify_speech` (pass ≤0.30, warn ≤0.50) are heuristics; they should be tuned on real output.
- The duration sanity band used for TTS chunks (~14 chars/s) is a rough conversational rate, only used to catch gross
  omissions or loops, not subtle ones.
- A tool call that exceeds `--call-timeout` is reported as `tool_timeout` and abandoned, but its worker thread runs until the
  HTTP client's own timeout (90 s).
- Video and non-WAV/MP3/FLAC/OGG audio need ffmpeg; without it the tools say so. Video dubbing and re-encoding are out of scope.
- OCR HTML tables (merged cells) are supported by the API but I default to Markdown tables; merged-cell tables lose their merges.
- The agent runs tool calls from one turn in parallel but doesn't resume a half-finished run; the cache makes a rerun cheap
  instead.

## What I'd do next

A resumable run state (not just a cache), word-level subtitle timing from window boundaries plus VAD, tuning verification
thresholds against a labelled set, and a live (not post-hoc) view of the trace.

## AI assistance

I used Claude Code, an AI coding assistant, while building this: it helped draft modules and tests from my design, and I
reviewed, ran and changed the code. The design decisions (tool granularity, where the LLM does and
doesn't decide, the verification gate, the sandbox) are written up in [docs/DESIGN.md](docs/DESIGN.md).
