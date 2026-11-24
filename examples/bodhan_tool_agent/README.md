# Bodhan tool server and task agent

> An MCP server that exposes Bodhan's four Indic models as ten well-described tools, and a command-line agent that plans, calls them, verifies its outputs and reports honestly.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr (hosted API)
**Languages:** Support varies by model. The `bodhan://languages` resource lists the accepted codes for each capability.
**Format:** Python CLI + MCP server (stdio; optional Streamable HTTP with bearer token)

## What it does

`python -m bodhan_agent --task "..." --files inputs/ --out out/` takes a plain-language task and input
files (audio, video, scans, photos, PDFs, text), writes a plan, calls the Bodhan tools, checks every
generated audio file and translation, and writes `result.md`, `artifacts/`, `trace.jsonl` and
`report.json`. Unsupported languages, missing files and too-small budgets end in a clear explanation
and no output.

`python -m bodhan_mcp` runs the same tools as a standalone MCP server for any MCP client.

Design documents:
* [Architecture diagram](docs/architecture.md): server, tools, agent and how they connect
* [Agent flow chart](docs/agent_flow.md): plan, act, verify, retry and stop
* [Design notes](docs/design_notes.md): tool list and granularity, verification, budgets, recovery, where the LLM is used

## Setup

Needs Python 3.11+ and **ffmpeg** on `PATH` (audio/video decoding and MP3 encoding; `apt install ffmpeg`,
`brew install ffmpeg` or `winget install ffmpeg`). Without ffmpeg, WAV input and WAV output still work.

```bash
cd examples/bodhan_tool_agent
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

On PowerShell, run the setup from the recipe folder with the Windows interpreter path.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

In PowerShell, use `.\.venv\Scripts\python.exe` in place of `python` when running the recipe. Fill in
`.env` (or the environment) with `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`,
`BODHAN_OCR_KEY` (or a single `BODHAN_API_KEY`), and `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` for any
OpenAI-compatible endpoint with tool calling. No other secrets.

For Docker on a POSIX shell, put a worksheet image you have rights to use at
`sample_data/worksheet.jpg`. The container uses your host UID to write to the mounted output directory.

```bash
docker build -t bodhan-tool-agent .
mkdir -p out
docker run --rm --env-file .env --user "$(id -u):$(id -g)" -e BODHAN_CACHE_DIR=/tmp/bodhan_cache \
  -v "$PWD/sample_data:/data/in:ro" -v "$PWD/out:/data/out" bodhan-tool-agent \
  python -m bodhan_agent --task "Translate this worksheet into Marathi and keep the table" \
    --files /data/in/worksheet.jpg --out /data/out/run1
```

## Run

```bash
# The task agent
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out --max-steps 30 --max-cost-inr 20

# The MCP server on its own (stdio)
python -m bodhan_mcp

# Optional: Streamable HTTP at http://127.0.0.1:8765/mcp, requires "Authorization: Bearer $MCP_HTTP_TOKEN"
MCP_HTTP_TOKEN=... python -m bodhan_mcp --transport http --port 8765
```

Run `python -m bodhan_agent --help` for `--max-runtime-s`, `--call-timeout-s`, and `--mcp`.

Output of one run:

```
out/
  result.md        what was done, the answer, assumptions, links to artifacts, verification
  artifacts/       deliverables (audio, subtitles, Markdown, ...); intermediate/ holds working files
  trace.jsonl      one line per event: start, inventory, plan, llm turn, tool_call (tool, arguments,
                   reason, result summary, latency, cost), verification, final
  report.json      status, steps, verification results, cost per model, time, quality targets missed
```

The exit code is 0 whenever the run ends cleanly, including honest failures; `report.json` `status`
is one of `completed`, `partial`, `impossible`, `failed`, `budget_exhausted`.

### Using the server from an MCP client

Tested with **MCP Inspector** (`@modelcontextprotocol/inspector`) over stdio, and with the official MCP
Python SDK client (the agent itself and `tests/test_server.py`). The Inspector listed all ten tools and returned `isError: true` with `unsupported_language` for French on `translate_text`.

```bash
npx @modelcontextprotocol/inspector --cli "$(which python)" -m bodhan_mcp --method tools/list
npx @modelcontextprotocol/inspector --cli "$(which python)" -m bodhan_mcp --method tools/call \
  --tool-name translate_text --tool-arg target_language=fr --tool-arg text=hello   # -> unsupported_language error
