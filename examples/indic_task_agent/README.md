# Indic task agent

> An MCP tool server for Bodhan's Indic speech, translation and OCR models, plus a task agent that plans, calls those tools, checks its own work and writes the results.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr (+ optional indic-transliterate)
**Languages shown:** Tamil, Hindi, Marathi, Telugu, Bengali (all 22 supported languages work without code changes)
**Format:** CLI app (`python -m bodhan_agent`) + MCP server (`python -m bodhan_mcp`)

## What it does

Give it a plain-language task and some files: a scanned PDF, a voice note, a photo of a worksheet, audio clips or a text file. For example: *"Make a two-minute Tamil audio summary of this chapter"*, or *"Transcribe this Hindi voice note and give me English subtitles"*. The agent plans with any OpenAI-compatible LLM and calls Bodhan's models through the MCP server. It then verifies the outputs (for example, it transcribes generated speech back to text and compares) and writes `result.md`, the output files, a step-by-step `trace.jsonl` and a `report.json`. Tasks it cannot do, such as unsupported languages, missing files or too small a budget, end with a clear explanation and no made-up output.

The MCP server also runs on its own, so any MCP client can use the tools without this agent.

## Setup

Needs Python 3.11+ and **ffmpeg** (with ffprobe) on your PATH: `winget install ffmpeg`, `brew install ffmpeg` or `apt install ffmpeg`.

```bash
cd examples/indic_task_agent
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt                      # pinned deps + this package (-e .)
cp .env.example .env                                 # then fill in the keys
```

`.env` needs one Bodhan key per model, from [console.bodhan.ai](https://console.bodhan.ai): `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY` and `BODHAN_OCR_KEY` (`BODHAN_TRANSLITERATE_KEY` is optional). It also needs an OpenAI-compatible LLM endpoint: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, plus the optional `LLM_EXTRA_BODY`. `.env.example` shows OpenAI (`gpt-6-luna`) and Anthropic (`claude-sonnet-5-5`) settings.

Or with Docker: `docker build -t indic-task-agent .` (see the comments at the top of the [Dockerfile](Dockerfile)).

## Run

The agent:

```bash
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files sample_data/chapter.pdf \
  --out ./out \
  --max-steps 30 --max-cost-inr 20
```

`--files` takes files and/or folders. Optional extra flags: `--call-timeout` (seconds per tool call, default 180), `--max-seconds` (wall clock, default 600) and `--verbose`. Each run writes:

```
out/
  result.md      what was done, the answer, outputs (Indian-language text embedded from the files), assumptions
  artifacts/     audio, subtitles, Markdown, text
  trace.jsonl    one line per step: plan, LLM call, tool call (arguments, result summary, latency, cost, rationale), verification
  report.json    status, steps, verification results, cost per tool and total, quality targets met/missed
  work/          intermediate files from the tools
```

Statuses are `completed`, `completed_with_warnings`, `partial`, `refused`, `budget_exhausted` and `failed`. Code computes them from what actually happened; the LLM's own claims never set the status.

The MCP server on its own (stdio):

```bash
python -m bodhan_mcp
```

Tested with **MCP Inspector** (`npx @modelcontextprotocol/inspector python -m bodhan_mcp`). For Claude Desktop or Claude Code, add it as a stdio server:

```json
{"mcpServers": {"bodhan": {"command": "python", "args": ["-m", "bodhan_mcp"],
  "env": {"BODHAN_TRANSCRIBE_KEY": "...", "BODHAN_TRANSLATE_KEY": "...", "BODHAN_SPEAK_KEY": "...", "BODHAN_OCR_KEY": "..."}}}}
```

Tests and self-evaluation:

```bash
pytest                          # 97 tests, no network: Bodhan and the LLM are faked
python evals/run_evals.py       # run every task in evals/tasks.json on your files in sample_data/ and report a pass rate
python probes/probe_api.py translate   # re-run a live API probe (see probes/FINDINGS.md)
```

## Tools

| Tool | Model | Handles inside the tool |
|---|---|---|
| `transcribe_audio` | indic-transcribe | Any length or format (video too): converts to 16 kHz mono, cuts at silences into windows of 25 s or less, writes the transcript and timed segments |
| `translate_text` | indic-translate | Windows of about 1,500 characters; re-splits a window the model truncates; `format=markdown` keeps headings and tables; `script` native/roman/codemix |
| `synthesize_speech` | indic-speak | Sentence groups of 200 characters or less; re-splits if the ~30 s cap is hit; checks the voice and language; mp3/wav tagged as AI-generated |
| `ocr_document` | indic-ocr | Images and multi-page PDFs (rendered page by page, pages in parallel); Markdown with tables, plus layout blocks |
| `make_subtitles` | indic-translate | SRT from timed segments, translated per segment so the timings are kept |
| `transliterate_text` | indic-transliterate | Same language, different script (optional key) |
| `inspect_media`, `detect_language`, `read_text`, `estimate_cost` | none (free) | File facts, script-based language guess, paged text reading, upper-bound cost |

Every tool takes file paths and returns `result_path`, a short `preview`, `meta`, `cost_inr`, `cached` and `warnings`. Errors come back as `{"error": {"code", "message", "hint"}}` and never crash the server. Resources: `bodhan://languages` and `bodhan://voices`.

## How it works

1. **Ingest** (code): lists the input files and describes each with `inspect_media`. Any Indian-language text inside the task itself is moved into a file.
2. **Plan** (LLM): one call turns the task into a structured plan: intent, languages, expected outputs, assumptions and steps.
3. **Preflight** (code): refuses before spending anything when a language is unsupported, files are missing or the minimum cost is over budget.
4. **Act → guard → execute** (LLM chooses, code enforces): the step, cost and time budgets are checked before every call. Indian-language text written by the LLM is rejected, identical calls are reused, and every tool result is fenced as untrusted data.
5. **Verify → repair** (code): generated speech is transcribed back and compared with its source text. Translations are checked for truncation, script and table structure, and subtitles for valid timings. A failed check is repaired once, with smaller chunks.
6. **Finalize** (code): computes the status and writes `result.md`, `report.json` and the trace.

Diagrams: [ARCHITECTURE.md](ARCHITECTURE.md). Design rationale (tool granularity, verification, budgets, recovery, where the LLM is used): [DESIGN_NOTES.md](DESIGN_NOTES.md). Measured API behaviour that shaped the tools: [probes/FINDINGS.md](probes/FINDINGS.md).

## Limitations

- **The LLM never reads or writes Indian-language text.** It summarises the English translation of a document, and Bodhan translates its English back. Quality depends on that round trip.
- **Speech verification uses Bodhan's own ASR**, so the similarity score includes recognition noise (the threshold is 60%). Duration is checked against about 12 characters per second.
- **Language detection is by script only**, as the brief allows. Hindi, Marathi and Nepali (all Devanagari) can't be told apart from text alone; the agent records an assumption.
- **No timestamps come back from indic-transcribe.** Subtitle timings come from our own silence-based cut points and are shared across sentences in proportion to their length.
- **`--max-cost-inr` covers Bodhan spend only.** LLM tokens are reported separately in `report.json`.
- **Speed:** speech synthesis and transcription run at roughly one second per second of audio, so long audio tasks approach the 5-minute target.
- Generated audio is AI-generated speech from Bodhan's fixed voice library, labelled as such.
