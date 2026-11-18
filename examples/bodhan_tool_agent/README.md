# Bodhan tool server and task agent

> An MCP server that exposes Bodhan's four Indic models as tools, and a command-line agent that uses them to complete multi-step language tasks.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages shown:** Hindi, Tamil, Marathi, Telugu, Bengali (any language the models support)
**Format:** CLI agent + MCP server (stdio)

> **Status: work in progress.** The scaffold, configuration, language data and the `supported_languages` / `list_voices` tools work today. The model tools and the agent loop are being built; see [Roadmap](#roadmap).

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

| Tool | Purpose | Status |
|---|---|---|
| `supported_languages` | Languages each Bodhan model accepts | ✅ |
| `list_voices` | Indic-Speak voices by language and gender | ✅ |
| `inspect_media` | Type, duration, pages and script of an input file | ⏳ |
| `ocr_document` | Image/PDF → Markdown, page by page | ⏳ |
| `transcribe_audio` | Any-length audio → transcript + timed segments | ⏳ |
| `translate_text` | Text/Markdown → target language, tables preserved | ⏳ |
| `translate_segments` | Timed segments → translated segments | ⏳ |
| `synthesize_speech` | Any-length text → MP3 (AI-generated) | ⏳ |
| `make_srt` | Segments → SRT subtitles | ⏳ |

## Tests

```bash
make test     # unit + end-to-end tests against mocked Bodhan and LLM endpoints
pytest -m live   # optional: real API calls (needs keys, costs credit)
```

## Roadmap

- [x] Scaffold, config, language and voice data
- [ ] Bodhan HTTP client: retries, backoff, concurrency limit, cache, cost ledger
- [ ] Model tools with chunking and limit handling
- [ ] Agent loop: plan, act, verify, retry, budgets
- [ ] Demo tasks, self-evaluation suite, demo video

## Limitations

To be completed with measured results. Generated audio is AI-generated from Indic-Speak's fixed voice library; voices are not cloned.

## AI assistance

TODO (author): which coding tools were used and for what. The assignment asks for this disclosure.
