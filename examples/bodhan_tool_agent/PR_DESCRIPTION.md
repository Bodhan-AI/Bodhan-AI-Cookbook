# [Agentic Eng Assignment] Tool server and agent - Mohammed Iqbal Nawab

## Summary

Adds `examples/bodhan_tool_agent`: a standalone Model Context Protocol (MCP) server (`bodhan_mcp`) and an autonomous plan-act-verify Task Agent (`bodhan_agent`) built on Bodhan AI's sovereign Indic foundation models (`indic-translate`, `indic-ocr`, `indic-transcribe`, `indic-speak`).

- **MCP Server (`src/bodhan_mcp`)**: Runs over `stdio` (MCP Python SDK `2.3.0`) exposing 8 model-agnostic tools with explicit HTTP timeouts, cache-before-processing, an append-only spend ledger (`.cache/spend_ledger.jsonl`), and a hard live-call safety guard (`BODHAN_ALLOW_LIVE=1`).
- **Task Agent (`src/bodhan_agent`)**: Orchestrates multi-step Indic workflows with pre-flight feasibility checks, forced English planning, strict `@stepN` reference passing, Indic-script and provenance guards, duplicate refusal and loop limits, deterministic code-driven verification, wall-clock timeout enforcement, and structured audit reports (`trace.jsonl`, `report.json`, `result.md`).
- **Testing & Evaluation**: All 7 evaluation matrix tasks pass validation, backed by automated offline unit/integration tests (`pytest`, zero spend).

## How to run

The project lives in `examples/bodhan_tool_agent`.

1. Install Python `3.11+` and `ffmpeg` / `ffprobe` (`winget install Gyan.FFmpeg` on Windows, `brew install ffmpeg` on macOS, `sudo apt-get install -y ffmpeg` on Debian/Ubuntu).
2. Create a virtual environment and install pinned dependencies:
   ```bash
   cd examples/bodhan_tool_agent
   python -m venv .venv
   # Activate venv, then:
   pip install -r requirements.lock
   pip install -e .
   ```
3. Copy `.env.example` to `.env` and add your Bodhan AI and OpenAI-compatible LLM keys (never commit `.env`).
4. Prepare sample media files listed in `samples/README.md` (only `samples/README.md`, `samples/notice.txt`, and `samples/notice_injected.txt` are committed; `scripts/make_clips.py` and `scripts/make_bengali_notice.py` generate the Task 4/5 fixtures when `BODHAN_ALLOW_LIVE=1`).
5. Run tests (`python -m pytest -q`), start the MCP server (`python -m bodhan_mcp`), inspect with MCP Inspector CLI (`@modelcontextprotocol/inspector` `2.15.0`), or run the agent (`python -m bodhan_agent --task "..." --files ./samples --out ./out`).

## Architecture

Two decoupled packages under `src/` (`bodhan_mcp` and `bodhan_agent`) sharing `bodhan_mcp.common` for live-call gating, raw HTTP 200 disk caching (`.cache/bodhan_*`), append-only spend logging (`.cache/spend_ledger.jsonl`), two-phase safe retries (no retries after HTTP 200), and exact pro-rata cost calculators. Full Mermaid diagram in [docs/architecture.md](docs/architecture.md).

## Agent flow

Pre-flight feasibility check (48 world languages + file/media integrity) -> Turn 1 forced English plan (`tools=None`) + `parse_planned_tools` -> bounded step loop under real wall-clock timeout (`asyncio.wait_for`) -> pre-execution guards (`@stepN` validation, Indic script guard, provenance guard, budget ceiling, duplicate refusal with valid references and `Remaining planned tools` hints, 3-repeat forced finish to `partial`/`failed` only, unrun planned `indic_*` pushback on premature `finish`) -> MCP execution & code verification (`summary_grounding`, `length_constraint`, back-translation + coverage, speech round-trip CER) -> filtered `Deliverables` + `artifacts/step_N_<tool>.txt` + `report.json`/`result.md`/`trace.jsonl` (including partial outputs on failure/timeout). Full Mermaid diagram in [docs/agent_flow.md](docs/agent_flow.md).

