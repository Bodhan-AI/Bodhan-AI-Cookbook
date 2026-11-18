# Indic Tool Server and Task Agent

> An MCP server and autonomous CLI agent that orchestrates Bodhan's four Indic-language AI models to complete multi-step language tasks — transcription, translation, speech synthesis, and OCR — on files and text the agent has never seen before.

**Bodhan models:** `indic-transcribe`, `indic-translate`, `indic-speak`, `indic-ocr`  
**Languages shown:** Hindi, Marathi, Gujarati, Punjabi, Sanskrit (Devanagari), English  
**Format:** CLI app (MCP stdio server + task agent)

---

## What it does

Takes a plain-language task and input files (audio, images, PDFs, or direct text), plans a multi-step execution using Bodhan's Indic models, runs the steps with budget and timeout enforcement, verifies every output, and writes structured results to an output directory.

**Example:**
```bash
python -m bodhan_agent \
  --task "Transcribe this Hindi audio, translate to English, and generate an English subtitle file" \
  --files path/to/audio.wav \
  --out ./out
```
Produces: `out/result.md`, `out/artifacts/` (transcript, translation, `.srt`), `out/trace.jsonl`, `out/report.json`.

The MCP server also runs standalone over stdio so any MCP client (Claude Desktop, Continue.dev, etc.) can use all four Bodhan model tools directly.

---

## Setup

```bash
cd examples/indic_tool_server_and_agent

# 1. Create virtualenv and install
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .

# 2. Configure API keys
cp .env.example .env
# Edit .env — fill in LLM_TRANSCRIBE, LLM_TRANSLATE, LLM_OCR, LLM_AUDIO, GEMINI_API_KEY
```

---

## Run

**Task agent (CLI):**
```bash
python -m bodhan_agent --task "Translate this image text to Hindi and English" \
  --files sample_data/ \
  --out ./out \
  --max-steps 20 \
  --max-cost-inr 10.0
```

**Direct text input (no file):**
```bash
python -m bodhan_agent --task "Translate to Punjabi" \
  --text "Good morning, have a wonderful day." \
  --out ./out
```

**MCP server (stdio — connect any MCP client):**
```bash
python -m bodhan_mcp
```

**Run tests:**
```bash
pytest tests/ -q
```

---

## How it works

The agent follows a plan → act → verify → replan → stop loop:

1. **Interpret** — LLM reads the task, identifies deliverables, languages, and feasibility.
2. **Plan** — LLM produces a typed step list (`ocr_document → translate_text → synthesize_speech`).
3. **Execute** — Each step calls the matching Bodhan model tool. `${step_1.full_text}` references resolve at runtime. Concurrency is capped; each call has a 120 s timeout. Results are cached so the same input is never sent twice.
4. **Verify** — Back-transcription CER on audio, length-ratio and script detection on translations, char-count on OCR. Verifier reads `resolved_args` (actual runtime values), not raw plan templates.
5. **Replan** — On failure, the planner receives the exact error, the available input file paths, and completed step outputs, then proposes replacement steps.
6. **Stop** — Stops when all steps succeed, budget is exhausted, or replan limit (2) is hit. Partial completions are reported honestly.

See [`docs/architecture.md`](docs/architecture.md), [`docs/agent-flow.md`](docs/agent-flow.md), and [`docs/design-notes.md`](docs/design-notes.md).

---

## MCP Tools

| Tool | Bodhan model | Description |
|------|-------------|-------------|
| `transcribe_audio` | `indic-transcribe` | Transcribe audio in any supported Indic language. Auto-chunks to 30 s windows. |
| `translate_text` | `indic-translate` | Translate text or file between Indic languages and English. |
| `synthesize_speech` | `indic-speak` | Generate WAV audio with multi-voice fallback for Indic languages. |
| `ocr_document` | `indic-ocr` | Extract text from images and multi-page PDFs. |
| `build_srt` | `indic-transcribe` | Build subtitle files with optional target-language translation of cues. |
| `inspect_media` | Python (ffprobe) | Inspect audio/video metadata. |
| `detect_script` | Python (Unicode) | Detect script/language of a text string. |
| `split_audio` / `concat_audio` | Python (pydub) | Chunk and join audio files. |

---

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for a full Mermaid diagram.

```
MCP Client / bodhan_agent
        │
        ▼
  bodhan_mcp server (stdio / FastAPI)
        │
   ┌────┴───────────────────────────────┐
   │  tools/                            │
   │  ├── stt.py   → indic-transcribe  │
   │  ├── translate.py → indic-translate│
   │  ├── tts.py   → indic-speak       │
   │  └── ocr.py   → indic-ocr         │
   │                                    │
   │  core/ (chunking, retry, cache,   │
   │          cost, errors, filestore) │
   └────────────────────────────────────┘
```

---

## Demo Tasks & Results

| Task | Input | Status | Cost | Time |
|------|-------|--------|------|------|
| OCR Hindi textbook image → English + Gujarati | `hindi_lang.png` | ✅ Completed | ₹0.22 | 25s |
| OCR Marathi image → Hindi + English + Hindi audio | `marathi_script.png` | ✅ Completed | ₹0.31 | 42s |
| OCR Marathi image → Gujarati | `marathi_script.png` | ✅ Completed | ₹0.00 | 9s |
| Translate text → Punjabi + Hindi (direct `--text`) | `--text "Good morning…"` | ✅ Partial (Rajasthani unsupported) | ₹0.01 | 8s |
| Transcribe Hindi audio → Punjabi audio | `New Recording 5.m4a` | ✅ Completed | — | — |
| Impossible task — unsupported language | Rajasthani (not in model) | ✅ Honest failure, no fabrication | ₹0.00 | 1s |

---

## Limitations

- Rajasthani (`raj`) is not supported by any Bodhan model; tasks requiring it fail gracefully with a clear explanation.
- CER verification for Devanagari can appear inflated due to sandhi and word-boundary differences — the audio is correct but the metric is noisy.
- Large images (> 400 KB) may approach the 120 s OCR timeout on slow connections.
- The agent is non-interactive; ambiguous tasks get a reasonable assumption logged in `result.md`.
- MCP server runs over stdio; the optional FastAPI streamable-HTTP transport is documented in `docs/design-notes.md`.

---

## Cost and Runtime

| Step type | Typical cost | Typical latency |
|-----------|-------------|-----------------|
| OCR (1 page image) | ₹0.20 | 1–2 s |
| Translation (500 chars) | ₹0.002 | 0.5–1 s |
| Speech synthesis (500 chars) | ₹0.10 | 5–30 s |
| Full 3-step task | ₹0.20–0.35 | 25–60 s |

Caching is enforced — identical inputs never hit the API twice in a run.

---

## AI Assistance

This recipe was built with Antigravity (Google DeepMind) for project scaffolding, refactoring, test generation, verifier bug fixes, and documentation.
