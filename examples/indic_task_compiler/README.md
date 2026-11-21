# Indic Task Compiler — Bodhan MCP Tool Server & Task Agent

> Plan-checked, injection-proof MCP tool server (`bodhan_mcp`) and autonomous multi-step task agent (`bodhan_agent`) for Bodhan.ai's Indic speech, translation, synthesis, transliteration, and OCR models.

**Bodhan models:** `indic-transcribe`, `indic-translate`, `indic-speak`, `indic-ocr`, `indic-transliterate`  
**Languages shown:** All 22+ Bodhan-supported Indian languages and English (data-driven from the vendored API capability table)  
**Format:** Python package (MCP server + CLI task agent + static trace viewer)

## What it does

This recipe ships two composable components:

1. **`bodhan_mcp`** — A standalone Model Context Protocol (MCP) server (stdio by default, optional streamable HTTP with Bearer auth) that wraps all Bodhan models with automatic model-limit handling (silence-aware <=28 s audio windowing, Markdown/table-preserving translation chunking with `truncated` recovery, script-aware TTS sentence splitting + loudness normalization + AI-generated tagging, PDF rasterization via `pypdfium2` + OCR 502 backoff/split recovery), SHA-256 disk caching, retry with backoff, concurrency limiting, and structured errors.
2. **`bodhan_agent`** — An unattended CLI task agent built around two architectural invariants:
   - **CaMeL Privileged / Quarantined LLM split**: the privileged planner (`P-LLM`) never sees untrusted file, OCR, transcript, or translation content — only typed metadata, handles (`$step.text`), and probe enums. Content-reading operations run inside a tool-less quarantined reader (`Q-LLM`) whose outputs are schema-constrained or isolated into artifact handles.
   - **Pre-spend Feasibility Gate & Plan Compiler**: before spending a single rupee, deterministic code checks language support against Bodhan's capability matrix, file existence/kinds, and provable cost floors; then compiles the planner's flat step list into a validated, scope-locked parallel wave DAG with per-deliverable verification contracts.

All speech-to-text, translation, TTS, and OCR calls go exclusively to Bodhan models through `bodhan_mcp`. Provenance checks enforce in code that the LLM never writes or rewrites Indian-language text.

See the detailed documentation in:
- [Architecture Diagram](docs/architecture.md)
- [Agent Loop Flowchart](docs/agent-flow.md)
- [Design Notes](docs/design-notes.md)

## Setup

### System prerequisites

- Python 3.10–3.14
- `ffmpeg` and `ffprobe` on `PATH` (Ubuntu/Debian: `sudo apt-get install ffmpeg`, macOS: `brew install ffmpeg`)

### One-command install from a clean clone

```bash
cd examples/indic_task_compiler
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install --no-deps -e .
cp .env.example .env
# Edit .env with your Bodhan keys and OpenAI-compatible LLM credentials
```

Or build the Docker image (includes `ffmpeg`):

```bash
cd examples/indic_task_compiler
docker build -t indic-task-compiler .
```

## Run

### Task Agent CLI

```bash
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./outputs/tamil_summary \
  --max-steps 30 \
  --max-cost-inr 20
```

Each run writes:

```text
out/
├── result.md      # summary, final answer, assumptions, artifact links, AI-generated disclosure
├── artifacts/     # produced audio, SRT/VTT, Markdown, text files
├── trace.jsonl    # step-by-step replayable trace (epoch, tool, handle-only args, meta, latency, cost)
├── report.json    # status, steps, verification results, per-model cost, quality-target report
└── trace.html     # self-contained static trace viewer (open in any browser)
```

### Standalone MCP Server

Over stdio (default, works with any MCP client):

```bash
python -m bodhan_mcp
```

Over Streamable HTTP with Bearer token authentication (optional stretch feature):

```bash
python -m bodhan_mcp --http --host 127.0.0.1 --port 8765 --token "$MCP_AUTH_TOKEN"
```

### Connecting an off-the-shelf MCP client

Tested with the official Python `mcp` client SDK (`tests/test_mcp_stdio.py`) and compatible with MCP Inspector / Claude Desktop:

```json
{
  "mcpServers": {
    "bodhan": {
      "command": "/abs/path/to/examples/indic_task_compiler/.venv/bin/python",
      "args": ["-m", "bodhan_mcp"],
      "env": {
        "BODHAN_TRANSCRIBE_KEY": "...",
        "BODHAN_TRANSLATE_KEY": "...",
        "BODHAN_SPEAK_KEY": "...",
        "BODHAN_OCR_KEY": "..."
      }
    }
  }
}
```