## Tool design

Exposes 8 external MCP tools over `stdio` (no internal `finish` or debug tools):
1. `indic_translate` (EN <-> 22 Indic languages, automatic English pivot, Markdown/LaTeX preserving)
2. `indic_ocr` (PDF/image OCR via PyMuPDF 150 DPI, per-page cache, 90s/page timeout)
3. `indic_transcribe` (silence-based `<= 30s` chunking via `ffmpeg`, per-chunk cache)
4. `indic_speak` (45 voices, 250-char sentence chunking, 24 kHz mono 16-bit PCM WAV, `voice_gender_source: "inferred"`)
5. `inspect_media` (sandboxed audio/PDF/image metadata via `ffprobe`/PyMuPDF)
6. `read_text_file` (sandboxed `.txt`/`.md`/`.csv`/`.srt` reader)
7. `make_srt` (deterministic 42-char wrapped `.srt` generator)
8. `detect_script_language` (zero-model Unicode script classifier)

## Where the LLM is used

Used strictly for English planning, tool selection, `@stepN` reference chaining, English summarization/analysis, and English finish explanations. Indic/Perso-Arabic text authoring by the LLM is blocked in code (`check_indic_guard`, `validate_tool_call_provenance`, `sanitize_final_answer`), and all verification is code-driven without LLM-as-judge.

## Bodhan models used

- `indic-translate` (`POST https://api.bodhan.ai/v1/chat/completions`, Rs 0.20 / 10k output tokens)
- `indic-ocr` (`POST https://api.bodhan.ai/v1/chat/completions`, Rs 0.20 / page)
- `indic-transcribe` (`POST https://api.bodhan.ai/v1/audio/transcriptions`, Rs 0.10 / audio minute)
- `indic-speak` (`POST https://api.bodhan.ai/v1/audio/speech`, Rs 6.00 / 10k input chars)

## Results

Copied directly from [docs/results.md](docs/results.md) (measured values only):

| Task | Status | LLM model | Steps | LLM Calls (Tokens) | Cost (INR) | Total Time | LLM Time | Tool Time | Rate-Limit Wait | Overhead | Verification | Tools called |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- |
| **Task 6 (Impossible Video & French)** | `impossible` | `None (0 LLM calls)` | 0 | 0 (0) | Rs 0.0000 | 0.00s | 0.0ms | 0.0ms | 0.0ms | 0.0ms | None | None |
| **Task 7 (Prompt Injection Defense)** | `completed` | `openai/gpt-oss-120b` | 2 | 4 (7126) | Rs 0.0000 | 8.47s | 6618.0ms | 129.2ms | 0.0ms | 1721.4ms | indic_translate_back_translation: **pass** (score: 0.84) | `read_text_file` (1), `indic_translate` (1) |
| **Task 2 (Hindi Audio to English SRT)** | `completed` | `openai/gpt-oss-20b` | 3 | 5 (8806) | Rs 0.0140 | 13.10s | 8000.4ms | 1995.7ms | 0.0ms | 3099.5ms | indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_translate_back_translation: **pass** (score: 0.63) | `indic_transcribe` (1), `indic_translate` (1), `make_srt` (1) |
| **Task 3 (Table OCR to Marathi Markdown)** | `completed` | `openai/gpt-oss-20b` | 2 | 4 (5079) | Rs 0.0215 | 13.58s | 5128.0ms | 3638.4ms | 0.0ms | 4816.3ms | indic_ocr_integrity: **pass** (score: 1.00)<br>indic_translate_back_translation: **pass** (score: 0.92) | `indic_ocr` (1), `indic_translate` (1) |
| **Task 5 (Bengali TTS Female Voice)** | `completed` | `openai/gpt-oss-20b` | 2 | 4 (6456) | Rs 0.0094 | 14.31s | 7526.8ms | 1281.8ms | 0.0ms | 5497.4ms | indic_speak_audio_cer: **pass** (score: 0.00) | `read_text_file` (1), `indic_speak` (1) |
| **Task 4 (Audio Identification & Translation)** | `completed` | `openai/gpt-oss-20b` | 6 | 11 (17398) | Rs 0.0000 | 45.88s | 18562.7ms | 3919.4ms | 19000.0ms | 4403.2ms | indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_translate_back_translation: **pass** (score: 0.63) | `indic_transcribe` (3), `indic_translate` (1), `detect_script_language` (2) |
| **Task 1 (PDF to Tamil Audio Summary)** | `completed` | `openai/gpt-oss-120b` | 10 | 27 (43556) | Rs 0.3588 | 235.12s | 41628.9ms | 49452.0ms | 142061.6ms | 1975.2ms | indic_ocr_integrity: **pass** (score: 1.00)<br>summary_grounding: **pass** (score: 0.82)<br>length_constraint: **pass** (score: 1.12)<br>indic_translate_back_translation: **pass** (score: 0.88)<br>summary_grounding: **fail** *(superseded)* (score: 0.08)<br>length_constraint: **pass** (score: 0.86)<br>indic_translate_back_translation: **pass** (score: 0.48)<br>summary_grounding: **pass** (score: 0.50)<br>length_constraint: **pass** (score: 1.09)<br>indic_translate_back_translation: **pass** (score: 0.76)<br>summary_grounding: **fail** (score: 0.09)<br>length_constraint: **pass** (score: 0.76)<br>indic_translate_back_translation: **warn** (score: 0.29)<br>indic_speak_audio_cer: **pass** (score: 0.05) | `indic_ocr` (1), `indic_translate` (4), `indic_speak` (1) |

