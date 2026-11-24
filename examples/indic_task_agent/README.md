# indic_task_agent

> An MCP server that exposes Bodhan's four Indic models as tools, plus a task agent that plans and runs multi-step language tasks with them.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages:** 25 for transcription, 26 for translation, 23 for speech (English plus 22 Indian languages)
**Format:** Python CLI agent and a stdio MCP server

## What it does

Give the agent a task in plain language and some files. It decides which tools to call, runs them,
checks its own work and writes the results, a log of every step and an honest report.

```bash
python -m bodhan_agent --task "Read this page and translate it into Hindi. Save it as hindi.md." \
    --files sample_data/page.png --out out/page_to_hindi
```

The same tools are also available as a standalone **MCP server**, so any MCP client can use them
without the agent.

* **Speech to text** for audio or video of any length (converted and split at silences automatically).
* **Translation** that keeps paragraphs and tables, splits long text and retries cut-off output.
* **Text to speech** for long text (split into sentences, joined into one WAV or MP3).
* **OCR** for images and multi-page PDFs, with tables as Markdown.
* **Checks:** generated speech is transcribed back and compared with the source text.
* **Honest failure:** unsupported languages, missing files and tiny budgets end with a clear report, never invented output.

## Setup

Requires Python 3.10 or newer and [ffmpeg](https://ffmpeg.org) on your PATH.

```bash
cd examples/indic_task_agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

Then edit `.env`. You need:

* **Bodhan keys** from [console.bodhan.ai](https://console.bodhan.ai). The console issues one key per model, so set
  `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY` and `BODHAN_OCR_KEY`.
  `BODHAN_API_KEY` is used as a fallback for any model without its own key.
* **A companion LLM** for the agent (the MCP server does not need one): any OpenAI-compatible endpoint that
  supports tool calling. Set `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`. It was developed with
  `openai/gpt-oss-120b` on Groq.

Small synthetic demo inputs (not committed) can be created with:

```bash
python scripts/make_test_page.py       # a worksheet with a table: sample_data/page.png, page.pdf
python scripts/make_demo_inputs.py     # injection page, 3-page newsletter, short video
```

## Run

```bash
# The task agent
python -m bodhan_agent --task "..." --files path/to/inputs --out ./out \
    [--max-steps 30] [--max-cost-inr 20] [--max-seconds 900]

# The MCP server on its own (stdio)
python -m bodhan_mcp
```

`--files` accepts files or folders. The exit code is `0` whenever the agent ran and wrote a report
(read `report.json` for the status: `completed`, `partial`, `failed` or `impossible`), and `2` if it
could not start, for example because a setting is missing.

### Example tasks

```bash
# Page image to Hindi Markdown (keeps the table)
python -m bodhan_agent --task "Read the text on this page and translate it into Hindi. Save it as hindi.md." \
    --files sample_data/page.png --out out/page_to_hindi

# Text to Tamil speech, checked by transcribing it back
python -m bodhan_agent --task "Translate this into Tamil and record it as audio: The meeting has been postponed to tomorrow." \
    --out out/tamil_audio

# Voice note to English subtitles
python -m bodhan_agent --task "Transcribe this voice note and write English subtitles as an SRT file." \
    --files path/to/voice_note.wav --out out/subtitles

# Multi-page PDF to a spoken summary
python -m bodhan_agent --task "Summarise this newsletter in English, then record the summary in Marathi." \
    --files sample_data/newsletter.pdf --out out/newsletter_summary

# An impossible task: no Bodhan model supports French
python -m bodhan_agent --task "Dub this video into French." --files sample_data/clip.mp4 --out out/french
```

### What a run writes

```
out/<run>/
  result.md        what was done, outputs, assumptions, verification, cost
  report.json      the same, machine-readable (status, steps, cost, files, warnings...)
  trace.jsonl      one line per step: plan, tool, arguments, result summary, latency, rupees
  artifacts/       the deliverables (audio, subtitles, Markdown...)
  work/            intermediate files (chunks, page images, text passed between tools)
```

## Use the tools from any MCP client

The server speaks MCP over stdio. Start it from this folder (so it finds `.env`), using the virtual
environment's Python:

```json
{
  "mcpServers": {
    "bodhan-indic-tools": {
      "command": ".venv/bin/python",
      "args": ["-m", "bodhan_mcp"],
      "cwd": "/full/path/to/examples/indic_task_agent"
    }
  }
}
```

It is tested with the official MCP Python SDK client over stdio (`tests/test_server.py`). To explore it
interactively with MCP Inspector, run `npx @modelcontextprotocol/inspector .venv/bin/python -m bodhan_mcp`.

| Tool | Does | Cost |
|---|---|---|
| `language_support` | Which models support a language, and its voices | free |
| `detect_text_language` | Script-based language guess (says when ambiguous) | free |
| `inspect_file` | Kind, duration, codecs, PDF page count | free |
| `transcribe_audio` | Speech to text, any length, with timed segments | Rs 0.10 per minute |
| `translate_text` | Translate text or a text file, keeping paragraphs and tables | Rs 0.20 per 10,000 tokens |
| `translate_segments` | Translate timed segments, keeping the timings (subtitles) | same |
| `speak_text` | Text to speech, WAV or MP3, choice of voice and style | Rs 6 per 10,000 characters |
| `verify_speech` | Transcribe audio back and compare with the expected text | Rs 0.10 per minute |
| `ocr_document` | Read images and PDFs page by page, tables as Markdown | Rs 0.20 per page |
| `read_text` | Read a slice of a text file (for summaries) | free |
| `write_subtitles` | Write an SRT file | free |
| `save_text` | Copy a text result into the deliverables folder under a chosen name | free |
| `get_usage` | Money spent so far, per model, and cache hits | free |

Files go in and out **by path**. Long text is saved to a file and returned as `text_file` plus a
preview, so audio, documents and long text never pass through a model's context.

## Tests

```bash
python -m pytest
```

The suite uses a fake Bodhan API and a scripted fake LLM, so it needs no keys, no network and costs nothing.
It covers the client (retries, budget, cache), every tool, the MCP server over a real stdio connection and the agent
loop (budgets, verification gate, prompt injection, misbehaving models).

## How it works

* [Architecture](docs/architecture.md) - modules and how they connect
* [Agent flow](docs/agent_flow.md) - the loop, and what is enforced in code
* [Design notes](docs/design_notes.md) - tool design, recovery, cost, safety, trade-offs

## Limitations

* The agent was developed against a free-tier LLM; per-minute token limits cause waits (the client backs off and retries).
* Translation is checked by script and length only, not by meaning. Only generated speech is verified automatically.
* Prompt-injection detection is a heuristic warning; the main protection is that no tool can send data out.
* Calls run one at a time, and not every language and voice has been tried against the live API.
* Audio requests are limited to about 30 seconds by the models; longer audio and text are chunked for you.