## Summary
An MCP server (`bodhan_mcp`) that exposes Bodhan's Indic-Transcribe, Indic-Translate, Indic-Speak and Indic-OCR as ten typed, capability-level tools that absorb each model's limits, plus a command-line task agent (`bodhan_agent`) that inspects its inputs, plans with an OpenAI-compatible LLM, calls the tools, verifies every audio file and translation before finishing, and stays within step and rupee budgets. Every run writes `result.md`, `artifacts/`, a replayable `trace.jsonl` and `report.json`; impossible tasks end with an explanation and no output.

## How to run
```bash
cd examples/bodhan_tool_agent
make setup                    # venv + pinned deps + editable install (needs ffmpeg on PATH, else the bundled imageio-ffmpeg binary is used)
cp .env.example .env          # Bodhan keys + LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
make test                     # tests against a fake Bodhan backend, no keys needed
make sample-data              # builds demo inputs locally (~INR 2.5 once, cached)

python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" \
  --files sample_data/chapter_scan/ --out ./out --max-steps 30 --max-cost-inr 20

python -m bodhan_mcp                                   # MCP server over stdio
npx @modelcontextprotocol/inspector python -m bodhan_mcp
python -m bodhan_mcp --http --port 8765                # streamable HTTP, bearer token from BODHAN_MCP_TOKEN
```
Docker: `docker build -t bodhan-tool-agent . && docker run --rm --env-file .env -v "$PWD/sample_data:/data" -v "$PWD/out:/out" bodhan-tool-agent --task "..." --files /data/... --out /out`.

## Architecture
Diagram: [docs/architecture.md](docs/architecture.md)

- **`bodhan_mcp.server`**: `MCPServer` with ten tools, resources `bodhan://languages` and `bodhan://voices`, and prompts; stdio by default, streamable HTTP with bearer auth as an option.
- **`bodhan_mcp.tools`**: the tool implementations; each handles chunking/windowing/paging for its model and returns paths, previews, cost and warnings.
- **`bodhan_mcp.client`**: the only code that calls Bodhan; retries with backoff, per-model concurrency limit, budget pre-check, cost meter.
- **`bodhan_mcp.cache`**: content-addressed disk cache with in-flight dedupe.
- **`bodhan_mcp.media` / `textproc` / `languages`**: ffmpeg + VAD, PDF rendering, Markdown block parsing, sentence chunking, CER, repetition and injection detection, script-based language ID.
- **`bodhan_agent`**: CLI, inventory, planner LLM, executor (parallel tool calls), guards, verification gate, trace and report writers. Talks to the server over MCP (in-process by default, `--transport stdio` optional).
- **`bodhan_agent.web`**: a thin local web UI (stretch goal) that starts a task and streams its plan, tool calls, checks and artifacts live from `trace.jsonl`, and replays finished runs.

## Agent flow
Flow chart: [docs/agent_flow.md](docs/agent_flow.md)

Inventory (code) → plan (`submit_plan`, LLM) → act (tool calls, guarded, budget-checked) → verify (code gate before `finish`) → repair once or stop. Stops cleanly on `--max-steps`, `--max-cost-inr`, impossibility or repeated LLM failure, always writing a report.

## Tool design
| Tool | Handles |
| --- | --- |
| `inspect_file` | free; kind, duration, pages, detected language, cost estimates; directories |
| `read_text` | free; sliced reads of long text, untrusted flag, injection warnings |
| `transcribe_audio` | any length/format; VAD windows ≤ 28 s at pauses; silence dropped; `language="auto"` detects from the first window |
| `translate_text` | Markdown-aware chunking within the context window; `truncated` → split and retry; tables/code/math preserved; transcript segments keep timestamps |
| `synthesize_speech` | sentence chunks ≤ ~220 chars; pauses; MP3 labelled AI-generated; back-transcription check; one re-split repair per bad chunk |
| `ocr_document` | PDF rendering, image normalisation, one page per call in parallel; 502 → higher `max_tokens`; headings rebuilt from block labels; furniture dropped |
| `verify_speech` | back-transcription CER, repetition, omission |
| `verify_translation` | script, numbers, structure, length ratio, untranslated spans; optional back-translation |
| `make_subtitles` | SRT/VTT with readable cue lengths |
| `list_languages_and_voices` | support matrix, voices, styles |

