<!-- PR title: [Agentic Eng Assignment] Tool server and agent — Priyanka Garhwal -->

## Summary
An MCP server (`bodhan_mcp`) that exposes Indic-Transcribe, Indic-Translate, Indic-Speak and Indic-OCR as 11 capability-level tools with typed schemas, model limits handled inside the tools, self-checks and structured errors, plus a CLI task agent (`bodhan_agent`) that plans, calls those tools by file reference, verifies its outputs, enforces step/rupee/time budgets and writes a replayable trace. Impossible tasks end with an explanation, never fabricated output. **Live demo: 8/8 tasks passed on the hosted Bodhan API.**

## How to run
```bash
cd examples/bodhan_tool_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # system dependency: ffmpeg on PATH (or use the Dockerfile)
cp .env.example .env                     # BODHAN_*_KEY (or BODHAN_API_KEY) + LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]
python -m bodhan_mcp                     # the MCP server on its own, stdio (add --http for streamable HTTP + bearer token)
pytest -q                                # 37 offline tests, no keys needed
python demo/make_inputs.py && python demo/run_demos.py   # rebuild demo inputs and re-run the 8 demo tasks
```

## Architecture
Diagram: [docs/design.md#architecture](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/design.md#architecture)
- `bodhan_mcp/client.py`: the only HTTP code. Per-model keys, retries with backoff (long backoff on 429), concurrency limit, content-addressed cache (the same input is never paid for twice), cost ledger
- `bodhan_mcp/media.py`: ffmpeg decode, silence-aware ≤28 s audio windows, PDF → page images, sentence/paragraph chunking that never splits tables
- `bodhan_mcp/tools.py`: the tools; each writes artifacts to disk and returns a short JSON summary with checks
- `bodhan_mcp/server.py`: FastMCP over stdio (or streamable HTTP + bearer token); voices and languages exposed as MCP resources
- `bodhan_agent/`: plan → act → verify → finish loop that uses the server's own tool schemas, plus budgets, trace and code verification

## Agent flow
Flow chart: [docs/design.md#agent-flow](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/design.md#agent-flow). Inputs are inspected in code (free) → the LLM must `record_plan` before any paid tool → tool calls guarded by duplicate/budget/timeout checks, run in parallel when independent → tools self-check (TTS back-transcription with automatic regeneration, translation coverage/number/table checks) → `finish` → code verification downgrades any claim the artifacts don't support. Missing inputs are decided in code without an LLM call; malformed planner tool calls are retried; if the planner answers in prose instead of calling `finish`, only tool-produced artifacts are claimed.

## Tool design
`inspect_media`, `supported_languages`, `list_voices`, `detect_language`, `read_text`, `write_text` (English only), `transcribe_audio`, `translate_text`, `synthesize_speech`, `ocr_document`, `create_subtitles`. One tool per *capability*, not per model: the planner chooses *what*, the tool guarantees *how*. Limits are handled inside: ≤28 s audio windows cut at silence, ≤220-char TTS requests, paragraph chunks within the translation context that never split tables, per-page OCR of rendered PDFs. Media always moves by file path, never base64 in the LLM context. Errors are structured (`code`, `message`, `retryable`, `hint`). Details: [Tool design](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/design.md#tool-design).

## Where the LLM is used, and why
Planning, choosing tools, writing English summaries/explanations, judging tool checks and recognising impossible tasks. It never transcribes, translates, synthesises, reads images or writes Indian-language text; this is enforced in code (`write_text` refuses Indian scripts; inline Indian text must trace back to a Bodhan output or an input file). OCR and transcript text is labelled untrusted and treated as data (the demo chapter contains a prompt-injection page, which was ignored). Fallbacks: [table](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/design.md#where-the-llm-is-used-and-why). **Tested with `openai/gpt-oss-120b` via Groq's OpenAI-compatible endpoint** (standard tool calling only, no provider-specific features).

## Bodhan models used
Hosted API (`https://api.bodhan.ai`), one key per model: `indic-transcribe` (language hint after detection), `indic-translate` (native script; `POST /translate`, falling back to the documented `/v1/chat/completions` transport because `/translate` returned 404 on 23 Nov 2026), `indic-speak` (voice chosen by language and gender from the library, optional style; every file back-transcribed), `indic-ocr` (Markdown tables, 170 DPI page renders, low-confidence blocks flagged).

## Results
Live run, 23 Nov 2026 — **8/8 passed**. All outputs, traces and reports: [docs/demo_runs/](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/) · [README results](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/README.md#results)

| Task | Pass | Status | Cost (Rs) | Time (s) | Output / trace |
|---|---|---|---|---|---|
| Make a two-minute Tamil audio summary of this chapter | ✅ | completed | 2.9175 | 327.4 | [result](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/tamil_audio_summary/result.md) · [trace](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/tamil_audio_summary/trace.jsonl) |
| Transcribe this Hindi voice note and give me English subtitles | ✅ | completed | 0.0711 | 26.3 | [result](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/hindi_note_english_srt/result.md) · [trace](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/hindi_note_english_srt/trace.jsonl) |
| Translate this worksheet into Marathi and keep the table | ✅ | completed | 0.202 | 66.4 | [result](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/worksheet_marathi/result.md) · [trace](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/worksheet_marathi/trace.jsonl) |
| Which of these clips are in Telugu? Translate those into English | ✅ | completed | 0.0176 | 182.0 | [result](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/which_clips_telugu/result.md) · [trace](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/which_clips_telugu/trace.jsonl) |
| Read this paragraph aloud in Bengali with a female voice | ✅ | completed | 0.0789 | 78.5 | [result](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/bengali_female_voice/result.md) · [trace](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/bengali_female_voice/trace.jsonl) |
| Translate this video into French | ✅ | not_possible | 0 | 16.4 | [result](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/french_impossible/result.md) · [trace](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/french_impossible/trace.jsonl) |
| Summarise the attached report in Hindi | ✅ | not_possible | 0 | 0.3 | [result](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/missing_file/result.md) · [trace](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/missing_file/trace.jsonl) |
| Read this whole chapter aloud in Hindi | ✅ | not_possible | 0 | 49.7 | [result](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/budget_too_small/result.md) · [trace](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/demo_runs/budget_too_small/trace.jsonl) |

Offline: `pytest -q` → 37 passed. `python scripts/validate_recipe.py examples/bodhan_tool_agent` → OK, no findings. (`make check` at the repo root also reports pre-existing errors in `examples/AndroidSpeak`, which this PR does not touch.)
Tool-level: `python -m bodhan_mcp` driven by the MCP Python SDK client and by raw JSON-RPC; `translate_text(target_language="fr")` returns a structured `unsupported_language` error instead of crashing.

## Demo video
VIDEO_URL (2 min 44 s: architecture, tests, the 5 completed tasks with the generated Bengali and Tamil audio, the prompt-injection page, and all 3 impossible tasks)

## Trade-offs and known limitations
- Script-based language ID cannot separate languages sharing a script (Hindi/Marathi, Bengali/Assamese); recorded as an assumption.
- No word timings from Indic-Transcribe, so subtitle cues are proportional within each ≤28 s window.
- Back-transcription CER mixes TTS and ASR errors; thresholds are conservative.
- Planner token cost is not in the rupee budget (only Bodhan spend).
- The Tamil task took 327 s vs the 5-minute target, almost entirely waiting on the free-tier planner's 429 rate limit.
- Not done: resumable runs, a live web UI for the trace. More in [design notes](https://github.com/Priyankagarhwal/Bodhan-AI-Cookbook/blob/assignment/tool-agent-Priyankagarhwal/examples/bodhan_tool_agent/docs/design.md#trade-offs-and-known-limitations).

## Cost and runtime
| Demo task | Bodhan cost (Rs) | Wall time (s) |
|---|---|---|
| tamil_audio_summary | 2.9175 | 327.4 |
| hindi_note_english_srt | 0.0711 | 26.3 |
| worksheet_marathi | 0.202 | 66.4 |
| which_clips_telugu | 0.0176 | 182.0 |
| bengali_female_voice | 0.0789 | 78.5 |
| french_impossible | 0 | 16.4 |
| missing_file | 0 | 0.3 |
| budget_too_small | 0 | 49.7 |

Total Bodhan spend for all 8 tasks: about Rs 3.3 (plus about Rs 0.1 to build the demo inputs).

## AI assistance
Claude (Anthropic) was used as a pair programmer for scaffolding, tests, documentation, running the live demo and producing the demo video. I reviewed every line and can explain all design choices in the debrief.