Or interactively with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python -m bodhan_mcp
```

### Tool list (`bodhan_mcp`)

| Tool | Bodhan model | Purpose & limit handling |
|---|---|---|
| `inspect_files` | none (free) | Magic-byte & `ffprobe` media inspection, PDF page counts, script histograms, cost floor estimates |
| `detect_language` | `indic-transcribe` (audio probe only) / none (text) | Unicode script histogram + stopword disambiguation (Devanagari/Bengali) + <=10 s audio probe |
| `transcribe_audio` | `indic-transcribe` | `ffmpeg` -> 16 kHz mono WAV (supports mp4/m4a/webm), `silencedetect` <=28 s windowing, drops pure silence, returns text artifact + timed segments |
| `translate_text` | `indic-translate` | Table/code/LaTeX-preserving paragraph chunking, halves chunk on `truncated: true`, checks chunk coverage, table dimensions & digits |
| `synthesize_speech` | `indic-speak` | Script-aware sentence chunking (<=250 chars), `ffmpeg` concat + loudness normalization, ID3 `"AI-generated (Bodhan Indic-Speak)"` tag |
| `verify_speech` | `indic-transcribe` | Per-chunk back-transcription, normalized CER, n-gram repetition detector, duration/char-ratio anomaly check |
| `ocr_document` | `indic-ocr` | `pypdfium2` PDF->PNG rasterization (no poppler), `table_format=markdown`, 502 retry (`max_tokens` bump -> downscale -> split), reading-order Markdown + block confidence summary |
| `transliterate_text` | `indic-transliterate` | Native <-> Roman script conversion with chunking |
| `write_subtitles` | none (free) | Deterministic SRT/VTT generation from timed segments + proportional cue splitting |

MCP Resources exposed: `bodhan://languages`, `bodhan://voices`, `bodhan://pricing`.  
MCP Prompt exposed: `plan_task`.

### Tests & Self-Evaluation

```bash
pytest -v
python scripts/self_eval.py          # fake-transport held-out suite (free)
python scripts/self_eval.py --live   # live API evaluation (uses .env keys)
```

## How it works

1. **Intake (deterministic code)**: Resolves `--files`, inspects headers/duration/pages/scripts for free (`FileFacts`).
2. **Epoch A (`P-LLM`)**: Converts the plain-language `--task` + `FileFacts` into a typed `TaskSpec` (deliverables, languages mapped deterministically from tables, constraints, assumptions, probes needed).
3. **Feasibility Gate (deterministic code)**: Validates requested languages against Bodhan's per-model capability matrix, checks file existence/kinds, and compares the provable minimum cost floor against `--max-cost-inr`. Provably impossible tasks exit immediately at ₹0.
4. **Probe Epoch (`bodhan_mcp`)**: Runs only required metadata probes (e.g., audio language detection on unknown clips) and returns typed enums/numbers only.
5. **Epoch B (`P-LLM`) + Plan Compiler (deterministic code)**: Emits a flat step list with `$step.field` references (and optional `foreach` over probe lists). The compiler validates every tool name and argument against the MCP `list_tools` JSON Schemas, enforces input/output/language scope locks, infers the dependency DAG into parallel waves, and repairs once on compile errors.
6. **Execution + CaMeL Split**: Runs waves concurrently with pre-call budget reservation, SHA-256 disk caching, and retries. Untrusted content stays in the artifact store; only typed `meta` returns to `P-LLM`. Reading/summarizing content runs via the tool-less `Q-LLM` (`q_summarize_en`, `q_classify`, `q_extract_number`).
7. **Contracts Verification + Bounded Re-plan (Epoch C)**: Deterministic post-conditions check every deliverable (non-empty file, audio duration tolerance, target script ratio, back-transcription CER & repetition, table shape & digits, SRT monotonicity, and Indic provenance). Failing subgraphs trigger up to 2 targeted re-plans.
8. **Writers**: Emits `result.md`, `artifacts/`, `trace.jsonl`, `report.json`, and static `trace.html`.

## Limitations

- **Not built (by design)**: No external agent framework (LangGraph/LangChain), Redis, vector DB, voice cloning, speaker diarization, live SSE web UI (static `trace.html` instead), or self-hosted model weights.
- **SRT timestamps**: `indic-transcribe` does not return word-level timestamps; subtitle cue boundaries come from silence-aware VAD audio windows and proportional splitting, so cues are coarser than forced alignment.
- **Shared-script audio language ID**: Languages sharing Devanagari (`hi`/`mr`/`ne`/`mai`/`sa`/`kok`/`doi`/`brx`) or Bengali (`bn`/`as`) scripts are disambiguated by stopword hit-rates on a <=10 s probe; very short clips can remain ambiguous and are explicitly logged as assumptions in `result.md`.
- **Hosted API outages / credit exhaustion**: Rate limits (`429`) are retried with exponential backoff; persistent exhaustion stops cleanly with partial results and exact spend reported in `report.json`.
