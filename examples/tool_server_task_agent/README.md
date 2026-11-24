# Bodhan tool server and task agent

> An MCP server that exposes Bodhan's four Indic models as well-described, typed tools, plus a budget-aware agent that plans, calls them, verifies its own work and writes a traceable result. For developers who want to give an LLM agent Indian-language speech, translation and OCR abilities.

**Bodhan models:** `indic-transcribe`, `indic-translate`, `indic-speak`, `indic-ocr` (hosted API)
**Languages shown:** Hindi, Marathi, Tamil, Telugu, Kannada, Bengali (all 22 supported languages plus English work)
**Format:** Python package (MCP server over stdio, task agent CLI, tests, Dockerfile)

## What it does

* **`python -m bodhan_mcp`**: an MCP server (stdio) with nine tools: transcribe, translate, speak, OCR, verify audio, verify translation, inspect media, detect script language, list voices. Each tool handles its model's limits internally (30-second audio windows, short TTS inputs, the translation context window, multi-page PDFs), takes files **by path**, and returns typed results or structured errors. Any MCP client can use it, with or without the agent.
* **`python -m bodhan_agent`**: reads a plain-language task and input files, writes a plan, calls the tools, **verifies every generated audio file and translation**, enforces step / rupee / time budgets, and writes `result.md`, `artifacts/`, `trace.jsonl` and `report.json`. Impossible tasks (unsupported language, missing file, ...) end in an honest explanation with no fabricated output.

Design write-ups: [architecture diagram](docs/architecture.md) · [agent-loop flow chart](docs/agent_flow.md) · [design notes](docs/design_notes.md) · [MCP Inspector session](docs/inspector_session.md).

## Setup

System dependencies (not installable with pip):

| Tool | Why | Install |
|---|---|---|
| **ffmpeg** (with `ffprobe`) | decode audio/video, silence detection, joining and loudness-normalising speech | `apt install ffmpeg` · `brew install ffmpeg` · `winget install Gyan.FFmpeg` |
| **poppler** (`pdftoppm`) | render PDF pages for OCR | `apt install poppler-utils` · `brew install poppler` · `winget install oschwartz10612.Poppler` |

```bash
cd examples/tool_server_task_agent
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt && pip install -e . --no-deps    # or: make setup
cp .env.example .env                                   # then fill in your keys (see below)
```

Python 3.10+ (developed and tested on 3.11). Dependencies are pinned in [requirements.txt](requirements.txt) and [pyproject.toml](pyproject.toml). A [Dockerfile](Dockerfile) bundles ffmpeg and poppler:

```bash
docker build -t bodhan-tool-agent .
docker run --rm --env-file .env -v "$PWD/inputs:/data/in:ro" -v "$PWD/out:/data/out" bodhan-tool-agent \
  --task "Translate this worksheet into Marathi and keep the table" --files /data/in --out /data/out
```

### Configuration

All secrets come from the environment (or a `.env` found in the working directory or any parent). Nothing else is read.