npx @modelcontextprotocol/inspector "$(which python)" -m bodhan_mcp                 # interactive UI
```

Claude Desktop / any stdio client config:

```json
{"mcpServers": {"bodhan": {"command": "/abs/path/.venv/bin/python", "args": ["-m", "bodhan_mcp"],
  "env": {"BODHAN_API_KEY": "<key>", "BODHAN_MCP_OUTPUT_DIR": "/abs/path/bodhan_outputs"}}}}
```

## How it works

1. **Inventory (code):** `inspect_files` reports kind, duration, pages, script-based language and cost estimates for every input; missing paths are listed.
2. **Plan (LLM):** a JSON plan: goal, feasibility against the supported-language lists, deliverables, assumptions, steps, verification. It is logged to the trace.
3. **Act (LLM + code guards):** the LLM calls tools; code resolves paths, caps each paid call at the remaining budget, forces speech verification, rejects Indian-language text the LLM did not copy from a tool, suppresses duplicate calls, wraps transcript/OCR/file text as untrusted data, and runs independent calls in parallel.
4. **Verify (tools + code gate):** speech is back-transcribed chunk by chunk; translations report coverage, truncation and target-script share; at `finish` the gate checks every deliverable before publishing it.
5. **Report:** deliverables are copied to `artifacts/`; `result.md` and `report.json` are written from recorded state, not from the LLM's claims.

### Tools

| Tool | Model | What it handles for the caller |
|---|---|---|
| `inspect_files` | - | file kind, duration, pages, language guess, cost estimate; folders; missing files |
| `read_text` | - | paged reading of transcripts, translations, OCR Markdown |
| `list_voices` | - | voice library, defaults per language and gender, styles (also resource `bodhan://voices`) |
| `detect_language` | Transcribe (audio only) | script, letters and function words for text. Audio detection uses a probe up to 8 seconds and checks its cost cap before calling ASR |
| `transcribe_audio` | Indic-Transcribe | any length, silence-aware windows <= 28 s, auto language, video audio, timed segments |
| `translate_text` | Indic-Translate | tables/lists/segments/subtitles preserved, context-window chunking, truncation re-split, coverage check |
| `synthesize_speech` | Indic-Speak | sentence chunks <= 220 chars, voice by gender, back-transcription check and regeneration, MP3 tagged AI-generated |
| `verify_speech` | Indic-Transcribe | audio vs text similarity, repetition/omission flags |
| `ocr_document` | Indic-OCR | multi-page PDFs, photos (EXIF), blank-page skip, 502 retry, per-page language, Markdown or HTML tables |
| `make_subtitles` | - | SRT/VTT from timed segments with proportional cue timing |

Resources: `bodhan://voices`, `bodhan://languages`, `bodhan://pricing`, `bodhan://usage`. Prompt: `indic_task`.

## Results

The [evaluation suite](evals/demo_tasks.json) ran the CLI over MCP stdio against hosted Bodhan APIs, with Gemini 3.1 Flash-Lite on Vertex AI as the planner. The [fixture generator](scripts/make_demo_inputs.py) creates synthetic inputs, including a 154-second Hindi recording and an eight-page scanned chapter. The published chapter uses the generator's English fallback for hosts that cannot print the Indic fixture to PDF.

All ten recorded live cases met the published evaluation criteria. Seven produced the requested outputs and three handled an unsupported language, a missing file, or an insufficient budget as expected. Each bundle contains the report, trace, final answer, and any deliverables. [Published evaluation](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/evaluation.json) · [Run manifest](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/demo-manifest.json)

The chapter walkthrough reused a [draft generated during the first live run](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/chapter-summary-draft.md) after checking it against the scan, and published the verified Tamil text and 118-second MP3. The first live run measured ₹2.6944 and 166.83 seconds and published the MP3. A fresh-script retry hit the speech API timeout.

