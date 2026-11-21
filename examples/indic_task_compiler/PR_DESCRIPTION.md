## Summary

`examples/indic_task_compiler/` ships a standalone MCP tool server (`bodhan_mcp`) wrapping Bodhan's four Indic models (`indic-transcribe`, `indic-translate`, `indic-speak`, `indic-ocr`) alongside an unattended CLI task agent (`bodhan_agent`). Its core architectural differentiator is a **CaMeL-style privileged/quarantined LLM split** (so untrusted file, OCR, transcript, and translation content never enters the planner's context) combined with a **deterministic pre-spend feasibility gate and plan compiler** that type-checks plans, enforces input/output/language scope locks, and rejects provably impossible tasks at ₹0 before calling any paid API.

## How to run

Prerequisites: Python 3.10–3.14 and `ffmpeg`/`ffprobe` on `PATH` (or use the included `Dockerfile`).

```bash
cd examples/indic_task_compiler
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install --no-deps -e .
cp .env.example .env
# Fill in BODHAN_*_KEY (or BODHAN_API_KEY) and LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
```

Run the task agent:

```bash
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out \
  --max-steps 30 \
  --max-cost-inr 20
```

Run the standalone MCP server (stdio default, or optional Streamable HTTP with Bearer auth):

```bash
python -m bodhan_mcp
# Optional Streamable HTTP:
python -m bodhan_mcp --http --host 127.0.0.1 --port 8765 --token "$MCP_AUTH_TOKEN"
```

Run tests and offline self-evaluation:

```bash
pytest -v
python scripts/self_eval.py
```

## Architecture

Full diagram in [`docs/architecture.md`](docs/architecture.md):

```mermaid
flowchart LR
    CLI["python -m bodhan_agent"] --> INTAKE["Intake (free FileFacts)"]
    INTAKE --> PLLM["P-LLM Planner<br/>(metadata & $handles only)"]
    PLLM --> GATE["₹0 Feasibility Gate"]
    GATE --> COMPILER["Plan Compiler<br/>(MCP schemas, scope lock, waves)"]
    COMPILER --> EXEC["Wave Executor + BudgetTracker"]
    EXEC <--> QLLM["Q-LLM Reader<br/>(tool-less, English-only)"]
    EXEC <--> STORE["ArtifactStore +<br/>Indic Provenance Guard"]
    EXEC --> VERIFY["Contracts Verifier"]
    VERIFY --> OUT["result.md / artifacts /<br/>trace.jsonl / report.json / trace.html"]
    EXEC <-->|"MCP stdio"| MCP["python -m bodhan_mcp<br/>(8 tools, 3 resources, 1 prompt)"]
    MCP --> CLIENT["BodhanClient<br/>(retry, semaphore, SHA-256 cache)"]
    CLIENT --> API["https://api.bodhan.ai"]
```

- **`bodhan_mcp/server.py` & `tools.py`**: Standalone MCP server exposing 8 typed tools, 3 resources (`bodhan://languages`, `bodhan://voices`, `bodhan://pricing`), and 1 prompt (`plan_task`).
- **`bodhan_mcp/client.py` & `cache.py`**: Async HTTP client with per-model key lookup (`BODHAN_*_KEY` -> `BODHAN_API_KEY`), exponential backoff + jitter, `Semaphore(4)`, SHA-256 disk cache, and INR cost meter.
- **`bodhan_agent/planner.py` (`P-LLM`)**: Privileged planner that never sees untrusted file/OCR/ASR/translation text — only metadata, probe enums, and `$step.field` handles.
- **`bodhan_agent/quarantined.py` (`Q-LLM`)**: Tool-less reader (`q_summarize_en`, `q_classify`, `q_extract_number`) enforcing English-only prose and strict enum/numeric validation.
- **`bodhan_agent/gate.py` & `compiler.py`**: Code-enforced ₹0 feasibility gate, MCP schema validator, task-scope lock, Indic-literal guard, and DAG-to-parallel-wave compiler.
- **`bodhan_agent/contracts.py` & `store.py`**: Post-condition verifier and byte-level Indic provenance guard.

## Agent flow

Full diagram in [`docs/agent-flow.md`](docs/agent-flow.md):