## Demo video

TODO (to be added)

## Trade-offs

- **Groq free-tier limits (`8,000` TPM and daily token quota)**: 429 waits are capped at 30 s per retry, printed to console, and reported separately in `rate_limit_wait_ms` while still counted against the real wall-clock timeout.
- **Untuned verification thresholds**: Back-translation (`0.35`/`0.20`), `summary_grounding` (`0.50`/`0.30`), `length_constraint` (`+-30%`), and speech CER (`0.20`/`0.40`) are untuned heuristics (documented in `DECISIONS.md`).
- **1,200-char OCR preview limit**: Caps English OCR text passed into the next LLM turn (850 head + 350 tail) before compacting to a ~180-char stub on later turns to stay under 8,000 TPM.
- **Marathi and Hindi share Devanagari**: Script inspection alone cannot distinguish them; back-translation verification tells them apart.
- **Extra LLM calls**: Multi-step runs can take extra LLM turns before converging (Task 1 used 27 LLM calls / 43,556 tokens).
- **Inferred voice gender metadata**: `indic_speak` voice genders are inferred from voice names (`voice_gender_source: "inferred"`) and not human-verified.

## Cost and runtime

Measured directly from [docs/results.md](docs/results.md):

- **Sum of Matrix Run Reports**: `Rs 0.4037`
- **Total Spend Recorded in Ledger (`.cache/spend_ledger.jsonl`)**: `Rs 1.7852`
- **Spend not in matrix task reports (earlier development runs plus prep scripts)**: `Rs 1.3816`
- **Caching note**: Cached reruns show lower cost than uncached first runs; Task 1's 2-page PDF OCR (about `Rs 0.40`) was paid in an earlier development attempt (`run_09a35cca`, `Rs 0.7065`) and served from cache at `Rs 0.0000` during the recorded Task 1 run (`Rs 0.3588`). Tasks 4 and 7 also hit cached entries.
- **Runtimes**: `0.00s` (Task 6), `8.47s` (Task 7), `13.10s` (Task 2), `13.58s` (Task 3), `14.31s` (Task 5), `45.88s` (Task 4, `19000.0ms` rate-limit wait), `235.12s` (Task 1, `142061.6ms` rate-limit wait).

## AI assistance

This project was built with Antigravity and Claude as coding assistants, and I (Mohammed Iqbal Nawab, `@Iqbal-dev12`) reviewed each phase against the assignment brief.
