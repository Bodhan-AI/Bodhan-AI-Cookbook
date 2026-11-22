# Indic Task Compiler — Bodhan MCP Tool Server & Task Agent

> Plan-checked, injection-proof MCP tool server (`bodhan_mcp`) and autonomous multi-step task agent (`bodhan_agent`) for Bodhan.ai's four Indic models (speech-to-text, translation, speech synthesis, and OCR).

**Bodhan models:** `indic-transcribe`, `indic-translate`, `indic-speak`, `indic-ocr`  
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

Tested with **MCP Inspector** (`@modelcontextprotocol/inspector@0.17.2`, CLI mode: `tools/list`, `resources/list`, good and bad `tools/call` inputs) and the official Python `mcp` client SDK (`tests/test_mcp_stdio.py`). Claude Desktop config:

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

# Scripted, no browser:
npx @modelcontextprotocol/inspector --cli python -m bodhan_mcp --method tools/list
npx @modelcontextprotocol/inspector --cli python -m bodhan_mcp \
  --method tools/call --tool-name transcribe_audio --tool-arg path=/nonexistent.wav   # -> ok:false, code file_not_found
```

### Tool list (`bodhan_mcp`)

| Tool | Bodhan model | Purpose & limit handling |
|---|---|---|
| `inspect_files` | none (free) | Magic-byte & `ffprobe` media inspection, PDF page counts, script histograms, cost floor estimates |
| `detect_language` | `indic-transcribe` (audio probe only) / none (text) | Unicode script histogram + stopword disambiguation (Devanagari/Bengali) + <=10 s audio probe |
| `transcribe_audio` | `indic-transcribe` | `ffmpeg` -> 16 kHz mono WAV (supports mp4/m4a/webm), `silencedetect` <=28 s windowing, drops pure silence, returns text artifact + timed segments |
| `translate_text` | `indic-translate` | Table/code/LaTeX-preserving paragraph chunking, halves chunk on `truncated: true`, output-script guard (auto-pivots via English through Bodhan if Indic->Indic returns Latin; `wrong_output_script` on persistent failure), `attempt` cache-busting, chunk/table/digit coverage |
| `synthesize_speech` | `indic-speak` | Deterministic Markdown-to-speakable conversion (tables -> labeled sentences), script-aware sentence chunking (<=250 chars), `attempt` + `only_chunks` selective failed-chunk repair, `ffmpeg` concat + loudnorm, ID3 `"AI-generated (Bodhan Indic-Speak)"` tag |
| `verify_speech` | `indic-transcribe` | Per-chunk back-transcription against exact speakable text in chunk manifest, digit-ITN-tolerant CER, n-gram repetition detector, duration/char-ratio anomaly check, `attempt` cache-busting |
| `ocr_document` | `indic-ocr` | `pypdfium2` PDF->PNG rasterization (no poppler), `table_format=markdown`, 502 retry (`max_tokens` bump -> downscale -> split), reading-order Markdown + block confidence summary |
| `write_subtitles` | none (free) | Deterministic SRT/VTT generation from timed segments + proportional cue splitting |

MCP Resources exposed: `bodhan://languages`, `bodhan://voices`, `bodhan://pricing`.  
MCP Prompt exposed: `plan_task`.

### Tests & Self-Evaluation

```bash
pytest -v                            # 46 unit, MCP stdio, CaMeL, API-regression, bug-regression & E2E tests
python scripts/self_eval.py          # 21-task offline machinery suite (12 core + 9 reviewer-unseen regression; ScriptedFakeLLM + FakeBodhanTransport)
python scripts/self_eval.py --live   # live API evaluation (uses .env keys; --group {all,core,regression})
```

### Demo video

