# Bodhan tool server and task agent

> An MCP server that exposes Bodhan's four Indic models as tools, and a command-line agent that uses them to complete multi-step language tasks.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages shown:** Hindi, Tamil, Marathi, Telugu, Bengali (any language the models support)
**Format:** CLI agent + MCP server (stdio)

> **Status: work in progress.** The MCP server and all its tools work today. The agent loop is being built; see [Roadmap](#roadmap).

## What it does

- `python -m bodhan_mcp` runs a standalone MCP server (stdio) with tools for speech-to-text, translation, speech synthesis and OCR. Every model limit (30-second audio windows, short TTS inputs, the translation context, multi-page PDFs) is handled inside the tools. Media moves by file path, never as inline base64.
- `python -m bodhan_agent` takes a plain-language task plus input files, plans, calls those tools, verifies its outputs, and writes `result.md`, `artifacts/`, `trace.jsonl` and `report.json`.

All speech-to-text, translation, speech synthesis and OCR go to Bodhan models. The companion LLM only plans, chooses tools, writes English and judges results. It never writes Indian-language text.

## Setup

Requires Python 3.10+ and [ffmpeg](https://ffmpeg.org/) on `PATH`.

```bash
cd examples/bodhan_tool_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # or: make setup (also installs dev tools)
cp .env.example .env                   # then fill in the Bodhan keys and LLM settings
```

Or with Docker: `docker build -t bodhan-tool-agent .`

## Run

```bash
# The task agent
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out \
  --max-steps 30 --max-cost-inr 20

# The MCP server on its own (stdio)
python -m bodhan_mcp

# Inspect the server's tools interactively (needs Node.js)
make inspect
```

Output layout, one folder per run:

```
out/
  result.md        # what was done, the final answer, assumptions, links to artifacts
  artifacts/       # files produced: audio, subtitles, Markdown, ...
  trace.jsonl      # one line per step: plan, tool, arguments, result summary, latency, cost
  report.json      # status, steps, verification results, total cost and time
```

## How it works

See [docs/architecture.md](docs/architecture.md), [docs/agent_flow.md](docs/agent_flow.md) and [docs/design_notes.md](docs/design_notes.md).

1. **Preflight (code):** expand inputs, check the files exist, inspect each one's media type, duration, page count and script.
2. **Plan (LLM):** structured plan with outputs, steps and assumptions.
3. **Feasibility (code):** languages supported? cost estimate within budget? Otherwise stop with an honest explanation.
4. **Act (LLM + MCP tools):** one tool call at a time. Results come back as file paths plus short previews.
5. **Verify (code + Bodhan):** back-transcribe generated audio, check translation coverage and tables, validate SRT.
6. **Report:** write `result.md`, `report.json` and the full trace.

## Tools

Every tool takes and returns **local file paths** (never base64) and returns `{"ok": true, ...}` or `{"ok": false, "error": {code, message, hint, retryable}}`. Model limits are handled inside the tools.

| Tool | Model | What it does, and how it handles the model's limits | Cost |
|---|---|---|---|
| `inspect_media` | none | Kind (audio, video, image, PDF, text), duration, pages, script; suggests the next tool and its cost | free |
| `ocr_document` | Indic-OCR | Image or PDF → Markdown. PDFs are rendered and read page by page in parallel; tables kept; per-page script; partial failures reported | ₹0.20/page |
| `transcribe_audio` | Indic-Transcribe | Any length or format, including video soundtracks. Cut at pauses into windows of 30 s or less, silence skipped, timestamps from our own cuts, language from the script. `segment_style="subtitle"` gives segments of 7 s or less | ₹0.10/min |
| `translate_text` | Indic-Translate | Text or Markdown. Long text split at sentences; **tables translated cell by cell and rebuilt**; each piece checked (non-empty, numbers kept, target script, length); free when the text is already in the target language | ≈₹0.20/10k output tokens |
| `translate_segments` | Indic-Translate | Timed segments → translated segments, timings kept | ≈₹0.20/10k tokens |
| `synthesize_speech` | Indic-Speak (+ Transcribe to verify) | Any length → one MP3 labelled AI-generated. Sentence-sized pieces in parallel; **each piece transcribed back and compared; a failing piece is re-synthesised once**; text in the wrong script is rejected | ₹6/10k chars |
| `make_srt` | none | Segments → validated SRT | free |
| `read_text` | none | Bounded reads of large outputs | free |
| `supported_languages`, `list_voices` | none | Language and voice catalogue (also exposed as resources `bodhan://languages` and `bodhan://voices`) | free |

What the live API actually returns, and why the tools are shaped this way: [docs/api_facts.md](docs/api_facts.md).

## Tests

```bash
make test        # 80+ unit and end-to-end tests against a fake Bodhan API; no keys, no cost
make live        # optional: one real round trip through the tools (about ₹0.05)
```

The tests cover chunking and limit handling for every tool, structured errors for bad inputs, caching (no input is sent twice), budget refusal, and a real stdio session driven by the MCP client SDK.

## Roadmap

- [x] Scaffold, config, language and voice data
- [x] Bodhan HTTP client: retries, backoff, concurrency limit, cache, cost ledger
- [x] Model tools with chunking and limit handling
- [ ] Agent loop: plan, act, verify, retry, budgets
- [ ] Demo tasks, self-evaluation suite, demo video

## Limitations

To be completed with measured results. Generated audio is AI-generated from Indic-Speak's fixed voice library; voices are not cloned.

## AI assistance

TODO (author): which coding tools were used and for what. The assignment asks for this disclosure.
