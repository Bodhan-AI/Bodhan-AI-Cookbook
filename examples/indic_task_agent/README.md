# Indic task agent

> An MCP server that exposes Bodhan's four Indic models as tools, plus a command-line agent that plans and runs multi-step language tasks with them.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages shown:** Hindi, Tamil, Marathi, Telugu, Bengali
**Format:** CLI + MCP server

*Work in progress, built in phases.*

## Setup

```bash
cd examples/indic_task_agent
python3.11 -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -r requirements.txt
cp .env.example .env   # then paste your keys from https://console.bodhan.ai
```

System dependency: `ffmpeg`.

## Run

Run commands from this folder.

**Task agent:**

```bash
python -m bodhan_agent --task "Read this paragraph aloud in Bengali with a female voice" \
  --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]
```

It writes `out/result.md` (answer, assumptions, links to files), `out/artifacts/`, `out/trace.jsonl` (one line per tool call: plan, tool, arguments, result summary, latency, cost) and `out/report.json` (status, steps, cost, time). The LLM is any OpenAI-compatible endpoint with tool calling, set by `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`; tested with Google Gemini (`gemini-3.5-flash-lite` via `https://generativelanguage.googleapis.com/v1beta/openai/`).

**MCP server** (stdio, works with any MCP client):

```bash
python -m bodhan_mcp --out-dir bodhan_outputs
```

Try it in the browser with the official MCP Inspector (needs Node.js):

```bash
npx @modelcontextprotocol/inspector .venv/bin/python -m bodhan_mcp
```

**Tests** (no network, no spend):

```bash
pytest -q
```

Bodhan responses are cached in `~/.cache/bodhan_agent/`, so the same input is never paid for twice.

## Tools

| Tool | Model | What it hides from the caller |
|---|---|---|
| `inspect_media` | none | kind, size, duration, pages of a file or folder |
| `detect_language` | Indic-Transcribe for audio | script-based guess; flags shared scripts as ambiguous |
| `transcribe_audio` | Indic-Transcribe | splits on silence into windows of at most 30 s, trims and skips silence, joins text with timestamps |
| `make_subtitles` | Indic-Translate | timed segments to `.srt`, translated per segment |
| `translate_text` | Indic-Translate | splits long text by paragraph; tables and code blocks kept whole |
| `synthesize_speech` | Indic-Speak | splits into sentences, joins into one MP3 tagged AI-generated |
| `ocr_document` | Indic-OCR | PDF to page images, one call per page, one Markdown file |
| `read_text` | none | reads a text file in parts |

Tools take and return file paths; outputs go to `--out-dir`. Errors are JSON with `code`, `message` and `hint`. The voice library is the MCP resource `bodhan://voices`.
