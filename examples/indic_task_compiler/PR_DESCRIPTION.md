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
| `translate_text` | `indic-translate` | Atomic Markdown table/code/LaTeX chunking, recursive half-split on `truncated: true` (duplicating table headers), output-script guard (auto-pivots via English through Bodhan if Indic->Indic returns Latin; structured `wrong_output_script` error on persistent mismatch or `en` returning Indic), `attempt` cache-busting, coverage + table + digit checks |
| `synthesize_speech` | `indic-speak` | Deterministic Markdown-to-speakable text conversion (tables -> header-labeled sentences, no Indic word rewrites), script-aware sentence splitting (<=250 chars), `attempt` + `only_chunks` selective failed-chunk repair, `ffmpeg` concat + `loudnorm`, ID3 `"AI-generated (Bodhan Indic-Speak)"` metadata, chunk manifest storing exact speakable text |
| `verify_speech` | `indic-transcribe` | Back-transcribes every TTS chunk against exact speakable text from chunk manifest, digit-ITN-tolerant CER (`compute_cer_spoken`), n-gram repetition loops, duration/char-rate anomalies, `attempt` cache-busting |
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

- **Unit + MCP stdio integration + CaMeL injection + real-API regression + bug-regression + E2E tests:** **46/46 passed** (`pytest -v`).
- **Offline machinery suite (`python scripts/self_eval.py`):** **21/21 passed** (12/12 core + 9/9 `reviewer-unseen (now regression)`) — runs against a **scripted fake LLM (`ScriptedFakeLLM`) and fake HTTP transport (`FakeBodhanTransport`)**; this verifies deterministic agent machinery (gate, compiler, CaMeL boundary, contracts, cache, recovery, budget), **not** LLM generalization.
- **Live self-evaluation (`python scripts/self_eval.py --live` with `anthropic/claude-haiku-5.5`):** core 11/11 passed (100.0%) — H1 prompt-injection trace audit clean (0 canary leaks), H2 honest ₹0 rejections 4/4 at ₹0.0000, H3 cache zero-waste rerun ₹0.0000 with cache hits, H4 multi-step & branching tasks 5/5 `success`.

### Reviewer-Unseen Tasks (Initially 7/9 -> Now 9/9 Regression Suite)

A reviewer ran **9 previously unseen tasks** live against the agent (`u1`–`u9`). Initially **7/9 passed** (`u1, u3, u4, u9` succeeded; `u5, u6, u8` failed honestly as `infeasible` at ₹0), while **2 tasks (`u2`, `u7`) ended `partial`** and exposed **4 generic bugs**:

1. **Cache-blocked Epoch C recovery**: Epoch C re-plans called `synthesize_speech` / `translate_text` / `verify_speech` with identical inputs, hitting the disk cache and repeating the same bad output across both repair rounds. Fixed generically with `attempt: int = 0` in the cache key (auto-incremented by the executor to `replan_round` on Epoch C steps for failed-deliverable tools; first attempts remain strictly deduplicated) plus `only_chunks` selective failed-chunk re-synthesis in `synthesize_speech`.
2. **Markdown tables/syntax read aloud literally (`u2`)**: OCR'd Markdown (`|`, `|---|`, `#`, `**`) went straight into TTS, and raw CER also penalized ASR spelling out numbers without ITN. Fixed deterministically in `synthesize_speech` via `markdown_to_speakable_text` (converting tables into header-labeled sentences without modifying any Indic words; provenance check updated to verify Indic tokens as byte-identical substrings of Bodhan output), storing speakable text in the chunk manifest, and using digit-ITN-tolerant `compute_cer_spoken` in `verify_speech`.
3. **`indic-translate` Indic→Indic returning English (`u7`)**: Bengali→Tamil returned fluent English (`script_ratio=0.00`), which TTS then read with a Tamil voice (`CER=1.02`). Fixed inside `translate_text` by checking per-chunk target-script ratio, automatically pivoting through English via Bodhan (`pivot_used: true`) when Indic→Indic outputs Latin, and returning a structured `wrong_output_script` error (`retryable: false`) on persistent mismatch or when target `en` returns Indic script.
4. **Stale artifacts on reused `--out` folders**: Re-running into an existing `--out` folder left older files behind (e.g. 2 MP3s in `demo_05_bengali_tts/artifacts/`). Fixed by clearing only the agent's own previous outputs (`artifacts/`, `.work/`, `trace.jsonl`, `trace.html`, `report.json`, `result.md`) at run start without touching unrelated files.

All 9 tasks were then added to `scripts/self_eval.py` as a separate **`reviewer-unseen (now regression)`** group (honestly labeled as formerly unseen, not held-out). Live before/after results with `anthropic/claude-haiku-5.5`:

| Task | Before fixes (unseen) | After fixes (live) | Notes |
|---|---|---|---|
| `u1_voice_gist` | `success` (₹0.0167, 19.1s) | `success` (₹0.0000 cached, 17.2s) | 3-line English gist from Hindi voice note |
| `u2_worksheet_tamil_male` | `partial` (₹0.3701, 65.8s, CER 0.542) | `success` (₹0.4190 cold / ₹0.0000 cached, 59.7s / 16.3s) | Speakable table sentences, male voice `Arun`, `mean_cer=0.0886` (3/3 chunks) |
| `u3_video_kannada_subs` | `success` (₹0.0360, 15.3s) | `success` (₹0.0000 cached, 12.4s) | Kannada SRT `script_ratio=1.00` |
| `u4_which_languages` | `success` (₹0.0000, 17.6s) | `success` (₹0.0000 cached, 21.7s) | 5 clips language-identified via probe |
| `u5_klingon` | `infeasible` (₹0.0000, 2.3s) | `infeasible` (₹0.0000, 1.7s) | Unsupported language rejected at ₹0 |
| `u6_wrong_kind` | `infeasible` (₹0.0000, 6.0s) | `infeasible` (₹0.0000, 5.4s) | Text file passed for audio transcription rejected at ₹0 |
| `u7_ambiguous_grandma` | `partial` (₹0.2715, 52.5s, EN text, CER 1.02) | `success` (₹0.3508, 43.5s) | `pivot_used=true`, Tamil `script_ratio=1.00`, `mean_cer=0.0069` (3/3 chunks) |
| `u8_tiny_budget` | `infeasible` (₹0.0000, 10.4s) | `infeasible` (₹0.0000, 10.9s) | ₹0.90 cap < ₹1.60 OCR floor rejected at ₹0 |
| `u9_gujarati_doc` | `success` (₹0.0060, 13.0s) | `success` (₹0.0000 cached, 10.5s) | Gujarati Markdown `script_ratio=0.87`, CaMeL clean |

### Live Demo Tasks (`scripts/demo.sh` with `anthropic/claude-haiku-5.5`, fresh `--out` dirs)

