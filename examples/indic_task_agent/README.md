# Indic task agent

> An MCP server that exposes Bodhan's four Indic models as eight well-described tools, and a command-line agent that plans, calls them, verifies its own outputs and reports honestly, on tasks it has never seen.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr (hosted API)
**Languages shown:** Hindi, Tamil, Telugu, Bengali, Marathi, Gujarati, English (anything in `list_capabilities` works)
**Format:** CLI agent + MCP server (stdio, optional streamable HTTP with token auth)

## What it does

```bash
python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" --files inputs/ --out ./out
```

The agent inspects the inputs and plans a chain of Bodhan tools: OCR → translate to English → the LLM writes an English summary → translate to Tamil → speak. It runs the chain under budgets, back-transcribes the audio to check it, and writes:

```
out/
  result.md        # what was done, the answer, assumptions, verification, links to artifacts
  artifacts/       # delivered files only (verified), e.g. summary.ta.mp3, summary.ta.md
  trace.jsonl      # one line per step: plan, tool, why, arguments, result summary, latency, cost
  report.json      # status, steps, verification, cost by model, time, quality targets
  work/            # every intermediate file, for inspection
```

The same tools work without the agent from any MCP client (`python -m bodhan_mcp`).

## Setup

Needs Python 3.12+ and ffmpeg (`apt install ffmpeg` / `brew install ffmpeg` / `winget install ffmpeg`).

```bash
cd examples/indic_task_agent
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt                         # pinned; also installs the two packages
cp .env.example .env                                    # add Bodhan key(s) and LLM settings
```

`.env` needs `BODHAN_API_KEY`, or per-model `BODHAN_TRANSCRIBE_KEY` / `BODHAN_TRANSLATE_KEY` / `BODHAN_SPEAK_KEY` / `BODHAN_OCR_KEY`, plus `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` for any OpenAI-compatible endpoint with tool calling. Nothing else is secret.

Docker (ffmpeg and fonts included):

```bash
docker build -t indic-task-agent .
docker run --rm --env-file .env -v "$PWD/inputs:/inputs" -v "$PWD/out:/out" indic-task-agent \
  python -m bodhan_agent --task "Translate this worksheet into Marathi and keep the table" --files /inputs --out /out
```

## Run

**The agent**

```bash
python -m bodhan_agent \
  --task "Transcribe this Hindi voice note and give me English subtitles" \
  --files path/to/inputs/ \
  --out ./out \
  [--max-steps 30] [--max-cost-inr 20] [--tool-timeout 300] [--max-wall-seconds 900]
```

`--files` takes files and/or directories. The exit code is 0 for `success`, `partial` and `impossible` (an honest answer), and 1 for `failed`.

**The MCP server on its own (stdio)**

```bash
python -m bodhan_mcp
```