Why this granularity: one-tool-per-endpoint pushes chunking, truncation and retries onto every caller (and a third-party client would get them wrong); mega-tools like `audio_summary(pdf, lang)` hide the steps the planner needs to inspect, verify and stop early. Capability-level tools that accept any input size, plus free helpers for inspection and verification, keep multi-step tasks to a few calls while leaving decisions with the planner. Details: [docs/design_notes.md](docs/design_notes.md).

## Where the LLM is used, and why
| Decision point | What it decides | Fallback |
| --- | --- | --- |
| Plan (`submit_plan`) | interpretation, target languages, assumptions for ambiguous wording, feasibility | schema-validated; one retry with the error, then `failed` with the reason |
| Tool selection and arguments | next tool, file, language | guards + schema validation return structured errors; step budget bounds loops |
| English writing | summaries and explanations, English only | `write_text_artifact` rejects Indic script; Indic text must come from Indic-Translate |
| Judging results | whether outputs answer the task; whether to repair | verification gate is in code and cannot be skipped |
| Impossible tasks | the explanation | tool error codes (`unsupported_language`, `file_not_found`) carry the reason into `result.md` |

Everything deterministic (inventory, language ID by script, chunking, retries, caching, budget arithmetic, verification checks, subtitle timing) is plain code.

## Bodhan models used
| Model | Hosted / self-hosted | Settings |
| --- | --- | --- |
| indic-transcribe | hosted | `language` passed whenever known or detected; windows ≤ 28 s |
| indic-translate | hosted, `POST /v1/chat/completions` (the documented `POST /translate` route returned 404 on 2026-11-16) | `script="native"` by default; markdown-aware chunks, `finish_reason: length` treated as truncated |
| indic-speak | hosted | default voice per language and gender from the voice table; style optional; chunks ≤ ~220 chars |
| indic-ocr | hosted | `table_format="markdown"` by default; `max_tokens` raised on 502 |

## Results
Self-eval (`python scripts/self_eval.py`, 12 tasks in `tasks/demo_tasks.json`): **12/12 pass**, total Bodhan cost **₹3.74**. Each run's `result.md`, `report.json`, `trace.jsonl` and text artifacts are in `demo_runs/`; audio is kept out of git. The scorer checks status, artifact kinds, output script, tables, budget, verification and latency, not translation quality.

| Task | Status | Pass | Output / trace |
| --- | --- | --- | --- |
| Tamil audio summary of an 8-page scanned chapter | completed (audio 1:51) | ✅ | `demo_runs/tamil_audio_summary/` |
| Hindi voice note (3 min) → English SRT | completed | ✅ | `demo_runs/hindi_note_english_srt/` |
| Worksheet photo → Marathi Markdown, table kept | completed | ✅ | `demo_runs/worksheet_marathi_table/` |
| Five clips: which are Telugu, translate them | completed | ✅ | `demo_runs/telugu_clips_to_english/` |
| Paragraph aloud in Bengali, female voice | completed | ✅ | `demo_runs/bengali_read_aloud/` |
| Video → French (impossible) | impossible | ✅ | `demo_runs/video_to_french_impossible/` |
| Unstated-language clip → English | completed (detected Kannada) | ✅ | `demo_runs/unstated_language_clip/` |
| Prompt injection in a worksheet image | completed, injection reported | ✅ | `demo_runs/injection_worksheet/` |
| Prompt injection in a text notice | completed, injection reported | ✅ | `demo_runs/injection_notice_text/` |
| Missing file | impossible | ✅ | `demo_runs/missing_file/` |
| Budget too small (`--max-cost-inr 0.05`) | partial: stopped before over-budget speech | ✅ | `demo_runs/budget_too_small/` |
| Ambiguous wording ("for my grandmother in Chennai") | completed (assumed Tamil audio) | ✅ | `demo_runs/ambiguous_grandmother/` |

LLM tested with: `gemini-3.5-flash-lite` via Google's OpenAI-compatible endpoint (`gemini-3.8-flash` and Pro timed out or were quota-limited on the test key during the runs).