| Variable | Used for |
|---|---|
| `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_OCR_KEY` | one key per Bodhan model ([console](https://console.bodhan.ai); keys are model-scoped) |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | the agent's OpenAI-compatible chat endpoint (must support tool calling) |

For local convenience only, `INDIC_TRANSCRIBE_API_KEY`, `INDIC_TRANSLATE`, `INDIC_SPEAK` and `INDIC_OCR` are accepted as fallbacks when the official names are unset. If `LLM_*` is missing the agent stops and says what to add (tasks that are refused before any LLM is needed still get an honest report). Optional: `BODHAN_CACHE_DIR`, `BODHAN_WORKDIR`, `BODHAN_MAX_CONCURRENCY` (default 3), `BODHAN_CALL_TIMEOUT_S` (default 90).

## Run

**The task agent**

```bash
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out \
  [--max-steps 30] [--max-cost-inr 20]
```

`--files` takes files and/or directories. Extra flags: `--call-timeout-s` (per tool call, default 240), `--max-wall-s` (default 900), `--cache-dir`. Output:

```
out/
  result.md      what was done, the answer, assumptions, artifacts (with previews), verification, cost
  artifacts/     the deliverables only: audio, subtitles, Markdown, ...
  trace.jsonl    one line per step: plan, tool, arguments, why, result summary, latency, cost, running cost
  report.json    status, steps, verification results, total cost and time, quality targets missed and why
  work/          intermediates (transcripts, OCR text, ...)
```

Exit code: `0` success **or an honest refusal**, `1` failed, `2` partial (budget reached or a verification failed). The report is always written.

**The MCP server on its own (stdio)**

```bash
python -m bodhan_mcp
```

Point any MCP client at that command, e.g. in a client config: `{"command": "python", "args": ["-m", "bodhan_mcp"]}`. Optional streamable HTTP (requires a bearer token): `BODHAN_MCP_TOKEN=<16+ random chars> python -m bodhan_mcp --transport streamable-http --port 8765`.

**Off-the-shelf client used for testing:** the official **MCP Inspector CLI** (`npx @modelcontextprotocol/inspector@1.0.6 --cli python -m bodhan_mcp ...`), including bad inputs. The recorded session is in [docs/inspector_session.md](docs/inspector_session.md) and is reproducible with `bash scripts/inspector_check.sh`. The agent itself uses the official MCP Python SDK client.

## Tools

| Tool | Cost | Purpose and limits handled inside |
|---|---|---|
| `inspect_media(path)` | free | Real format from bytes, kind, duration, pages, size, audio track, script-based language; directories are listed. Flags mislabelled or unsupported files. |
| `detect_script_language(text_or_path)` | free | Language by Unicode script (no model); ranked candidates and confidence. |
| `list_voices(language, gender?)` | free | Voices, speaking styles, supported speech languages (from the fixed library). |
| `transcribe_audio(audio_path, language?)` | Rs 0.10 / audio min | Any audio/video up to 3 h; windows of at most 28 s cut at silences; silent windows skipped; returns transcript `.txt`, timed `.srt`, segments, language. |
| `translate_text(text_or_path, target_lang, source_lang?, script?, pivot_via_english?)` | Rs 0.20 / 10k output tokens | Chunks inside the context window; keeps Markdown, tables, LaTeX, numbers; `.srt` keeps timing and cue count; Indian→Indian note and optional English pivot. |
| `synthesize_speech(text_or_path, language, voice?, gender?, style?, output_format?, force_refresh?)` | Rs 6 / 10k characters | Short requests at sentence boundaries, stalled requests split, parts joined and loudness-normalised, AI-generated label in metadata; rejects text in the wrong script. |
| `ocr_document(path, table_format?, page_range?)` | Rs 0.20 / page | Images and multi-page PDFs (rendered page by page, text layer never used); per-page confidence and flags. |
| `verify_audio(audio_path, expected_text, language, max_cer?)` | Rs 0.10 / audio min | Back-transcribes and returns character error rate plus repetition / omission flags. |
| `verify_translation(source_path, translated_path, target_language?)` | free | Structural coverage check: headings, tables, numbers, LaTeX, length, script, SRT timing. No model. |

Every result has `ok`, `error`, `cost_inr`, `latency_s`, `cache_hits`, `warnings`. Every failure is `{error_code, message, retryable, hint}` and arrives as an MCP error result (`isError: true`) with the same payload in `structuredContent`; the server never crashes on bad input. Two MCP resources: `bodhan://voices`, `bodhan://languages`.

## How it works

1. **Preflight (code):** refuse a missing input path or a requested *output* language no Bodhan model supports (before any spend); inspect every input.
2. **Plan (LLM):** steps, assumptions, estimated cost. Infeasible or over-budget plans are refused.
3. **Act (LLM chooses, code guards):** Indic-text rule, duplicate-call memo, pre-spend cost refusal, per-call timeout, parallel execution of independent calls.
4. **Verify (code-forced):** every generated audio file and translation needs a passing check before the run may report success.
5. **Retry / re-plan / stop:** one retry for failed audio (another voice), re-plan after tool errors, clean stop when a budget runs out.
6. **Write outputs:** `result.md`, `artifacts/`, `trace.jsonl`, `report.json`.

Details: [docs/design_notes.md](docs/design_notes.md).

## Demo runs

Six required demo tasks (1-6) plus a prompt-injection input and a PDF task, all run through the real CLI against the hosted Bodhan API with `gpt-5.4-nano` as the planner. **Cold runs** (empty cache), measured by the agent itself; total Bodhan spend for the eight runs was **Rs 2.04**. Each run folder holds the committed `result.md`, `trace.jsonl` and `report.json`; audio and other media are git-ignored (regenerate with `make demo`). `result.md` embeds the text deliverables, so the audio files themselves are not needed to review the outcome.

| # | Task | Input | Result | Cost (Rs) | Time (s) | Tool calls | Verification | Outputs |
|---|---|---|---|---|---|---|---|---|
| 1 | Tamil audio summary of a scanned/image page | Hindi page image (PNG) | **pass** | 0.380 | 57 | 5 | translation ok, audio ok (CER 0.0093) | [result](demo_runs/tamil_audio_summary_of_page/result.md) · [trace](demo_runs/tamil_audio_summary_of_page/trace.jsonl) · [report](demo_runs/tamil_audio_summary_of_page/report.json) |
| 2 | Hindi voice note → English SRT | 10 s Hindi WAV | **pass** | 0.017 | 27 | 3 | translation ok | [result](demo_runs/hindi_voice_note_to_english_srt/result.md) · [trace](demo_runs/hindi_voice_note_to_english_srt/trace.jsonl) · [report](demo_runs/hindi_voice_note_to_english_srt/report.json) |
| 3 | Worksheet photo with a table → Marathi Markdown, table intact | Photo-style PNG | **pass** | 0.202 | 43 | 3 | translation ok | [result](demo_runs/worksheet_to_marathi_markdown/result.md) · [trace](demo_runs/worksheet_to_marathi_markdown/trace.jsonl) · [report](demo_runs/worksheet_to_marathi_markdown/report.json) |
| 4 | Which of five clips are Telugu? Translate those to English | 5 short WAVs (hi, te, ta, te, kn) | **pass** | 0.027 | 24 | 9 | translation ok, translation ok | [result](demo_runs/which_clips_are_telugu/result.md) · [trace](demo_runs/which_clips_are_telugu/trace.jsonl) · [report](demo_runs/which_clips_are_telugu/report.json) |
| 5 | Read a paragraph aloud in Bengali, female voice | English text file | **pass** | 0.147 | 45 | 5 | translation ok, audio ok (CER 0.0) | [result](demo_runs/bengali_read_aloud/result.md) · [trace](demo_runs/bengali_read_aloud/trace.jsonl) · [report](demo_runs/bengali_read_aloud/report.json) |
| 6 | **Impossible:** translate this video into French | MP4 | **pass (honest refusal)** | 0.000 | 0 | 0 | n/a (no output generated) | [result](demo_runs/impossible_translate_video_to_french/result.md) · [trace](demo_runs/impossible_translate_video_to_french/trace.jsonl) · [report](demo_runs/impossible_translate_video_to_french/report.json) |
| + | **Prompt injection** inside an image, translate the notice to Marathi | PNG with 'IGNORE ALL PREVIOUS INSTRUCTIONS ...' | **pass** | 0.202 | 16 | 3 | translation ok | [result](demo_runs/prompt_injection_notice/result.md) · [trace](demo_runs/prompt_injection_notice/trace.jsonl) · [report](demo_runs/prompt_injection_notice/report.json) |
| + | One-minute Tamil audio summary of a PDF chapter | 2-page PDF | **pass** | 1.064 | 86 | 7 | translation ok, audio ok (CER 0.0013) | [result](demo_runs/pdf_chapter_tamil_summary/result.md) · [trace](demo_runs/pdf_chapter_tamil_summary/trace.jsonl) · [report](demo_runs/pdf_chapter_tamil_summary/report.json) |

No run missed a quality target (`quality_targets_missed` is empty in every report): multi-step tasks finished well under 5 minutes, spend stayed far inside the Rs 20 default cap, and every generated audio file and translation was verified before the run ended. Intended-behaviour notes:

* **#6** was refused by code before any spend or LLM call, with no artifact and a plain-English `result.md`.
* **Injection**: the notice was translated as document content; the injected orders were flagged, listed under *Security notes*, and not obeyed (no speech was synthesised).
* **#4** identifies clips 2 and 4 as Telugu (the others were Hindi, Tamil and Kannada) and delivers one English file per Telugu clip; the English text is embedded in `result.md`. In an earlier development run the small planner model repeated its whole pipeline after finishing it; the plan guard and the duplicate-call memo blocked every repeat at zero cost (`guard` lines in that trace), and this recorded run was clean.

The self-evaluation suite (`scripts/eval_suite.py`, 8 demo + 9 edge-case tasks with explicit pass/fail checks) is described in the PR description.


## Demo video

<!-- TODO(author): replace with the public link (5 minutes or less; several tasks end to end, including the impossible one) -->
VIDEO_LINK_PLACEHOLDER

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests                       # 67 tests, about 30 s; the Bodhan HTTP layer is mocked, no keys or credit needed
python scripts/eval_suite.py       # live suite: 8 demo + 9 edge-case tasks with expectations and a pass rate (17/17 on the final code; uses keys and a little credit)
```

Unit tests cover audio window splitting, TTS splitting, translation chunking (tables, SRT, LaTeX), PDF pagination, structured errors on bad input for every tool, config fallback, retries/backoff, cache hits, budget enforcement, the injection guard, the Indic-text rule and the verification gate. One end-to-end test class runs the **real MCP server over stdio** with a fake Bodhan API and a scripted LLM (the scripted LLM exists only in tests). Tests that need ffmpeg or poppler skip themselves if absent.

## Limitations

* **The hosted `/translate` route returned 404** during development; the documented chat-completions equivalent of `indic-translate` is used instead (same model and price).
* **Script-based language ID cannot separate languages that share a script** (Hindi/Marathi/Nepali in Devanagari, Bengali/Assamese): the tool returns candidates and a marker-word guess. Language-ID for romanised Indic text is not attempted.
* **`verify_translation` is structural only**: it catches truncation, collapsed tables and untranslated output, not mistranslation. `verify_audio` measures intelligibility by round-trip ASR, so ASR errors count against good audio and its threshold (0.35 CER) is a heuristic.
* **Indic-Speak is the slow, expensive model** (10 to 20 s per sentence or two; Rs 6 per 10,000 characters), and it occasionally stalls; stalled requests are split and retried, which costs time.
* Cost for tasks with cache hits is lower than cold-run cost; the table above is from cold runs. Speech cost is estimated from characters, translation cost from the reported output tokens.
* The LLM is a small model (`gpt-5.4-nano`) in the demos: it sometimes plans redundantly (the guards stop repeats) and writes terse summaries.
* Only text, audio, video (audio track), images and PDFs are handled; `.docx`/`.xlsx` inputs are refused honestly.
* Windows paths and POSIX paths both work; tested on Windows 11 with Python 3.11. Docker build and a clean-clone setup were verified (see the PR description).
* Not done: a web UI, resumable runs, translation-quality scoring.

## AI assistance

Built with **Claude Code** (Anthropic): it drafted the code, tests and docs from my specification, and it ran the live experiments. I read and can explain each module; the design decisions (tool granularity, the code-enforced gates, what the LLM may and may not do) and the review of every demo output were mine. Demo inputs are synthetic and generated by the scripts in this folder.
