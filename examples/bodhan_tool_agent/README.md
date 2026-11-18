# Bodhan Tool Server and Task Agent

## Summary

This project (`examples/bodhan_tool_agent`) provides two decoupled Python packages built on Bodhan AI's sovereign Indic foundation models:

- **`bodhan_mcp`**: A Model Context Protocol (MCP) server over `stdio` (MCP Python SDK `2.3.0`) exposing 8 model-agnostic tools covering translation, OCR, transcription, speech synthesis, media inspection, sandboxed text reading, subtitle generation, and zero-model Unicode script detection.
- **`bodhan_agent`**: An autonomous plan-act-verify Task Agent that orchestrates multi-step workflows across those MCP tools with pre-flight feasibility checks, strict `@stepN` reference passing, Indic-script and provenance guards, pre-flight budget enforcement, automatic code-driven verification, wall-clock timeouts, and full audit trails (`trace.jsonl`, `report.json`, `result.md`).

All 7 evaluation matrix tasks (6 executable multi-modal/security tasks plus 1 pre-flight impossible task) pass validation and are backed by 179 automated unit and integration tests using fake HTTP/LLM mocks with zero spend.

## How to run

### Project location

The project lives in `examples/bodhan_tool_agent`. Run all commands below from that directory:

```bash
cd examples/bodhan_tool_agent
```

### Prerequisites & installing `ffmpeg`

- Python `3.11+`
- `ffmpeg` and `ffprobe` on system `PATH` (used for audio probing, silence-based chunking, and WAV assembly):
  - **Windows**: `winget install Gyan.FFmpeg` (or `choco install ffmpeg`) and reopen your terminal.
  - **macOS**: `brew install ffmpeg`
  - **Linux (Debian/Ubuntu)**: `sudo apt-get update && sudo apt-get install -y ffmpeg`

### Virtual environment & installation

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.lock
pip install -e .
```

### Configure `.env` from `.env.example`

Copy `.env.example` to `.env` and fill in your own API keys (never commit `.env`):

```bash
# macOS / Linux:
cp .env.example .env
# Windows PowerShell:
Copy-Item .env.example .env
```

Required variables in `.env`:
- `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_OCR_KEY` (from `https://console.bodhan.ai`)
- `LLM_BASE_URL` (e.g. `https://api.groq.com/openai/v1`), `LLM_MODEL` (e.g. `openai/gpt-oss-20b` or `openai/gpt-oss-120b`), `LLM_API_KEY`

### Sample files

Large binary sample media are **not** shipped in git; only `samples/README.md` and two tiny text fixtures (`samples/notice.txt`, `samples/notice_injected.txt`) are committed. Expected sample files in `samples/` and how to create them (see also `samples/README.md`):

- `notice.txt` *(shipped)*: short English notice text.
- `notice_injected.txt` *(shipped)*: 2-line English notice containing a prompt-injection payload for Task 7.
- `notice_2page.pdf`: any 2-page English PDF (used for Task 1; evaluated with a 2-page PDF printout of the Wikipedia Pythagorean theorem article).
- `table_photo.png`: a photo or screenshot of an English table (used for Task 3).
- `hindi_voicenote.wav`: a short Hindi speech clip in WAV format (used for Task 2; record or convert with `ffmpeg -i input.mp4 -ar 16000 -ac 1 samples/hindi_voicenote.wav`).
- `bengali_notice.txt`: short Bengali paragraph generated via `python scripts/make_bengali_notice.py` (used for Task 5; skips paid calls if the file already exists).
- `clip_telugu.wav`, `clip_hindi.wav`, `clip_tamil.wav`: 3 short Indic WAV clips generated via `python scripts/make_clips.py` (used for Task 4; skips paid calls if the files already exist).
- `lecture.mp4` (and optional `corrupt.mp4`): any small placeholder MP4 file for Task 6 impossible-task validation (e.g. `ffmpeg -f lavfi -i color=c=black:s=320x240:d=1 -f lavfi -i anullsrc -t 1 samples/lecture.mp4`).

### Run the MCP server, Task Agent, and tests

