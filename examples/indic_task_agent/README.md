# Indic task agent

> An MCP server that exposes Bodhan's four Indic models as tools, plus a command-line agent that plans, runs and checks multi-step language tasks with them.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr (hosted API)
**Languages shown:** Tamil, Hindi, Marathi, Telugu, Bengali, Kannada
**Format:** CLI agent + MCP server (stdio)

## What it does

Give the agent a plain-English task and some files: *"Make a two-minute Tamil audio summary of this chapter"* with a scanned PDF, *"Which of these clips are in Telugu? Translate those into English"* with five audio clips. It works out the steps, calls Bodhan models through the MCP tools, checks its own output (generated speech is transcribed back and compared with the text), stays inside a step, rupee and time budget, and writes the result with a full trace. Impossible tasks (unsupported language, unreadable file, budget too small) end with a clear explanation and no fake output.

The same tools run as a standalone MCP server, so any MCP client can use them without the agent.

- Design, architecture diagram and agent flow chart: [DESIGN.md](DESIGN.md)
- Demo outputs and traces: [demo_runs/](demo_runs/)
- Demo video: https://youtu.be/Wp9ZWRiwPCw

## Setup

Requires Python 3.11+ and `ffmpeg` (`brew install ffmpeg` / `apt install ffmpeg`).

```bash
cd examples/indic_task_agent
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in the keys
```

