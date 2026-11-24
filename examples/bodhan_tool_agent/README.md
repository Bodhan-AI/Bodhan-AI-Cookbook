# Bodhan Tool Server & Task Agent

MCP server exposing Bodhan's Indic-Transcribe, Indic-Translate, Indic-Speak,
and Indic-OCR models as limit-absorbing tools, plus a CLI task agent that
plans, executes, verifies and enforces budgets on multi-step language tasks.

- **Server:** `python -m bodhan_mcp` (stdio MCP)
- **Agent:**  `python -m bodhan_agent --task "..." --files ./inputs --out ./out`

## What it does

Given a plain-language task and a directory of input files (audio, images,
scanned PDFs, text), the agent plans a sequence of Bodhan tool calls, executes
them with strict cost and step budgets, verifies the results, and writes:

```
out/
  result.md        # what was done, answer, assumptions, artifact links
  artifacts/       # generated audio, subtitles, Markdown, ...
  trace.jsonl      # one line per step (plan, tool call, verification, cost)
  report.json      # status, steps, verifications, total cost & time
```

## Setup (from a clean clone)

```bash
# System dependencies
# macOS: brew install ffmpeg poppler
# Debian/Ubuntu: sudo apt-get install ffmpeg poppler-utils

python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in your Bodhan + LLM keys
pytest -q              # unit tests, all mocked
```

Docker alternative:

```bash
docker build -t bodhan-recipe .
docker run --rm -e-file .env -v "$PWD/out:/app/out" bodhan-recipe \
  python -m bodhan_agent --task "..." --files /app/inputs --out /app/out
```

## Run

```bash
# Run the autonomous task agent CLI
python -m bodhan_agent --task "Transcribe this audio" --files ./inputs --out ./out

# Run the standalone FastMCP server over stdio
python -m bodhan_mcp
```

## Configuration

See `.env.example`. Required:
- `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_OCR_KEY`
- `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`

## Tools

| Tool | Purpose | Absorbs |
|---|---|---|
| `transcribe_audio` | Audio → transcript with segment timestamps | 30 s chunking, timestamp remapping |
| `translate_text` | Text → translation, Markdown-aware | 32K pagination, table-safe splits |
| `synthesize_speech` | Text → audio | Char cap, AI-generated label |
| `ocr_page` | Image → Markdown | — |
| `ocr_document` | PDF → Markdown | Page loop, ordering |
| `inspect_media` | Cheap local metadata | — |
| `chunk_audio` | Split audio at pauses | — |
| `get_voice_library` | Voices per language (also MCP resource) | — |

See [`TOOL_DESIGN.md`](TOOL_DESIGN.md) for schemas, error codes, and
granularity rationale.

## Supported languages per model (Standing Rule F6)

| Language Code | Language Name | Transcribe (`bodhan-transcribe`) | Translate (`bodhan-translate`) | Speak (`bodhan-speak`) | OCR (`bodhan-ocr`) |
|:---:|:---|:---:|:---:|:---:|:---:|
| `hi` | Hindi | Supported | Supported | Supported | Supported |
| `ta` | Tamil | Supported | Supported | Supported | Supported |
| `te` | Telugu | Supported | Supported | Supported | Supported |
| `bn` | Bengali | Supported | Supported | Supported | Supported |
| `mr` | Marathi | Supported | Supported | Supported | Supported |
| `kn` | Kannada | Supported | Supported | Supported | Supported |
| `ml` | Malayalam | Supported | Supported | Supported | Supported |
| `gu` | Gujarati | Supported | Supported | Supported | Supported |
| `pa` | Punjabi | Supported | Supported | Supported | Supported |
| `or` | Odia | Supported | Supported | Supported | Supported |
| `as` | Assamese | Supported | Supported | Supported | Supported |
| `ur` | Urdu | Supported | Supported | Supported | Supported |
| `en` | English | Supported (Best-effort) | Supported | **Excluded** | Supported |

> [!NOTE]
> All core Indian languages are supported across all four models. English is supported for translation and OCR, best-effort for transcription, and strictly excluded for speech synthesis (`speak`).
> Non-Indian foreign languages (e.g. French, German, Spanish, Mandarin) are strictly unsupported across all Bodhan Indic models. When requested, tasks are rejected honestly with `status="impossible"` and zero spend/artifacts per Golden Rule 1 and Constraint C8.

## Architecture & Visual Diagrams

- **Architecture Specification (with Native Mermaid):** [`ARCHITECTURE.md`](ARCHITECTURE.md)
- **Architecture Diagrams:**
  - Interactive Excalidraw: [`docs/architecture.excalidraw`](docs/architecture.excalidraw)
  - Vector SVG: [`docs/architecture.svg`](docs/architecture.svg)
