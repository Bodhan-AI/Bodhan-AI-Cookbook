# Indic Task Agent

> An MCP server exposing Bodhan's four Indic models as tools, plus a budget-aware CLI agent that plans, runs and verifies multi-step language tasks.

## Setup

Needs Python 3.11+ and **ffmpeg** (with ffprobe) on `PATH`; the Dockerfile installs it.

```bash
cd examples/indic_task_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install --no-deps -e .   # or: uv sync
cp .env.example .env     # then fill in the keys; .env is gitignored
```

`.env` holds the four per-model Bodhan keys (`BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_OCR_KEY`; `BODHAN_API_KEY` is a shared fallback) and the planner's `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` (any OpenAI-compatible endpoint). The LLM never reads or writes Indic text.

## Run

```bash
# the task agent
python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20] [--ui] [--resume]

# the MCP server on its own: stdio, or streamable HTTP with a bearer token
python -m bodhan_mcp
BODHAN_MCP_TOKEN=... python -m bodhan_mcp --transport streamable-http --port 8765
```

Each run writes `out/result.md`, `out/artifacts/`, `out/trace.jsonl` and `out/report.json`. An impossible or unaffordable task exits 0 with `status: failed` and the reason; exit 2 means the command line was wrong.

## Tools

| Tool | What it hides from the caller |
|---|---|
| `probe_media` | Free: kind, duration, pages, script, estimated cost, silence, missing audio track, for a whole batch |
| `detect_language` | Free: script-based; shared scripts (Devanagari, Bengali, Perso-Arabic) return candidates, not a guess |
| `transcribe_audio` | Any container or video; cut at pauses into windows of at most 30 s; failed windows reported, never dropped |
| `ocr_document` | PDF or image, one page per request, 502 retry ladder (2048, 4096, 8192), headings rebuilt, per-page cache |
| `translate_text` | Whole-document, split at headings only when needed, one re-split on truncation, unsupported targets refused before any request |
| `speak_text` | Any length: sentence clips of at most 400 characters, joined into one WAV or MP3, voice resolved locally |

Files go in and out as paths. `--resume` continues an interrupted run in the same `--out`: the saved plan and every finished step are reused, so nothing already paid for is bought again. Prompts: `document_to_audio`, `audio_to_subtitles`. Resources: `bodhan://languages`, `bodhan://voices`, `bodhan://pricing`. Errors are JSON with `code`, `message`, `retryable`, `hint`.

## How the agent works

One LLM call writes a JSON plan; Python prices it, reserves cost before every call, settles after, runs independent steps in parallel, verifies each artifact and derives `status` from the checks. See [architecture](docs/architecture.md) and [agent flow](docs/agent-flow.md).

- **Verification:** audio is back-transcribed and compared, checked for repetition and against the requested duration; translations are checked for truncation, digits, length, table shape and (English sources) round-trip meaning; SRT cues for order and line length.
- **Budget:** estimates carry a 15% margin; one re-plan if over; otherwise a clean refusal before anything is bought. Timeouts are not re-sent, because the call may already be billed.
- **Untrusted content:** Indic text lives only in files. Arguments carrying Indic characters are refused; text files that give orders are flagged and ignored.

## Tests and evaluation

```bash
pip install pytest                          # once; tests are not in requirements.txt
pytest -q                                   # 172 tests, no network, no keys
python scripts/cold_read.py                 # stock MCP client against the stdio server
python scripts/check_media.py               # which demo media is present (free)
python scripts/eval_suite.py --replay demo_runs   # re-check recorded runs, free
python scripts/eval_suite.py                # live: spends money; skips tasks without media
```

Demo media is not committed; see `demo_tasks/media.manifest.json`.

## Live results

Ten tasks run against the real models; recordings are in `demo_runs/` (reports, traces, text outputs; no media). Re-check them for free with `python scripts/eval_suite.py --replay demo_runs`.

| Task | Status | Cost (Rs) | Time |
|---|---|---|---|
| Tamil chapter (8-page scan) to a 2-minute Tamil MP3 and text | completed, audio 124 s vs 120 asked | 0.99 | 252 s |
| Hindi voice note to English SRT | completed | 0.06 | 21 s |
| Marathi worksheet photo to Markdown with the table | completed | 0.20 | 23 s |
| Which of five clips are Telugu, translated | completed, 3 of 5 translations skipped | 0.05 | 48 s |
| Bengali paragraph read aloud, female voice | completed | 0.12 | 45 s |
| Translate a video into French | refused, nothing spent | 0 | 5 s |
| Missing file / tiny budget / long audio on a small budget | refused with the arithmetic, nothing spent | 0 | under 20 s |
| Text file that tries to give orders | completed, order ignored | 0.51 | 86 s |

Media is synthetic (generated with Bodhan's own models by `scripts/make_demo_media.py`, a 33 s Hindi note instead of 3 minutes). Costs are the tool-measured spend; the LLM is billed separately.

## Limitations

- Subtitle timing is interpolated inside each transcription window: the API returns text without timestamps.
- Indic-Speak can repeat or omit content; the back-transcription check reports it, it cannot repair it.
- Back-transcription similarity is a threshold, not proof of correctness.

## Acknowledgements

Thanks to the candidates whose open pull requests I read, with their agreement. Ideas, API findings and test scenarios only; no code or media was copied.

| Author | PR | What I took from it |
|---|---|---|
| CyberRik | #19 | API findings (audio format, rate limits); test scenarios |
| sujalgiriiitp-source | #20 | Translation route finding; unsupported-language handling |
| BishalJena | #21 | Cost and budget handling; completion rule; test scenarios |
| Rahulbharvadiya | #22 | Transcription response finding; output guard |
| BharadwajMittapelli | #27 | Table check for translated documents |
| code-killer0 | #28 | Text guard; OCR retry; test scenarios |
