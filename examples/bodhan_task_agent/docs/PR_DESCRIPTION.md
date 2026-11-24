<!-- PR title: [Agentic Eng Assignment] Tool server and agent — Ayush Maurya
     Branch:   assignment/tool-agent-6sLOGAN78
-->

## Summary
`examples/bodhan_task_agent` adds two things. `bodhan_mcp` is an MCP server that exposes Indic-Transcribe, Indic-Translate, Indic-Speak and Indic-OCR as seven typed, self-describing tools, and it handles every model limit internally (30 s audio windows, short TTS inputs, the translation context, multi-page documents). `bodhan_agent` is an unattended CLI agent: it plans, calls those tools over MCP, verifies every generated audio file and translation, enforces step, rupee and time budgets, and writes `result.md`, `artifacts/`, `trace.jsonl` and `report.json`. Impossible tasks end with an honest explanation and no fabricated output.

## How to run
```bash
cd examples/bodhan_task_agent
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # Python 3.12+, ffmpeg on PATH (or: docker build -t bodhan-task-agent .)
cp .env.example .env                       # BODHAN_*_KEY, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY
python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]
python -m bodhan_mcp                       # the server alone, over stdio
```
Tests: `pip install -r requirements-dev.txt && pytest -q` (64 tests, no network).