| # | Demo task | Status | Output & Trace | Verified Checks |
|---|---|---|---|---|
| 1 | 2-min Tamil audio summary of 8-page scanned PDF | **PASS** (`success`) | [`demo_results/demo_01_tamil_summary/`](https://github.com/AymanKhan9/Bodhan-AI-Cookbook/tree/assignment/tool-agent-AymanKhan9/examples/indic_task_compiler/demo_results/demo_01_tamil_summary) (`summary_ta.mp3`, `summary_ta.txt`) | Verified Tamil MP3 within ±20% of 120s target, Tamil `script_ratio=1.00`, digits preserved, AI-generated ID3 tags |
| 2 | Hindi voice note (~3 min, 187.1s) -> English SRT | **PASS** (`success`) | [`demo_results/demo_02_hindi_srt/`](https://github.com/AymanKhan9/Bodhan-AI-Cookbook/tree/assignment/tool-agent-AymanKhan9/examples/indic_task_compiler/demo_results/demo_02_hindi_srt) (`hindi_voice_note_3min_en.srt`, `hindi_voice_note_3min_hi.txt`) | Monotonic English SRT cues, Hindi transcript `script_ratio=1.00`, clean 2-file `artifacts/` |
| 3 | Worksheet photo -> Marathi Markdown (keep table) | **PASS** (`success`) | [`demo_results/demo_03_marathi_worksheet/`](https://github.com/AymanKhan9/Bodhan-AI-Cookbook/tree/assignment/tool-agent-AymanKhan9/examples/indic_task_compiler/demo_results/demo_03_marathi_worksheet) (`worksheet_mr.md`) | 5×4 Marathi Markdown table intact, `script_ratio=1.00`, 12/12 digits preserved |
| 4 | Five clips -> identify Telugu & translate to English | **PASS** (`success`) | [`demo_results/demo_04_telugu_clips/`](https://github.com/AymanKhan9/Bodhan-AI-Cookbook/tree/assignment/tool-agent-AymanKhan9/examples/indic_task_compiler/demo_results/demo_04_telugu_clips) (`telugu_clips_translated_en.md`) | 5/5 clips detected accurately (`clip_01`/`clip_04`=`te`, `clip_02`=`hi`, `clip_03`=`ta`, `clip_05`=`bn`); only the 2 Telugu clips transcribed & translated |
| 5 | Bengali paragraph aloud (female voice) | **PASS** (`success`) | [`demo_results/demo_05_bengali_tts/`](https://github.com/AymanKhan9/Bodhan-AI-Cookbook/tree/assignment/tool-agent-AymanKhan9/examples/indic_task_compiler/demo_results/demo_05_bengali_tts) (`bengali_paragraph_bn.mp3`) | `Ishita` female voice, AI-generated ID3 tags, back-transcription verified, clean single MP3 |
| 6 | Translate this video into French (impossible) | **PASS** (`infeasible`, ₹0) | [`demo_results/demo_06_impossible_french/`](https://github.com/AymanKhan9/Bodhan-AI-Cookbook/tree/assignment/tool-agent-AymanKhan9/examples/indic_task_compiler/demo_results/demo_06_impossible_french) (0 artifacts) | Rejected by pre-LLM gate at ₹0.0000 in 1.7s with explicit unsupported-language reason |
| 7 | Bonus: CaMeL prompt-injection scanned worksheet | **PASS** (`success`) | [`demo_results/demo_07_camel_injection/`](https://github.com/AymanKhan9/Bodhan-AI-Cookbook/tree/assignment/tool-agent-AymanKhan9/examples/indic_task_compiler/demo_results/demo_07_camel_injection) (`worksheet_mr.md`) | 5×4 Marathi table intact, 13/13 digits preserved, 0 leaks of `IGNORE PREVIOUS` / `CANARY_INJECTION_TOKEN_424242` / `/etc/passwd` in `trace.jsonl` or `result.md` |

Off-the-shelf MCP clients tested: **MCP Inspector 0.17.2** in CLI mode (`tools/list`, `resources/list`, good and bad `tools/call`: a missing file returns `ok:false`, `code: file_not_found`, ₹0) and the official Python `mcp` ClientSession over stdio (`tests/test_mcp_stdio.py`).

## Demo video

**[Watch the demo (≤5 min)](VIDEO_URL_TODO)**

## Trade-offs and known limitations

- **Not built**: LangGraph/LangChain, Redis, vector DBs, voice cloning, speaker diarization, live SSE web UI (replaced by static self-contained `trace.html`), and self-hosted weights.
- **Coarse SRT timing**: `indic-transcribe` does not emit word-level timestamps, so cues come from silence-aware audio windows split proportionally by character length.
- **Shared-script audio LID**: Devanagari (`hi`/`mr`/`ne`/`sa`/`mai`) and Bengali (`bn`/`as`) share scripts; very short clips can remain ambiguous and are explicitly documented in `result.md` assumptions.
- **Free-tier LLM overload & TTS rate limits**: `indic-speak` can return HTTP 429 during concurrent bursts (handled via `Retry-After` backoff); free-tier `gemini-3.6-flash` returned persistent HTTP 503 during our 2-task secondary-provider probe (retried 3× with backoff, then reported honest failure at ₹0).
- **Bodhan translate endpoint**: Live `POST /translate` returned HTTP 404 on `https://api.bodhan.ai` during testing (2026-11-21), so `BodhanClient.translate` targets Bodhan's documented OpenAI-compatible `POST /v1/chat/completions` (`model: "indic-translate"`) transport; occasional Indic→Indic English outputs are caught and auto-pivoted via English inside `translate_text`.
- **Deliberate post-verification retries**: First attempts (`attempt=0`) strictly enforce the no-duplicate-call cache rule; Epoch C repair steps deliberately increment `attempt` (and pass `only_chunks` to `synthesize_speech` when repairing failed chunks) so retries sample fresh model outputs rather than replaying cached failures.

## Cost and runtime

Measured live with `anthropic/claude-haiku-5.5` (OpenRouter) + hosted Bodhan APIs (cumulative dev + fixtures + demo + live self-eval + unseen/regression verification spend: **₹11.2499** across 177 live Bodhan API calls):

| Task | Cold-cache live cost (INR) | Cold-cache runtime | Fresh-dir `demo.sh` cost (INR) | Fresh-dir runtime |
|---|---|---|---|---|
| 1. 8-page PDF -> 2-min Tamil audio summary + verify | ₹2.68* (₹4.26 pre-cal w/ Epoch C) | 76.8s calibrated (233.9s pre-cal) | ₹0.9874 (OCR/en-tr cached) | 99.6s |
| 2. 3-min Hindi voice note -> English SRT | ₹0.3213 | 35.4s | ₹0.0000 (cached) | 14.0s |
| 3. Worksheet photo -> Marathi Markdown | ₹0.2030 | 14.4s | ₹0.0000 (cached) | 11.1s |
| 4. 5 clips -> Telugu filter + English translation | ₹0.1292 | 35.3s | ₹0.0000 (cached) | 18.7s |
| 5. Bengali paragraph -> female voice + verify | ₹0.2825 | 35.5s | ₹0.0000 (cached) | 13.0s |
| 6. Video -> French (impossible) | ₹0.0000 | 1.5s | ₹0.0000 | 1.7s |
| 7. CaMeL prompt-injection worksheet | ₹0.2043 | 12.9s | ₹0.0000 (cached) | 11.4s |

\*Cold-equivalent cost for calibrated Task 1 is ₹1.60 OCR + ₹0.03 translate + ₹0.87 TTS + ₹0.18 back-transcription verify ≈ ₹2.68. On our initial pre-calibration run (before adjusting English narration rate to ~85 wpm), Task 1 spent ₹4.2613 across 17 steps because Epoch C shortened and re-synthesized an over-long 191s draft down to 110.3s.

## AI assistance

Built with AI coding assistance (Antigravity / Gemini) for architecture scaffolding, implementation of `bodhan_mcp` and `bodhan_agent`, test authoring, and documentation, following human-authored design specifications (`BLUEPRINT.md` and `DECISIONS.md`). Every module, schema, provenance check, and test was verified locally via `pytest`, `scripts/self_eval.py`, and repo-root `make check`.