Paid Bodhan API calls are **enabled by default** whenever the required API keys are configured in `.env` or the environment. To block all paid calls (dry/offline mode), set `BODHAN_ALLOW_LIVE=0` (`cmd`: `set BODHAN_ALLOW_LIVE=0`, `bash`: `export BODHAN_ALLOW_LIVE=0`, PowerShell: `$env:BODHAN_ALLOW_LIVE="0"`). Automated tests force `BODHAN_ALLOW_LIVE=0` via `tests/conftest.py` so `pytest` never makes live calls. The batch evaluation runner `scripts/run_matrix.ps1` keeps its own explicit check requiring `BODHAN_ALLOW_LIVE=1` as a human confirmation before running the full paid matrix.

```bash
# Run offline unit & integration tests (zero network spend; conftest.py sets BODHAN_ALLOW_LIVE=0)
python -m pytest -q

# Start the MCP server over stdio
python -m bodhan_mcp

# Run the Task Agent (paid calls enabled when keys are configured; set BODHAN_ALLOW_LIVE=0 to block)
python -m bodhan_agent --task "Translate notice.txt into Hindi." --files ./samples --out ./out/run1 --max-steps 30 --max-cost-inr 5.0
```

### Off-the-shelf MCP client verification

Verified end-to-end against **MCP Inspector CLI** (`@modelcontextprotocol/inspector` `2.15.0`):

```bash
# List all 8 registered MCP tools over stdio
npx --yes @modelcontextprotocol/inspector --cli .venv/Scripts/python.exe -m bodhan_mcp --method tools/list

# Invoke the zero-model Unicode classifier tool (no Bodhan API call, Rs 0.00)
npx --yes @modelcontextprotocol/inspector --cli .venv/Scripts/python.exe -m bodhan_mcp --method tools/call --tool-name detect_script_language --tool-arg "text=नमस्ते, आज मौसम अच्छा है।"
```

Result: all 8 tools (`indic_translate`, `indic_ocr`, `indic_transcribe`, `indic_speak`, `inspect_media`, `read_text_file`, `make_srt`, `detect_script_language`) discovered, and `detect_script_language` returned `{"success": true, "language": "hi/mr/ne", "character_count": 25}` (`isError: false`).

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full Mermaid diagram and component breakdown.

- **Peer packages**: `src/bodhan_mcp` (standalone stdio MCP server) and `src/bodhan_agent` (autonomous task runner consuming `bodhan_mcp` via `BodhanMcpClient` over stdio).
- **Shared safety & caching layer (`bodhan_mcp.common`)**:
  - **Live-call kill switch**: paid requests to `https://api.bodhan.ai/v1` are allowed when API keys are configured and refused with `LIVE_CALLS_DISABLED` whenever `BODHAN_ALLOW_LIVE=0` (or `false`). Tests force `BODHAN_ALLOW_LIVE=0` via `tests/conftest.py`, and `scripts/run_matrix.ps1` requires `BODHAN_ALLOW_LIVE=1` before running the full matrix.
  - **Cache-before-processing**: commits raw HTTP 200 bytes to `.cache/bodhan_*` before post-processing and never retries after a 200 response.
  - **Append-only spend ledger**: records every dispatched call (`dispatched` -> `completed` / `failed`) in `.cache/spend_ledger.jsonl` keyed by `run_id`.
- **Makefile & Dockerfile**: `Makefile` defines `install`, `test`, `check` (`compileall`, `pytest`, `ruff check`, `ruff format --check`), `lint`, `smoke`, and `run-mcp`. Because `make` was not installed on this Windows machine, the equivalent `check` commands were run directly (all passed). A `Dockerfile` template is also included *(not built or tested with `docker build` on this machine)*.

## Agent flow

See [docs/agent_flow.md](docs/agent_flow.md) for the full Mermaid execution flow diagram.