Tested with the official MCP Python SDK client (see `tests/test_server_stdio.py`). To try it in [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector .venv/bin/python -m bodhan_mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{"mcpServers": {"bodhan": {"command": "/abs/path/examples/indic_task_agent/.venv/bin/python",
  "args": ["-m", "bodhan_mcp"], "env": {"BODHAN_API_KEY": "<key>"}}}}
```

Streamable HTTP with a bearer token: `BODHAN_MCP_TOKEN=<16+ chars> python -m bodhan_mcp --http --port 8765`. Clients then call `http://127.0.0.1:8765/mcp` with `Authorization: Bearer <token>`.

**Demo inputs and self-evaluation**

```bash
python scripts/make_demo_inputs.py   # builds sample_data/demo/ with Bodhan (~Rs 2 once, cached after)
python scripts/run_eval.py           # runs tasks/demo_tasks.json, writes outputs/eval/summary.md
python -m pytest -q                  # 72 offline tests: fake Bodhan API + scripted LLM, no keys needed
```

## How it works

Read these three first:

- [Architecture diagram](docs/architecture.md): the server, its tools, the agent, and how they connect.
- [Agent flow chart](docs/agent_flow.md): plan, act, verify, retry and stop.
- [Design notes](docs/design_notes.md): the tool list and why this granularity, how each tool handles limits, verification, budgets, error recovery, the LLM decision points and their fallbacks, and trade-offs.

**Tools**

| Tool | Does | Limits handled inside |
|---|---|---|
| `inspect_files` (free) | Types, durations, pages, scripts, estimated cost | Directories expanded; missing paths listed, not raised |
| `list_capabilities` (free) | Languages per model, voices, styles, prices | Also exposed as `bodhan://catalog` and `bodhan://voices` resources |
| `transcribe_audio` | Audio or video of any length → transcript + timed segments | VAD chunks < 30 s, silent chunks skipped, `language=auto` → `detected_script` / `likely_languages` |
| `translate_text` | Text, Markdown, .docx, segments JSON or SRT → any supported language | Tables/headings/lists kept by construction, sentence chunks ≤ 1,200 chars, truncation re-split |
| `synthesize_speech` | Text of any length → one MP3/WAV/OGG, labelled AI-generated | 1–2 sentence chunks, joined; back-transcription check per chunk; failing chunks repaired |
| `ocr_document` | Images and PDFs of any length → Markdown with tables | Per-page rendering, blank-page skip, 502 → larger `max_tokens`, partial results kept |
| `make_subtitles` (free) | Segments → SRT/VTT | Long segments split into two-line cues |
| `read_text` (free) | Paged reading of text outputs | Content marked untrusted |

Every paid tool takes `max_cost_inr` and refuses before spending if the work would exceed it. Every result reports `usage.cost_inr`. Errors are JSON with a stable `code`, a `message` and a `hint` (see the `bodhan://errors` resource).

**Rules enforced in code, not just in the prompt**

- The LLM never writes Indian-language text: inline `text` arguments and the final answer are rejected if they contain Indic script the user did not type.
- File content reaches the LLM wrapped as untrusted data, and instruction-like text is flagged.
- Artifacts must be files produced by a tool in this run. An artifact counts as verified only if its tool's checks passed, and `impossible`/`failed` runs deliver nothing.
- Budgets (steps, ₹, time, per-call timeout) are enforced by the agent and the server, and the run stops cleanly with a report.
- The same input is never paid for twice: a content-hash cache, in-flight de-duplication, and duplicate-call short-circuiting.

## Demo results

<!-- Fill from outputs/eval/summary.md after running scripts/run_eval.py with real keys. -->

| Task | Input | Status | Cost (₹) | Time | Output |
|---|---|---|---|---|---|
| Two-minute Tamil audio summary of a chapter | 8-page scanned PDF | | | | [result](outputs/demo/01_tamil_audio_summary/result.md) |
| Hindi voice note → English subtitles | ~2.5-min WAV | | | | [result](outputs/demo/02_hindi_subtitles/result.md) |
| Worksheet → Marathi, keep the table | Photo | | | | [result](outputs/demo/03_marathi_worksheet/result.md) |
| Which clips are Telugu? Translate those | 5 clips | | | | [result](outputs/demo/04_telugu_clips/result.md) |
| Read aloud in Bengali, female voice | Text file | | | | [result](outputs/demo/05_bengali_tts/result.md) |
| Translate this video into French | MP4 | impossible (expected) | | | [result](outputs/demo/06_french_video/result.md) |
| Notice with an injected instruction → Hindi | Image | | | | [result](outputs/demo/07_prompt_injection/result.md) |
| Budget too small (₹0.05) | Text file | | | | [result](outputs/demo/08_budget_too_small/result.md) |
| Missing input file | — | | | | [result](outputs/demo/09_missing_file/result.md) |
| Unstated input language → English | Text file | | | | [result](outputs/demo/10_unstated_language/result.md) |

Audio artifacts are not committed (media stays out of git); listen to them in the demo video.

## Limitations

- Voice-activity detection is energy-based: noisy recordings may be cut at the quietest point instead of a natural pause (always under 30 s).
- Subtitle timing inside a segment is proportional to text length, because Indic-Transcribe returns no word timestamps. Segments are kept short (2–12 s).
- Script-based language ID cannot tell apart languages that share a script (Hindi/Marathi/Nepali, Bengali/Assamese). The result reports the candidates.
- TTS verification thresholds are conservative and were not calibrated on a large set; per-chunk scores are in the report.
- Digital PDFs are also read with Indic-OCR (₹0.20/page) to keep all text extraction on Bodhan.
- LLM spend is reported in tokens, not rupees.
- All generated speech uses Bodhan's fixed voice library (no cloning) and is labelled AI-generated in file metadata and in `result.md`.