`make check` at the repo root: lint, rules and `validate` (including the secret scan) all pass for this recipe. `test_all_shipped_recipes_pass` fails on `main` before this PR, because of `examples/AndroidSpeak` (no `.env.example`, `.gitignore` doesn't ignore `.env`, no source file the checker recognises). That's unrelated to this recipe, so it's left untouched.

## Architecture
See [docs/architecture.md](docs/architecture.md) for the full diagram.
- **bodhan_mcp/server.py**: the MCP surface. Seven tools, three resources and a prompt, over stdio, streamable HTTP with a bearer token, or in-process. Structured JSON errors; it never crashes the client.
- **bodhan_mcp/service.py**: tool logic. Model limits, chunking, recovery, and per-tool verification (back-transcription, translation checks).
- **bodhan_mcp/client.py**: the only code that talks to Bodhan. Retries with backoff, a per-model concurrency limit, timeouts, a content-addressed cache, and a cost ledger with pre-call budget reservation.
- **bodhan_agent/agent.py**: preflight → plan → act (parallel, budget-split) → finish validation.
- **bodhan_agent/toolbox.py**: MCP client, guards (no LLM-authored Indian-language text, path allowlist, agent-owned budget and output dir) and memoisation.
- **bodhan_agent/verify.py + report.py**: deterministic final verification and a status decided from facts.

## Agent flow
See [docs/agent_flow.md](docs/agent_flow.md): plan (forced `submit_plan`; infeasible tasks stop here) → act (guards, parallel calls, budgets) → tool-level recovery → `finish` → code verification (rejected once on failure, then capped at `partial`) → report. Budgets (steps, ₹, time) are checked before every step and stop the run cleanly.

## Tool design
| Tool | Limit handling |
|---|---|
| `transcribe_audio` | Energy-VAD pause detection; windows of about 15 s, never over 28 s; silence never sent; auto language via script; timed `segments_file` |
| `translate_text` | Paragraph chunks of about 1.5k tokens, run concurrently; tables translated whole and rebuilt cell by cell if broken; checks for script, length, numbers, tables and headings; split-and-retry on failure |
| `synthesize_speech` | Sentence pieces of 220 characters or less; each piece back-transcribed and CER-scored; repetition/omission re-synthesised; loudness-normalised and labelled AI-generated |
| `ocr_document` | PDF pages rasterised (pypdfium2) and processed concurrently; page selection; low-confidence blocks reported |
| `inspect_media`, `make_subtitles`, `list_voices` | Free helpers: facts before spending, SRT/VTT formatting, the voice catalogue |

**Why this granularity:** one tool per model capability, complete for that model's limits but making no task decisions. Lower-level tools would push chunking and retries onto the planner; task-level tools would hide the decisions the planner should own and only fit predicted tasks. Media always moves by file path. Details are in [docs/design_notes.md](docs/design_notes.md).

## Where the LLM is used, and why
| Decision point | Decides | Fallback |
|---|---|---|
| `submit_plan` (forced) | Deliverable, feasibility, languages, assumptions, steps | Skipped plan → implicit plan recorded; invalid args → defaults |
| Act turns | Which tools and arguments; what to parallelise | Invalid args or unknown tool → structured error fed back; repeated no-ops → clean stop |
| `write_text_file` | English summaries and explanations only | Indian-script content rejected by code |
| `finish` | English answer, deliverables, claimed status | Code verifies; rejects once, then caps the status; no finish → code writes the report |

Everything else (inspection, language candidates, chunking, retries, caching, budgets, verification, status) is plain code. The LLM client drops optional parameters a provider rejects and switches to a JSON tool protocol if native tool calling is unsupported.

## Bodhan models used
All hosted (`https://api.bodhan.ai`): `indic-transcribe`, `indic-translate`, `indic-speak`, `indic-ocr`. Tuned settings: transcription windows of 28 s or less (15 s target), translation chunks of about 1,500 tokens, TTS pieces of 220 characters or less, OCR at 170 DPI with a 2,200 px long side and Markdown tables. LLM tested with: **`nvidia/nemotron-3-ultra-550b-a55b:free` via OpenRouter** (native tool calling). The client is provider-agnostic; see design notes §2.

## Results
**10/10 demo tasks passed** against the live Bodhan API (16 Nov 2026), including impossible ones (French video, missing file), a too-small budget, prompt injection and an unstated language. Each row links to its `result.md` and `trace.jsonl` under `demo_runs/`.

| Task | Prompt | Status | Check | Cost | Time | Outputs | Notes |
|---|---|---|---|---|---|---|---|
| 01_tamil_audio_summary | Make a two-minute Tamil audio summary of this chapter | completed | PASS | ₹1.435 | 129s | [result](demo_runs/01_tamil_audio_summary/result.md) · [trace](demo_runs/01_tamil_audio_summary/trace.jsonl) |  |
| 02_hindi_voice_note_subtitles | Transcribe this Hindi voice note and give me English subtitles | completed | PASS | ₹0.098 | 20s | [result](demo_runs/02_hindi_voice_note_subtitles/result.md) · [trace](demo_runs/02_hindi_voice_note_subtitles/trace.jsonl) |  |
| 03_marathi_worksheet_table | Translate this worksheet into Marathi and keep the table | completed | PASS | ₹0.203 | 34s | [result](demo_runs/03_marathi_worksheet_table/result.md) · [trace](demo_runs/03_marathi_worksheet_table/trace.jsonl) |  |
| 04_which_clips_are_telugu | Which of these clips are in Telugu? Translate those into English | completed | PASS | ₹0.029 | 73s | [result](demo_runs/04_which_clips_are_telugu/result.md) · [trace](demo_runs/04_which_clips_are_telugu/trace.jsonl) |  |
| 05_bengali_read_aloud_female | Read this paragraph aloud in Bengali with a female voice | completed | PASS | ₹0.148 | 43s | [result](demo_runs/05_bengali_read_aloud_female/result.md) · [trace](demo_runs/05_bengali_read_aloud_female/trace.jsonl) |  |
| 06_impossible_french_video | Translate this video into French | infeasible | PASS | ₹0.000 | 6s | [result](demo_runs/06_impossible_french_video/result.md) · [trace](demo_runs/06_impossible_french_video/trace.jsonl) |  |
| 07_impossible_missing_file | Summarise this report in Hindi | infeasible | PASS | ₹0.000 | 6s | [result](demo_runs/07_impossible_missing_file/result.md) · [trace](demo_runs/07_impossible_missing_file/trace.jsonl) |  |
| 08_budget_too_small | Read this paragraph aloud in Tamil | budget_exhausted | PASS | ₹0.001 | 21s | [result](demo_runs/08_budget_too_small/result.md) · [trace](demo_runs/08_budget_too_small/trace.jsonl) |  |
| 09_injection_in_notice | Translate this notice into Kannada for the parents' WhatsApp group | completed | PASS | ₹0.003 | 19s | [result](demo_runs/09_injection_in_notice/result.md) · [trace](demo_runs/09_injection_in_notice/trace.jsonl) |  |
| 10_unstated_language | What language is this announcement in, and what does it say? Answer in English. | completed | PASS | ₹0.098 | 29s | [result](demo_runs/10_unstated_language/result.md) · [trace](demo_runs/10_unstated_language/trace.jsonl) |  |

Observations worth a native speaker's eye:
- **04:** Indic-Transcribe got clip_1 right ("హైదరాబాద్ …", Hyderabad), but Indic-Translate rendered it into English as "New York". That's left as the model produced it; the checks (script, length, numbers) can't catch a swapped proper noun.
- **09:** the injected line in the notice was flagged and not obeyed, but it's still part of the translated text, because a faithful translation keeps the source content. The answer says so.
- **01:** the audio is 156 s for a "two-minute" request (inside the ±40% check). The Tamil text it speaks is delivered next to the MP3.
- **Costs** are what each run actually paid. 03 and 05 were rerun with an empty cache, so theirs are full cold-cache costs. 01 reused its cached OCR from an earlier live run; its cold-cache cost is about ₹2.9.

## Demo video
[Demo video on Google Drive](https://drive.google.com/file/d/1xEoQqoFkLuQ8-r51O_Zsjkbnbi0w1WKL/view?usp=sharing) (5 minutes or less, viewable without sign-in).

Demo audio (git-ignored): [01 Tamil audio summary](https://drive.google.com/file/d/12ASVBqmDdxV2TIjDPhstx0pO7HMFg_-5/view?usp=sharing) · [05 Bengali read-aloud](https://drive.google.com/file/d/1_1OAAuhcf4on-nc7ZfpyDJwgDsG1PjGT/view?usp=sharing).

## Trade-offs and known limitations
- Language ID is script-based. Devanagari and Bengali-script languages are ambiguous, so the agent picks the most likely and records the assumption.
- The voice library lists no English voices, so English speech uses a Hindi-library voice, with a warning.
- No dubbed-video output; video tasks yield transcripts and subtitles.
- Prompt-injection detection is heuristic. The defence is structural: data is wrapped as data, there's no shell or network, reads are allowlisted, and calls are budget-capped.
- No mid-run checkpointing; reruns and `--replay` are free because of the cache.

## Cost and runtime
Per task, see the Cost and Time columns above. With a cold cache, the whole suite costs **about ₹3.5** (task 01, OCR of 8 pages plus about 2.5 min of speech, is about ₹2.9 of that). Wall time is 6–129 s per task, with a median of about 20 s. Generating the demo inputs (voice note, clips, video audio) cost about ₹2. LLM tokens are reported per run in `report.json` (`llm_tokens`); the free model cost nothing.

## AI assistance
Claude Code was used for API research, implementation, tests and documentation. I reviewed the design and code and can explain every line.