1. **Pre-flight feasibility**: Checks 48 non-Indian world languages (distinguishing topic mentions from requested target/audio languages), file existence, allowed extensions, non-zero byte sizes, and `ffprobe`/`PyMuPDF` integrity. Infeasible tasks terminate in `0.00s` with `status: "impossible"` and `Rs 0.0000` spend.
2. **Plan turn (`tools=None`)**: Turn 1 produces a concise English plan logged to `trace.jsonl`; `parse_planned_tools` extracts declared tools so later tool outputs append `Remaining planned tools: ...`.
3. **Guards before dispatch**: Strict `@stepN.field` resolution, failed-step reference blocking, Indic-script guard, provenance registry (`TextProvenanceRegistry`), pre-flight cost ceiling (`budget_stopped`), duplicate refusal naming the earlier step and valid references, same-script translation refusal, and a 2-attempt cap per source group/target language.
4. **Loop termination guards**: Forced finish triggers after 3 consecutive duplicate repeats with `status: "partial"` (only if verified output/artifact exists) or `"failed"` — never `"completed"`. Calling `finish(status="completed")` while a planned `indic_*` tool never ran triggers one pushback, then downgrades to `"partial"` on repeat. JSON-wrapped finish explanations are unwrapped cleanly.
5. **Code-driven verification**: Automatically executes OCR/transcript integrity checks, deterministic `summary_grounding` and `length_constraint` checks for LLM-authored summaries, back-translation + coverage checks, and speech round-trip CER via `indic_transcribe`.
6. **Timeouts, rate-limit accounting & deliverables**: Every LLM, MCP tool, and verification call runs under `asyncio.wait_for` against the real wall-clock budget (including HTTP 429 waits reported separately as `rate_limit_wait_ms`). Full OCR/transcript text is written to `artifacts/step_N_<tool>.txt`; `result.md` lists only final verified text outputs and text passed to `indic_speak` (excluding failed grounding/verification candidates) and shows a 300-char preview plus link for non-final OCR/transcripts.

## Tool design

`bodhan_mcp` exposes 8 focused, model-agnostic tools over MCP `stdio` (internal agent controls like `finish` are excluded):

| Tool | Purpose | Key Design Details |
| :--- | :--- | :--- |
| `indic_translate` | Translate English <-> 22 scheduled Indian languages | Automatic English pivot for Indic-to-Indic; chunking; preserves Markdown/tables/LaTeX; disk cache |
| `indic_ocr` | OCR multi-page PDFs and images (`.pdf`, `.png`, `.jpg`, `.webp`) | PyMuPDF 150 DPI rasterization (no external poppler); per-page cache; explicit 90s/page timeout |
| `indic_transcribe` | Speech-to-text across 25+ Indian languages and English | Silence-based `<= 30s` chunking via `ffmpeg`/`ffprobe`; optional `language` ISO code; per-chunk cache |
| `indic_speak` | Text-to-speech across 45 voices | Sentence-aware 250-char chunking; `ffmpeg` concatenation to 24 kHz mono 16-bit PCM WAV; `voice_gender_source: "inferred"` |
| `inspect_media` | Inspect audio/PDF/image metadata | Sandboxed to `--files`; reports duration, codec/format, and PDF page count |
| `read_text_file` | Read `.txt`, `.md`, `.csv`, `.srt` files | Sandboxed to `--files`; path-traversal protection and `max_chars` limit |
| `make_srt` | Build `.srt` subtitle files | Deterministic millisecond timestamps and 42-char line wrapping from transcript/translated text |
| `detect_script_language` | Identify script & language cluster | Zero-model Unicode block analysis (Rs 0.00 cost) |

## Where the LLM is used

The agent was developed and tested on Groq's `openai/gpt-oss-20b` and `openai/gpt-oss-120b` via `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY`, and any OpenAI-compatible model with tool calling can be used. The frontier LLM (`OpenAILLMClient`) is strictly limited to **English-language orchestration**:

- Generating the Turn 1 English execution plan.
- Choosing which MCP tool to call next and passing `@stepN.field` references between steps.
- Authoring English summaries or English analysis (e.g. Task 1's ~400-character English summary of `notice_2page.pdf` before passing it to `indic_translate`).
- Producing the final English explanation for `finish`.

The LLM **never** authors, edits, translates, or speaks Indian-language text: `check_indic_guard`, `validate_tool_call_provenance`, and `sanitize_final_answer` block LLM-written Indic/Perso-Arabic characters in tool calls and final answers. Verification is also code-driven (no LLM-as-judge).

## Bodhan models used

All Indian-language AI processing calls `https://api.bodhan.ai/v1` with explicit connect/read/write timeouts and two-phase ledger logging:

- **`indic-translate`** (`POST /v1/chat/completions`): Rs 0.20 per 10,000 output tokens. Used for direct translation, Indic-to-Indic pivoting, and code-driven back-translation verification.
- **`indic-ocr`** (`POST /v1/chat/completions`): Rs 0.20 per page image. Used for PDF and image digitization preserving Markdown tables and LaTeX math.
- **`indic-transcribe`** (`POST /v1/audio/transcriptions`): Rs 0.10 per audio minute (pro-rata by second). Used for speech-to-text and round-trip speech CER verification.
- **`indic-speak`** (`POST /v1/audio/speech`): Rs 6.00 per 10,000 input characters. Used for TTS synthesis across 45 voices.

## Results

All figures below are copied directly from [docs/results.md](docs/results.md) (measured from `out/demo_task*/report.json`, `trace.jsonl`, and `.cache/spend_ledger.jsonl`):

| Task | Status | LLM model | Steps | LLM Calls (Tokens) | Cost (INR) | Total Time | LLM Time | Tool Time | Rate-Limit Wait | Overhead | Verification | Tools called |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- |
| **Task 6 (Impossible Video & French)** | `impossible` | `None (0 LLM calls)` | 0 | 0 (0) | Rs 0.0000 | 0.00s | 0.0ms | 0.0ms | 0.0ms | 0.0ms | None | None |
| **Task 7 (Prompt Injection Defense)** | `completed` | `openai/gpt-oss-120b` | 2 | 4 (7126) | Rs 0.0000 | 8.47s | 6618.0ms | 129.2ms | 0.0ms | 1721.4ms | indic_translate_back_translation: **pass** (score: 0.84) | `read_text_file` (1), `indic_translate` (1) |
| **Task 2 (Hindi Audio to English SRT)** | `completed` | `openai/gpt-oss-20b` | 3 | 5 (8806) | Rs 0.0140 | 13.10s | 8000.4ms | 1995.7ms | 0.0ms | 3099.5ms | indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_translate_back_translation: **pass** (score: 0.63) | `indic_transcribe` (1), `indic_translate` (1), `make_srt` (1) |
| **Task 3 (Table OCR to Marathi Markdown)** | `completed` | `openai/gpt-oss-20b` | 2 | 4 (5079) | Rs 0.0215 | 13.58s | 5128.0ms | 3638.4ms | 0.0ms | 4816.3ms | indic_ocr_integrity: **pass** (score: 1.00)<br>indic_translate_back_translation: **pass** (score: 0.92) | `indic_ocr` (1), `indic_translate` (1) |
| **Task 5 (Bengali TTS Female Voice)** | `completed` | `openai/gpt-oss-20b` | 2 | 4 (6456) | Rs 0.0094 | 14.31s | 7526.8ms | 1281.8ms | 0.0ms | 5497.4ms | indic_speak_audio_cer: **pass** (score: 0.00) | `read_text_file` (1), `indic_speak` (1) |
| **Task 4 (Audio Identification & Translation)** | `completed` | `openai/gpt-oss-20b` | 6 | 11 (17398) | Rs 0.0000 | 45.88s | 18562.7ms | 3919.4ms | 19000.0ms | 4403.2ms | indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_translate_back_translation: **pass** (score: 0.63) | `indic_transcribe` (3), `indic_translate` (1), `detect_script_language` (2) |
| **Task 1 (PDF to Tamil Audio Summary)** | `completed` | `openai/gpt-oss-120b` | 10 | 27 (43556) | Rs 0.3588 | 235.12s | 41628.9ms | 49452.0ms | 142061.6ms | 1975.2ms | indic_ocr_integrity: **pass** (score: 1.00)<br>summary_grounding: **pass** (score: 0.82)<br>length_constraint: **pass** (score: 1.12)<br>indic_translate_back_translation: **pass** (score: 0.88)<br>summary_grounding: **fail** *(superseded)* (score: 0.08)<br>length_constraint: **pass** (score: 0.86)<br>indic_translate_back_translation: **pass** (score: 0.48)<br>summary_grounding: **pass** (score: 0.50)<br>length_constraint: **pass** (score: 1.09)<br>indic_translate_back_translation: **pass** (score: 0.76)<br>summary_grounding: **fail** (score: 0.09)<br>length_constraint: **pass** (score: 0.76)<br>indic_translate_back_translation: **warn** (score: 0.29)<br>indic_speak_audio_cer: **pass** (score: 0.05) | `indic_ocr` (1), `indic_translate` (4), `indic_speak` (1) |

- **LLM model note**: `report.json` now records `llm_model` from `LLM_MODEL`. For existing reports written before that field was added, Tasks 2, 3, 5, and 4 used `openai/gpt-oss-20b`, Task 1 and the latest Task 7 run used `openai/gpt-oss-120b`, and Task 6 made 0 LLM calls.

## Demo video

https://drive.google.com/file/d/16TgIXnVj3m7muICbnJlncxhTA93hCjMV/view?usp=sharing

## Trade-offs

- **Groq free-tier rate limits (`8,000` TPM and daily token quota)**: Multi-turn tasks with tool schemas and OCR previews can trigger HTTP 429 responses on Groq's free tier. `OpenAILLMClient` caps each 429 retry wait at 30 s, logs each wait to the console, and reports cumulative wait time separately in `rate_limit_wait_ms` (outside `llm_ms` and `overhead_ms`), while the real wall-clock timeout still caps total run duration.
- **Untuned verification thresholds**: Back-translation similarity (`>= 0.35` pass, `>= 0.20` warn), `summary_grounding` content-word overlap (`>= 0.50` pass, `0.30–0.50` warn, `< 0.30` fail), `length_constraint` (`+-30%` pass, report-only warn otherwise), and speech CER (`<= 0.20` pass, `<= 0.40` warn) are untuned heuristics documented in `DECISIONS.md` rather than calibrated benchmarks.
- **1,200-character English OCR preview limit**: To protect LLM TPM budgets, English OCR text sent into the next LLM turn is capped at 1,200 characters (850 head + 350 tail for multi-page PDFs) and compacted to a ~180-char stub on later turns. Summaries of long documents therefore rely on the head/tail preview rather than every middle paragraph.
- **Marathi and Hindi share Devanagari**: `detect_script_language` cannot distinguish Hindi from Marathi (or Nepali/Sanskrit) by Unicode script alone; only Bodhan `indic-translate` and back-translation verification differentiate them.
- **Extra LLM calls on complex tasks**: Open-weight tool-calling models can propose redundant or ungrounded calls before converging (Task 1 required 27 LLM calls / 43,556 tokens across retries, grounding rejections, and duplicate refusals before completing with a verified spoken summary).
- **Inferred voice gender metadata**: Bodhan API docs list voice names by language without an explicit gender column; gender tags are inferred from conventional Indian names (`voice_gender_source: "inferred"`) and have not been verified by human listening.

## Cost and runtime

Measured directly from [docs/results.md](docs/results.md) and `.cache/spend_ledger.jsonl`:

- **Sum of the 7 matrix task reports**: `Rs 0.4037`
- **Total spend recorded in `.cache/spend_ledger.jsonl`**: `Rs 1.7852`
- **Spend not in matrix task reports (earlier development runs plus prep scripts)**: `Rs 1.3816`
- **Cached reruns vs. cold cost**: Because `.cache/bodhan_*` caches raw HTTP 200 responses, cached reruns show lower cost than an uncached cold run. Task 1's 2-page PDF OCR (about `Rs 0.40`) was paid in an earlier development attempt (`run_09a35cca`, `Rs 0.7065`) and served from cache at `Rs 0.0000` during the recorded Task 1 matrix run (`Rs 0.3588` for translation, speech synthesis, and verification). Several tasks (at least 3, 4 and 7) hit cache entries populated by earlier development runs, so recorded costs are lower than first-run costs.
- **Console balance check**: Bodhan console balance after the matrix: Rs 8.21 of Rs 10.00 (Rs 1.79 used), matching the ledger total of Rs 1.7852.
- **Runtime range across the matrix**: `0.00s` (Task 6 pre-flight impossible), `8.47s–14.31s` (Tasks 7, 2, 3, 5 with `0.0ms` rate-limit wait), `45.88s` (Task 4, including `19000.0ms` rate-limit wait), and `235.12s` (Task 1, including `142061.6ms` rate-limit wait, `41628.9ms` LLM time, `49452.0ms` tool+verification time, and `1975.2ms` overhead).

## AI assistance

This project was built with Antigravity and Claude as coding assistants for implementation, refactoring, test generation, and debugging. I (Mohammed Iqbal Nawab, `@Iqbal-dev12`) directed the architecture, ran and inspected the live evaluations, and reviewed each phase against the assignment brief.