Second planner check: the suite was also run with `openai/gpt-oss-120b` on Groq's free tier. Every task whose run fit that tier's quota completed, 5 of 5 (Hindi SRT, Marathi table, Bengali read-aloud, unstated-language clip, and the French request refused as impossible); the slower runs exceeded the suite's latency targets only because of rate-limit waits. The other three hit the free tier's limits (a single request over 8,000 tokens per minute, then the daily token cap) and ended as honest `partial` results listing what was done. The remaining four tasks were not reached before the run was stopped. That run exposed a real bug, now fixed with tests: the LLM client ignored the provider's `Retry-After` and gave up on 429s too early.

## Demo video
TBD (link, ≤ 5 minutes).

## Trade-offs and known limitations
- Capability-level tools mean the server makes chunking decisions the caller cannot override beyond the exposed parameters.
- Audio language detection relies on the script of the first window's transcript; short or code-mixed clips and shared scripts (Devanagari, Bengali) can be misidentified. The guess and confidence are reported.
- Translation verification is structural; it does not catch subtle meaning errors. Back-translation is optional.
- Speech verification uses Indic-Transcribe as the judge; CER thresholds are not tuned per language.
- Summary duration is targeted from an estimated chars-per-second rate (the Tamil demo landed at 1:51 for a 2:00 request).
- No resumable runs; reruns are cheap because of the cache.
- Not done: web UI, self-hosted weights.
- The hosted API differed from its reference: `/translate` returned 404 (translation goes through `/v1/chat/completions`), Indic-Speak returns MP3, and Indic-Speak/Indic-Transcribe throttle at four parallel requests without Retry-After (defaults lowered to 2, longer 429 backoff).
- Found and fixed during the demo runs: Gemini's `thought_signature` must be echoed back on tool calls; the language detector split Devanagari words at vowel signs (Marathi read as Hindi).

## Cost and runtime
| Task | Cost (₹) | Time (s) | Bodhan work |
| --- | --- | --- | --- |
| Tamil audio summary of an 8-page scanned chapter | 2.600 | 94 | OCR, translate x2, speak, verify |
| Hindi voice note (3 min) → English SRT | 0.044 | 65 | transcribe (4 retries after 429), translate, subtitles |
| Worksheet photo → Marathi Markdown, table kept | 0.003* | 12 | OCR (cached), translate, verify |
| Five clips: which are Telugu, translate them | 0.041 | 21 | transcribe x5, translate x2 |
| Paragraph aloud in Bengali, female voice | 0.372 | 42 | translate, speak (+ back-transcription) |
| Video → French (impossible) | 0.000 | 4 | none |
| Unstated-language clip → English | 0.012 | 23 | transcribe, translate, verify |
| Prompt injection in a worksheet image | 0.203 | 30 | OCR, translate, verify |
| Prompt injection in a text notice | 0.003 | 14 | translate, verify |
| Missing file | 0.000 | 4 | none |
| Budget too small (`--max-cost-inr 0.05`) | 0.000 | 8 | translate, verify |
| Ambiguous wording ("for my grandmother in Chennai") | 0.465 | 37 | translate, speak |

\* OCR for the worksheet was a cache hit from an earlier smoke test; a cold run adds ₹0.20 per page. LLM cost is not included (Gemini free tier).

## AI assistance

I built this with Claude Code (Anthropic) as the implementation tool. Claude Code wrote the code, tests and docs, ran the live API checks, and fixed the bugs those checks turned up. My part:

- **Scope and direction:** I picked this option out of the four, set the constraints (a hard budget on Bodhan credits, the planner LLM provider and model, keys per model), and decided when each stage was good enough to commit.
- **Review and understanding:** I read through the code and the design, and worked through the architecture until I could explain each decision and the alternatives it beat: capability-level tools, file-path data flow, the plan-first loop with the verification gate in code, budget reservations, back-transcription checks on speech, and the guards on untrusted text.
- **Demo:** I ran the demo suite.

I can walk through any part of the code, why it is built the way it is, and how I would extend it.

No assistant is used at runtime: every LLM call in the shipped agent goes to the endpoint set by `LLM_*`.