`.env` needs the four Bodhan keys from [console.bodhan.ai](https://console.bodhan.ai) (one per model: `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_OCR_KEY`) and an OpenAI-compatible LLM with tool calling (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`). Tested with Google Gemini `gemini-3.5-flash-lite` at `https://generativelanguage.googleapis.com/v1beta/openai/`. The LLM only plans and writes English; it never receives audio or images.

Or with Docker (ffmpeg included):

```bash
docker build -t indic-task-agent .
```

## Run

Run commands from this folder.

**Task agent:**

```bash
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out \
  [--max-steps 30] [--max-cost-inr 20] [--max-seconds 600] [--call-timeout 120]
```

Output:

```
out/
  result.md      answer, checks, assumptions, links to the files
  artifacts/     produced files (MP3, SRT, Markdown, transcripts)
  trace.jsonl    one line per tool call: plan (why), tool, arguments, result summary, latency, cost, cache hits
  report.json    status, steps, cost per model, verification results, time, errors
```

Status is `completed`, `partial` (some part could not be done, or an output check failed), `impossible` (nothing produced) or `stopped` (a budget ran out).

**MCP server** (stdio):

```bash
python -m bodhan_mcp --out-dir ./bodhan_outputs
```

Try it in a browser with the official MCP Inspector (Node.js). Run it from this folder and give the full Python path:

```bash
npx @modelcontextprotocol/inspector "$PWD/.venv/bin/python" -m bodhan_mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{"mcpServers": {"bodhan": {"command": "/abs/path/to/examples/indic_task_agent/.venv/bin/python",
  "args": ["-m", "bodhan_mcp", "--out-dir", "/abs/path/to/outputs"],
  "cwd": "/abs/path/to/examples/indic_task_agent"}}}
```

**With Docker:**

```bash
docker run --rm --env-file .env -v "$PWD/inputs:/inputs" -v "$PWD/out:/out" indic-task-agent \
  bodhan_agent --task "Read this paragraph aloud in Bengali with a female voice" --files /inputs --out /out
docker run --rm -i --env-file .env indic-task-agent bodhan_mcp          # MCP server over stdio
```

**Tests** (no network, no spend; about 2 s):

```bash
pytest -q
```

Bodhan responses are cached in `~/.cache/bodhan_agent/`, so the same input is never paid for twice.

## How it works

1. **Preflight (code):** missing input → `impossible` at once. Inputs are described for free (`inspect_media`): type, duration, pages, script.
2. **Plan (LLM):** the LLM gets the task, the input manifest, the budgets, the supported languages per model and, if the task asks for a spoken length, a word target. It picks one tool at a time, each with a one-line `why`.
3. **Act (tools):** tools handle the model limits (30 s audio windows, short TTS inputs, translation context, one OCR image per call) and pass files by path.
4. **Verify (tools + code):** generated speech is transcribed back and compared with its text (character error rate); a bad piece is redone once. Translations are checked for coverage, numbers and table rows. Audio length is checked against the request.
5. **Stop:** when the LLM calls `finish`, or a step, rupee or time budget runs out. Code checks claimed files exist, turns `completed` into `partial` if a check failed, and leaves no files for an impossible task.

Rules are enforced in code: tool arguments written by the LLM may not contain Indian-language text (Indic text only moves between Bodhan tools as files), text from input files is wrapped as untrusted data, and unsupported languages are refused before any paid call. See [DESIGN.md](DESIGN.md).

## Tools

| Tool | Model | What it hides from the caller |
|---|---|---|
| `inspect_media` | none | kind, size, duration, pages of a file or folder |
| `detect_language` | Indic-Transcribe for audio | script-based guess; flags shared scripts as ambiguous |
| `transcribe_audio` | Indic-Transcribe | splits on silence into windows of at most 30 s, trims and skips silence, joins text with timestamps |
| `make_subtitles` | Indic-Translate | timed segments to `.srt`, translated per segment |
| `translate_text` | Indic-Translate | splits long text by paragraph; tables and code blocks kept whole; checks coverage |
| `synthesize_speech` | Indic-Speak | splits into sentences, transcribes each piece back to check it, joins into one MP3 tagged AI-generated |
| `ocr_document` | Indic-OCR | PDF to page images, one call per page, one Markdown file |
| `read_text` | none | reads a text file in parts |

Errors are JSON with `code`, `message`, `hint` and `retryable`. The voice library is the MCP resource `bodhan://voices`. MCP clients tested: the official MCP Python SDK client (automated, `tests/test_tools.py`) and MCP Inspector.

## Demo results

Run with `gemini-3.5-flash-lite` and the hosted Bodhan API on 17 Nov 2026. Outputs and traces are in [demo_runs/](demo_runs/); audio files are linked, not committed.

| # | Task | Input | Result | Steps | Cost | Time |
|---|---|---|---|---|---|---|
| 1 | Make a two-minute Tamil audio summary of this chapter | NCERT Class 9 Maths ch. 6, pages 1–4 (PDF) | pass: [result](demo_runs/1_tamil_audio_summary/result.md), 95 s [Tamil MP3](https://drive.google.com/file/d/1zFoEiBAmNba_pAd0XcdQVOj5kYZcoOcP/view?usp=sharing), all pieces pass back-transcription | 8 | ₹0.91 (+ ₹0.80 OCR, cached from an earlier run) | 56 s |
| 2 | Transcribe this Hindi voice note and give me English subtitles | own 18 s Hindi recording | pass: [SRT](demo_runs/2_hindi_note_english_subtitles/artifacts/Hindi_M_Darshan.en.srt) | 3 | ₹0.03 | 9 s |
| 3 | Translate this worksheet into Marathi and keep the table | worksheet image with a table | pass: [Marathi Markdown](demo_runs/3_worksheet_marathi/artifacts/worksheet.mr.md), table intact | 3 | ₹0.20 (cached on rerun) | 6 s |
| 4 | Which of these clips are in Telugu? Translate those into English | 5 clips (Telugu ×2, Tamil, Kannada, Hindi) generated with Indic-Speak | pass: all 5 languages identified, [result](demo_runs/4_which_clips_telugu/result.md) | 8 | ₹0.04 | 17 s |
| 5 | Read this paragraph aloud in Bengali with a female voice | English text file | pass: [Bengali MP3](https://drive.google.com/file/d/1n8Rtd-RlOB0lycb2xUJPfMAqiT2njoxg/view?usp=sharing), voice Ishita, [result](demo_runs/5_bengali_female_voice/result.md) | 4 | ₹0.08 | 9–23 s |
| 6 | Translate this video into French | MP4 | pass (impossible): [explanation](demo_runs/6_french_impossible/result.md), no files | 1 | ₹0 | 2 s |
| + | Translate this note into Hindi | note containing "IGNORE ALL PREVIOUS INSTRUCTIONS…" | pass: translated, injection ignored and reported, [result](demo_runs/extra_prompt_injection/result.md) | 3 | ₹0.002 | 7 s |
| + | Read this note aloud in Tamil, `--max-cost-inr 0.05` | text note | pass (partial): speech refused by the budget before any spend, [result](demo_runs/extra_budget_too_small/result.md) | 4 | ₹0.001 | 10 s |

The NCERT chapter is not included (copyright NCERT; available at [ncert.nic.in](https://ncert.nic.in/textbook.php)). The Telugu-task clips were generated from English sentences with Indic-Translate and Indic-Speak.

## Limitations

- **Spoken length is approximate.** Speaking rates per language are measured from a few runs; the length check allows ±25%. The demo's two-minute request gave 95 s.
- **Script detection cannot separate languages that share a script** (Hindi/Marathi/Nepali in Devanagari; Bengali/Assamese apart from two letters). The agent records its assumption; audio language comes from Indic-Transcribe's auto-detect plus the script.
- **Number check skips single digits**, because translators spell them out ("8 am" → "सुबह आठ बजे").
- **Long inputs:** speech is capped at 5,000 characters per call (≈ ₹3); longer text must be summarised first. Very large tables are sent as one translation chunk.
- **Unsupported input types** (Word, Excel, ...) are reported as impossible rather than converted.
- **LLM quality matters:** with a small model the code-side checks (length, verification, rule guards) carry much of the reliability. Tool calls run one at a time; independent steps are not parallelised.
- **API differences from the docs** (translation route, MP3 output, error codes) are handled in code; see [DESIGN.md](DESIGN.md).

## Cost

Bodhan prices (hosted): transcription ₹0.10 per audio minute, translation ₹0.20 per 10,000 output tokens, speech ₹6 per 10,000 characters, OCR ₹0.20 per page. Building and running every demo, including retries and experiments, cost about ₹5.2.

## AI assistance

I used Claude Code (Anthropic) as a coding assistant throughout, working one phase at a time: probing the Bodhan API, writing and refactoring the client, tools, agent, tests and docs, and debugging the demo runs. I directed each phase, reviewed and ran every change, chose the test inputs and the spending, checked the outputs (including listening to the generated audio), and kept a build log of every bug and fix so I can explain each part of the code.