```mermaid
flowchart TD
    I["Intake (free inspect_files)"] --> PG{"Pre-LLM ₹0 Gate"}
    PG -- "Impossible" --> STOP0["Stop at ₹0 (infeasible)"]
    PG -- "OK" --> EA["Epoch A (P-LLM -> TaskSpec)"]
    EA --> G{"Feasibility Gate (₹0)"}
    G -- "Impossible" --> STOP0
    G -- "OK" --> PR["Probe Epoch (detect_language)"]
    PR --> EB["Epoch B (P-LLM -> flat steps)"]
    EB --> C{"Plan Compiler (+1 repair)"}
    C -- "Pass" --> ACT["Execute Parallel Waves<br/>(pre-call budget reserve)"]
    ACT --> V{"Contracts Verifier"}
    V -- "Pass" --> DONE["Write outputs (success)"]
    V -- "Fail & rounds < 2" --> EC["Epoch C (targeted repair)"] --> C
    V -- "Budget/Retries exhausted" --> PART["Stop cleanly (partial/failed)"]
```

## Tool design

Eight capability-level tools strike the balance between usability for third-party MCP clients and fine-grained control for the agent:

| Tool | Bodhan model | Model-limit handling |
|---|---|---|
| `inspect_files` | none (free) | Magic-byte kind detection, `ffprobe` streams/duration, `pypdfium2` page count, script histogram, cost estimates |
| `detect_language` | `indic-transcribe` (audio probe) / none (text) | Unicode script histogram + Devanagari/Bengali/Arabic stopword disambiguation; <=10 s non-silent probe for audio |
| `transcribe_audio` | `indic-transcribe` | `ffmpeg` -> 16 kHz mono WAV (handles MP4/M4A/WebM), `silencedetect` <=28 s splits, drops pure silence, returns timed segments |
| `translate_text` | `indic-translate` | Atomic Markdown table/code/LaTeX chunking, recursive half-split on `truncated: true` (duplicating table headers), coverage + table + digit checks |
| `synthesize_speech` | `indic-speak` | Script-aware sentence splitting (<=250 chars), `ffmpeg` concat + `loudnorm`, ID3 `"AI-generated (Bodhan Indic-Speak)"` metadata, chunk manifest |
| `verify_speech` | `indic-transcribe` | Back-transcribes every TTS chunk, checks normalized CER, n-gram repetition loops, and duration/char-rate anomalies |
| `ocr_document` | `indic-ocr` | `pypdfium2` PDF->PNG rasterization (no poppler), `table_format=markdown`, 502 cascade (`max_tokens=8192` -> 65% downscale -> vertical split) |
| `write_subtitles` | none (free) | Deterministic SRT/VTT from timed segments with proportional cue splitting and monotonicity validation |

See [`docs/design-notes.md`](docs/design-notes.md) for full tool-design rationale.

## Where the LLM is used, and why

| Decision point | LLM role | What it decides | Deterministic guard / fallback |
|---|---|---|---|
| Epoch A (`TaskSpec`) | `P-LLM` | Extracts deliverables, constraints, assumptions, and required probes from `--task` + `FileFacts` | Pre-LLM ₹0 gate catches obvious unsupported languages/missing files even if LLM is down; language names mapped via table (`bodhan_api_rules.json`); full ₹0 feasibility gate validates `TaskSpec` |
| Epoch B (Step list) | `P-LLM` | Emits flat step list with `$step.field` handles and optional `foreach` | Compiler validates against live MCP JSON Schemas, enforces scope lock & Indic-literal ban, runs 1 repair pass on compile errors, else fails honestly |
| Content reading (`q_summarize_en`, `q_classify`, `q_extract_number`) | `Q-LLM` (no tools) | Summarizes English text or classifies/extracts a number from untrusted content | Input/output Indic-script ban, strict enum/numeric validation; output flows only to artifact handle |
| Epoch C (Targeted repair) | `P-LLM` | Proposes minimal subgraph fix when a deliverable contract fails | Capped at 2 re-plans and remaining budget; sees failure metadata only |

Deterministic Python handles all chunking, SRT formatting, script/stopword LID, table/digit checks, cost accounting, caching, DAG wave inference, and provenance verification.

## Bodhan models used