| Live task | Checked result | Wall time | Bodhan cost | Evidence |
| --- | --- | ---: | ---: | --- |
| Tamil audio summary of an eight-page scan | Pass · Verified Tamil script and narration | 27.17 s | ₹0.0000 | [Output and trace](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/01_tamil_audio_summary.zip) |
| Hindi voice note to English subtitles | Pass · Hindi ASR and verified English SRT | 322.41 s | ₹0.2853 | [Output and trace](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/02_hindi_note_english_subtitles.zip) |
| Marathi worksheet translation | Pass · Table structure preserved | 12.58 s | ₹0.0000 | [Output and trace](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/03_worksheet_marathi_table.zip) |
| Telugu clips in a mixed folder | Pass · Clips 1 and 3 identified and translated into English | 76.44 s | ₹0.0556 | [Output and trace](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/04_which_clips_telugu.zip) |
| Bengali read-aloud with a female voice | Pass · Ishita narration verified by back-transcription | 50.94 s | ₹0.2584 | [Output and trace](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/05_bengali_read_aloud_female.zip) |
| Video translation into French | Pass · Expected refusal for unsupported French | 8.28 s | ₹0.0000 | [Output and trace](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/06_video_to_french_impossible.zip) |
| Kannada notice containing embedded instructions | Pass · Kannada translation and recorded injection flag | 12.11 s | ₹0.0038 | [Output and trace](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/07_notice_with_prompt_injection.zip) |
| Missing PDF | Pass · Expected refusal for missing input | 8.59 s | ₹0.0000 | [Output and trace](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/08_missing_file.zip) |
| Tamil read-aloud with a ₹0.02 cap | Pass · Expected budget failure, with an empty deliverable list | 12.06 s | ₹0.0000 | [Output and trace](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/09_budget_too_small.zip) |
| Unknown-language voice message | Pass · Marathi identified and translated into English | 61.56 s | ₹0.0291 | [Output and trace](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/10_unknown_language_voice_message.zip) |

Wall time includes planner turns and MCP calls. Prices are the recipe's per-run Bodhan usage estimates. The runs used a shared cache, so cache hits add zero incremental Bodhan cost. Gemini usage is logged in tokens and is outside the INR figures above.

The subtitle run exceeded the five-minute multi-step target by 22.41 seconds. Its report records the latency miss.

## Demo video

[Download the 2:59 demo walkthrough](https://github.com/Abhyuday-06/Bodhan-AI-Cookbook/releases/download/tool-agent-takehome-demo/tool-agent-demo.mp4). It replays recorded MCP stdio runs with pauses shortened and includes excerpts of the actual generated audio.

## Tests

```bash
pip install -r requirements.txt
python -m pytest -q
python -m ruff check src tests evals scripts
```

* Unit tests for each tool's chunking and limits: 30-second ASR windows, TTS input size, translation payload size and truncation, multi-page OCR, blank pages, 502 retries, budget refusal, structured errors.
* MCP tests: schemas and descriptions, bad inputs over the protocol, a standalone stdio server.
* End-to-end agent tests with a scripted planner: a multi-step translate-then-speak task, an impossible language, the provenance guard, a too-small budget, a step limit, prompt injection, the finish gate.
To rerun the hosted evaluation, first generate the synthetic fixtures, then run the CLI evaluation. Both commands use your configured keys and can incur API charges. The resulting `runs/eval/summary.md` reports pass rates and links to each run. [Task suite](evals/demo_tasks.json)

```bash
python scripts/make_demo_inputs.py --out sample_data
python evals/run_evals.py --suite evals/demo_tasks.json --inputs sample_data --out runs/eval
```

## Limitations

* Language detection for Devanagari-script text and speech (Hindi, Marathi, Nepali, Maithili, Konkani, ...) uses function words and can be wrong on very short inputs; the result carries a confidence and candidates, and the agent records the assumption.
* Transcription has no word timestamps from the model; subtitle timing comes from the VAD windows (8 s for subtitles) with time shared by text length inside a window.
* Speech verification compares text with a transcription of the audio, so ASR errors can mask or create mismatches; thresholds are lenient (similarity >= 0.6, length ratio 0.6-1.45).
* PDFs are always OCR'd page by page (Rs 0.20/page) rather than reading an embedded text layer, because Indic text layers are often broken; up to 100 pages per call.
* No video output (dubbing back into a video), no voice cloning, no languages outside Bodhan's list.
* LLM spend is reported in tokens; it counts toward `--max-cost-inr` only if `LLM_PRICE_INR_PER_1M_INPUT/OUTPUT` are set.
* Paid tools check their allocated share before requesting data, including the ASR probe used for language detection. Reported cost uses metered units and the published price schedule. Translation token totals can be estimated when the API omits usage.

## AI assistance

Kiro assisted with initial scaffolding, implementation, tests and documentation. Codex assisted with provider compatibility, live evaluation, debugging, regression checks and the submission materials. The design decisions are described in
[docs/design_notes.md](docs/design_notes.md).