**[Watch the demo (≤5 min)](https://youtu.be/OXJhd09qrEo)**

### Live Demo Results & Cost (`scripts/demo.sh`)

Committed copies of each run live in [`demo_results/`](demo_results/) (local absolute paths rewritten to `./`; `trace.html` opens offline). The Tamil MP3 is re-encoded to 32 kbps mono to stay under the Cookbook's 1 MB file limit; rerun `scripts/demo.sh` for full-quality output in `outputs/`.

Tested live against hosted Bodhan APIs (`https://api.bodhan.ai`) with `anthropic/claude-haiku-5.5` (OpenRouter). Total cumulative live spend across smoke tests, fixture generation, demo runs, live self-eval, and reviewer-unseen verification: **₹11.25** (177 Bodhan API calls).

| # | Demo task | Status | Cold-cache cost | Fresh-dir cached cost | Runtime (cold / cached) | Outputs & traces |
|---|---|---|---|---|---|---|
| 1 | 8-page scanned PDF -> 2-min Tamil audio summary + text | `success` | ~₹2.68 (₹4.26 pre-cal) | ₹0.9874 (OCR/en cached) | 76.8s / 99.6s | [result](demo_results/demo_01_tamil_summary/result.md) · [trace](demo_results/demo_01_tamil_summary/trace.jsonl) · [report](demo_results/demo_01_tamil_summary/report.json) · [artifacts](demo_results/demo_01_tamil_summary/artifacts/) |
| 2 | 3-min Hindi voice note -> English SRT subtitles | `success` | ₹0.3213 | ₹0.0000 | 35.4s / 14.0s | [result](demo_results/demo_02_hindi_srt/result.md) · [trace](demo_results/demo_02_hindi_srt/trace.jsonl) · [report](demo_results/demo_02_hindi_srt/report.json) · [artifacts](demo_results/demo_02_hindi_srt/artifacts/) |
| 3 | Worksheet photo -> Marathi Markdown (keep table) | `success` | ₹0.2030 | ₹0.0000 | 14.4s / 11.1s | [result](demo_results/demo_03_marathi_worksheet/result.md) · [trace](demo_results/demo_03_marathi_worksheet/trace.jsonl) · [report](demo_results/demo_03_marathi_worksheet/report.json) · [artifacts](demo_results/demo_03_marathi_worksheet/artifacts/) |
| 4 | 5 clips -> identify Telugu & translate to English | `success` | ₹0.1292 | ₹0.0000 | 35.3s / 18.7s | [result](demo_results/demo_04_telugu_clips/result.md) · [trace](demo_results/demo_04_telugu_clips/trace.jsonl) · [report](demo_results/demo_04_telugu_clips/report.json) · [artifacts](demo_results/demo_04_telugu_clips/artifacts/) |
| 5 | Bengali paragraph aloud (female voice) | `success` | ₹0.2825 | ₹0.0000 | 35.5s / 13.0s | [result](demo_results/demo_05_bengali_tts/result.md) · [trace](demo_results/demo_05_bengali_tts/trace.jsonl) · [report](demo_results/demo_05_bengali_tts/report.json) · [artifacts](demo_results/demo_05_bengali_tts/artifacts/) |
| 6 | Translate video into French (impossible) | `infeasible` | ₹0.0000 | ₹0.0000 | 1.5s / 1.7s | [result](demo_results/demo_06_impossible_french/result.md) · [trace](demo_results/demo_06_impossible_french/trace.jsonl) · [report](demo_results/demo_06_impossible_french/report.json) · no artifacts (refused at ₹0) |
| 7 | Bonus: CaMeL prompt-injection scanned worksheet | `success` | ₹0.2043 | ₹0.0000 | 12.9s / 11.4s | [result](demo_results/demo_07_camel_injection/result.md) · [trace](demo_results/demo_07_camel_injection/trace.jsonl) · [report](demo_results/demo_07_camel_injection/report.json) · [artifacts](demo_results/demo_07_camel_injection/artifacts/) |

A secondary 2-task probe on Google Gemini's OpenAI-compatible free tier (`gemini-3.6-flash`) completed Epoch A and cached probes on the 5-clip task before encountering persistent free-tier HTTP 503 overload across all 4 retry attempts (failing honestly at ₹0 without fabricating output).

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
- **Hosted API quirks & outages**: Live `POST /translate` returned HTTP 404 on `api.bodhan.ai` during testing, so `BodhanClient.translate` uses Bodhan's OpenAI-compatible `POST /v1/chat/completions` (`model: "indic-translate"`). `indic-speak` returns RIFF/WAVE PCM16 bytes under `Content-Type: audio/mpeg` (handled via magic-byte inspection). Rate limits (`429`) and transient `5xx` errors are retried with exponential backoff; persistent exhaustion stops cleanly with partial results and exact spend reported in `report.json`.