- **`indic-transcribe`** (hosted `POST /v1/audio/transcriptions`): 16 kHz mono WAV, <=28 s silence-aware windows, <=10 s probe for auto language detection, per-chunk TTS back-transcription.
- **`indic-translate`** (hosted `POST /v1/chat/completions`, `model: "indic-translate"` — used because live `POST /translate` returned HTTP 404 on `api.bodhan.ai`): paragraph/table-aware chunks (<=1800 chars), recursive half-split on `finish_reason == "length"`, cost computed from `usage.completion_tokens`.
- **`indic-speak`** (hosted `POST /v1/audio/speech`): <=250-char script-aware sentence chunks (parallelized under `Semaphore(4)`), `instructions` JSON string with `lang` + optional `style`, loudness-normalized 24 kHz output tagged as AI-generated (handles live `Content-Type: audio/mpeg` header carrying RIFF/WAVE PCM16 bytes).
- **`indic-ocr`** (hosted `POST /v1/chat/completions`, `model: "indic-ocr"`): 175 DPI PNG pages via `pypdfium2`, `table_format="markdown"`, top-level `blocks` confidence summary, 502 recovery cascade + `200 null` response guard.

**Tested LLM endpoints:**
- **Primary live runs (all 7 demo tasks + 11-task `self_eval.py --live`):** `anthropic/claude-haiku-5.5` via OpenRouter (`https://openrouter.ai/api/v1`).
- **Secondary provider check (2 tasks):** `gemini-3.6-flash` via Google Gemini's OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`). On the 5-clip Telugu task, `gemini-3.6-flash` completed Epoch A and all 5 cached probes before hitting persistent free-tier HTTP 503 overload on Epoch B across all 4 attempts (initial + 3 exponential-backoff retries); on the Marathi worksheet task, free-tier HTTP 503 persisted on Epoch A. Both runs failed honestly at ₹0 without fabricating output.

## Results

### Automated Tests & Self-Evaluation Suite (`pytest` + `scripts/self_eval.py`)

- **Unit + MCP stdio integration + CaMeL injection + real-API regression + E2E tests:** **40/40 passed** (`pytest -v`).
- **Offline machinery suite (`python scripts/self_eval.py`):** **12/12 passed** — runs against a **scripted fake LLM (`ScriptedFakeLLM`) and fake HTTP transport (`FakeBodhanTransport`)**; this verifies deterministic agent machinery (gate, compiler, CaMeL boundary, contracts, cache, budget), **not** LLM generalization.
- **Live self-evaluation (`python scripts/self_eval.py --live` with `anthropic/claude-haiku-5.5`):** **11/11 passed (100.0%)** — H1 prompt-injection trace audit clean (0 canary leaks), H2 honest ₹0 rejections 4/4 at ₹0.0000 (French, Japanese, missing file, ₹0.50 budget below ₹1.60 OCR floor), H3 cache zero-waste rerun ₹0.0000 with cache hits, H4 multi-step & branching tasks 5/5 `success`.

### Live Demo Tasks (`scripts/demo.sh` with `anthropic/claude-haiku-5.5`)

| # | Demo task | Status | Output & Trace | Verified Checks |
|---|---|---|---|---|
| 1 | 2-min Tamil audio summary of 8-page scanned PDF | **PASS** (`success`) | `outputs/demo_01_tamil_summary/` (`summary_ta.mp3`, `summary_ta.txt`) | 122.62s MP3 vs 120s target (±20%), `mean_cer=0.1692` across 8/8 chunks, Tamil `script_ratio=1.00`, 20/20 digits preserved, AI-generated ID3 tags |
| 2 | Hindi voice note (~3 min, 187.1s) -> English SRT | **PASS** (`success`) | `outputs/demo_02_hindi_srt/` (`hindi_voice_note_3min_en.srt`, `hindi_voice_note_3min_hi.txt`) | 42 monotonic English SRT cues (`00:00:00,000` -> `00:03:07,051`), Hindi transcript `script_ratio=1.00` |
| 3 | Worksheet photo -> Marathi Markdown (keep table) | **PASS** (`success`) | `outputs/demo_03_marathi_worksheet/` (`worksheet_mr.md`) | 5×4 Marathi Markdown table intact, `script_ratio=1.00`, 12/12 digits preserved |
| 4 | Five clips -> identify Telugu & translate to English | **PASS** (`success`) | `outputs/demo_04_telugu_clips/` (`telugu_clips_translated_en.md`) | 5/5 clips detected accurately (`clip_01`/`clip_04`=`te`, `clip_02`=`hi`, `clip_03`=`ta`, `clip_05`=`bn`); only the 2 Telugu clips transcribed & translated |
| 5 | Bengali paragraph aloud (female voice) | **PASS** (`success`) | `outputs/demo_05_bengali_tts/` (`bengali_paragraph_bn_female.mp3`) | `Ishita` female voice, 32.00s MP3 with AI-generated ID3 tags, back-transcription `mean_cer=0.0` (2/2 chunks) |
| 6 | Translate this video into French (impossible) | **PASS** (`infeasible`, ₹0) | `outputs/demo_06_impossible_french/` (0 artifacts) | Rejected by pre-LLM gate at ₹0.0000 in 1.2s with explicit unsupported-language reason |
| 7 | Bonus: CaMeL prompt-injection scanned worksheet | **PASS** (`success`) | `outputs/demo_07_camel_injection/` (`worksheet_mr.md`) | 5×4 Marathi table intact, 13/13 digits preserved, 0 leaks of `IGNORE PREVIOUS` / `CANARY_INJECTION_TOKEN_424242` / `/etc/passwd` in `trace.jsonl` or `result.md` |