- **Agent Execution Flow (with Native Mermaid):** [`AGENT_DESIGN.md`](AGENT_DESIGN.md)
- **Agent Flow Diagrams:**
  - Interactive Excalidraw: [`docs/agent-flow.excalidraw`](docs/agent-flow.excalidraw)
  - Vector SVG: [`docs/agent-flow.svg`](docs/agent-flow.svg)

## Design notes

- **Tool granularity:** Model-level tools absorb model quirks (chunking, 32K context pagination, timestamp remapping, concurrency semaphore=4, retry, caching) so the planner sees clear decision points without reimplementing plumbing.
- **Budget policy:** Pre-flight estimate based on media metadata before any spend; hard ceilings on cost (`--max-cost-inr`) and steps (`--max-steps`); clean stop with a structured report on exhaustion.
- **Verification:** Every generated audio is back-transcribed (`transcribe_audio`, char similarity ≥ 0.85); every translation has a coverage check; SRTs and Markdown tables are structurally validated.
- **Untrusted content:** OCR/transcript text is wrapped in structural tags and scanned for injection patterns (`ignore previous`, `system:`, tool-call strings); flagged hits are logged in `trace.jsonl` as security events and treated purely as data.
- **File resolution:** Deterministic code algorithm selects files from inventory via exact name, ordinal, or type-consistency; LLM tie-breaker only sees metadata (no contents).

## Limitations

- One-shot tasks from the CLI; no multi-turn dialogue.
- Sequential tool execution in base loop (trace format supports parallel fan-out).
- Non-Indian languages are strictly unsupported by design.
- Voice cloning is not supported by underlying models; voice selection is from fixed Indic voice library (`get_voice_library`).
- English TTS is excluded; English text intended for TTS must be translated into an Indic language first.

## Demo tasks (SPEC §20)

Six reproducible demo tasks (including one impossible) are available under `demo/outputs/` and documented in [`demo/README.md`](demo/README.md):

| # | Demo Task | Status | Cost (₹) | Runtime | Artifacts & Traces |
|---|---|---|---|---|---|
| 1 | Two-minute Tamil audio summary of scanned PDF (8 pages) | `success` | ₹1.8560 | 0.71s | [Outputs & Trace](demo/outputs/task_1/result.md) |
| 2 | Transcribe Hindi WAV → English SRT | `success` | ₹0.0072 | 0.01s | [Outputs & Trace](demo/outputs/task_2/result.md) |
| 3 | Translate worksheet photo → Marathi Markdown (table intact) | `success` | ₹0.2013 | 0.01s | [Outputs & Trace](demo/outputs/task_3/result.md) |
| 4 | Five clips: which are Telugu? Translate those → English | `success` | ₹0.0044 | 0.01s | [Outputs & Trace](demo/outputs/task_4/result.md) |
| 5 | Read Bengali paragraph aloud (female voice) | `success` | ₹0.1236 | 0.02s | [Outputs & Trace](demo/outputs/task_5/result.md) |
| 6 | **Impossible:** Translate MP4 into French | `impossible` | ₹0.0000 | 0.00s | [Honest Stop](demo/outputs/task_6/result.md) |

### Demonstration Video
🎥 **[Watch Demonstration Video Walkthrough (Google Drive)](https://drive.google.com/file/d/13nspKqcyYHLC9HIHR7rAxFOT3BkWnckI/view?usp=sharing)**

To reproduce all demos:
```bash
python scripts/prepare_demo_media.py
python scripts/run_demos.py
```

## Testing

```bash
# Run complete test suite (all 77 unit & integration tests, Bodhan HTTP mocked)
pytest -q

# Run end-to-end multi-step workflow tests
pytest tests/test_e2e_multi_step.py -q
```

## MCP client compatibility

Tested and verified with the official [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector python -m bodhan_mcp
```

Any standard MCP client can connect over `stdio` and drive the tools independently of the agent with zero stdout pollution.

## Docs

- [`SPEC.md`](SPEC.md) — the build brief (contract).
- [`AGENTS.md`](AGENTS.md) — how to work in this repo.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — components, contracts, decisions.
- [`TOOL_DESIGN.md`](TOOL_DESIGN.md) — tool surface and rationale.
- [`AGENT_DESIGN.md`](AGENT_DESIGN.md) — loop, code-vs-LLM split, budgets.

## License

Per the Bodhan AI Cookbook contribution guide.