Off-the-shelf MCP client tested: Official Python `mcp` ClientSession over stdio (`tests/test_mcp_stdio.py`); MCP Inspector and Claude Desktop configs documented in `README.md`.

## Demo video

TBD after recording (script prepared in [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md), targeting <= 5 minutes).

## Trade-offs and known limitations

- **Not built**: LangGraph/LangChain, Redis, vector DBs, voice cloning, speaker diarization, live SSE web UI (replaced by static self-contained `trace.html`), and self-hosted weights.
- **Coarse SRT timing**: `indic-transcribe` does not emit word-level timestamps, so cues come from silence-aware audio windows split proportionally by character length.
- **Shared-script audio LID**: Devanagari (`hi`/`mr`/`ne`/`sa`/`mai`) and Bengali (`bn`/`as`) share scripts; very short clips can remain ambiguous and are explicitly documented in `result.md` assumptions.
- **Free-tier LLM overload & TTS rate limits**: `indic-speak` can return HTTP 429 during concurrent bursts (handled via `Retry-After` backoff); free-tier `gemini-3.6-flash` returned persistent HTTP 503 during our 2-task secondary-provider probe (retried 3× with backoff, then reported honest failure at ₹0).
- **Bodhan translate endpoint**: Live `POST /translate` returned HTTP 404 on `https://api.bodhan.ai` during testing (2026-11-21), so `BodhanClient.translate` targets Bodhan's documented OpenAI-compatible `POST /v1/chat/completions` (`model: "indic-translate"`) transport.

## Cost and runtime

Measured live with `anthropic/claude-haiku-5.5` (OpenRouter) + hosted Bodhan APIs (cumulative dev + fixtures + demo + live self-eval spend: **₹9.4927** across 147 live Bodhan API calls):

| Task | Cold-cache live cost (INR) | Cold-cache runtime | Warm-cache `demo.sh` cost (INR) | Warm-cache runtime |
|---|---|---|---|---|
| 1. 8-page PDF -> 2-min Tamil audio summary + verify | ₹2.68* (₹4.26 pre-calibration w/ Epoch C) | 76.8s calibrated (233.9s pre-cal) | ₹1.0585 (OCR/en-tr cached) | 76.8s |
| 2. 3-min Hindi voice note -> English SRT | ₹0.3213 | 35.4s | ₹0.0000 (2 cache hits) | 13.9s |
| 3. Worksheet photo -> Marathi Markdown | ₹0.2030 | 14.4s | ₹0.0000 (2 cache hits) | 10.5s |
| 4. 5 clips -> Telugu filter + English translation | ₹0.1292 | 35.3s | ₹0.0000 (9 cache hits) | 16.9s |
| 5. Bengali paragraph -> female voice + verify | ₹0.2825 | 35.5s | ₹0.0000 (2 cache hits) | 18.6s |
| 6. Video -> French (impossible) | ₹0.0000 | 1.5s | ₹0.0000 | 1.2s |
| 7. CaMeL prompt-injection worksheet | ₹0.2043 | 12.9s | ₹0.0000 (2 cache hits) | 10.5s |

\*Cold-equivalent cost for calibrated Task 1 is ₹1.60 OCR + ₹0.03 translate + ₹0.87 TTS + ₹0.18 back-transcription verify ≈ ₹2.68. On our initial pre-calibration run (before adjusting English narration rate to ~85 wpm), Task 1 spent ₹4.2613 across 17 steps because Epoch C shortened and re-synthesized an over-long 191s draft down to 110.3s.

## AI assistance

Built with AI coding assistance (Antigravity / Gemini) for architecture scaffolding, implementation of `bodhan_mcp` and `bodhan_agent`, test authoring, and documentation, following human-authored design specifications (`BLUEPRINT.md` and `DECISIONS.md`). Every module, schema, provenance check, and test was verified locally via `pytest`, `scripts/self_eval.py`, and repo-root `make check`.
