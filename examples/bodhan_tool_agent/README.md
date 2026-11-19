# Bodhan tool server and task agent

> An MCP server that turns Bodhan's four Indic models into ten agent-friendly tools, and a command-line agent that plans, calls them, verifies its own output and stays inside a rupee budget.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages shown:** Hindi, Tamil, Telugu, Marathi, Bengali, Kannada, English (any language the models support works; French is shown as an honest refusal)
**Format:** CLI app (MCP server + task agent), Python 3.12+

## What it does

You give the agent a plain-language task and some files:

```bash
python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" --files chapter_scan/ --out ./out
```

It inspects the inputs, asks an LLM for a plan, runs the Bodhan tools (OCR the 8 pages, write an English summary, translate it to Tamil, speak it), back-transcribes the audio to check it, and writes `out/result.md`, the MP3 and Tamil text in `out/artifacts/`, a step-by-step `trace.jsonl` and a `report.json` with cost and verification results. If the task cannot be done (French, a missing file, a budget too small) it says so and produces nothing fake.

The server (`python -m bodhan_mcp`) works on its own with any MCP client. Each tool hides its model's limits: 30-second audio windows, short TTS inputs, the translation context window, one image per OCR call. Callers pass file paths and get file paths back.

## Setup

Requires **Python 3.12+** (the pinned numpy needs it). System dependency: **ffmpeg** (`apt install ffmpeg`, `brew install ffmpeg`, `winget install ffmpeg`). If it is not on `PATH`, the binary bundled with `imageio-ffmpeg` is used instead.

```bash
cd examples/bodhan_tool_agent
make setup                 # python -m venv .venv + pip install -r requirements-dev.txt (pinned) + editable install
cp .env.example .env       # make setup does this if .env is missing; then fill in the keys
make test                  # unit + end-to-end tests against a fake Bodhan backend: no keys, no cost
```

Without `make`:

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                    # or requirements.txt for runtime only
```

`.env` needs the four Bodhan keys (`BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_OCR_KEY`; `BODHAN_API_KEY` is the fallback for any left empty) and an OpenAI-compatible LLM endpoint (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`). See [.env.example](.env.example).

**Docker:**

```bash
docker build -t bodhan-tool-agent .
docker run --rm --env-file .env -v "$PWD/sample_data:/data" -v "$PWD/out:/out" \
  bodhan-tool-agent --task "Read this paragraph aloud in Bengali with a female voice" --files /data/paragraph_en.txt --out /out
```

**Demo inputs** are not committed (no media in git). Build them locally, about ₹2.5 once, cached afterwards:

```bash
make sample-data           # python scripts/make_sample_data.py; --local-only for the free parts only
```

It renders a worksheet image and an 8-page scanned-looking PDF with Pillow, and creates Indic voice notes with Indic-Translate + Indic-Speak (synthetic voices, no recordings of real people). Details and costs are in the [script's docstring](scripts/make_sample_data.py).

## Run

**The agent:**

```bash
python -m bodhan_agent \
  --task "Transcribe this Hindi voice note and give me English subtitles" \
  --files sample_data/hindi_voice_note.wav \
  --out ./out \
  --max-steps 30 --max-cost-inr 20
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--task` | required | The task in plain language |
| `--files` | required | File or directory; repeat the flag or pass a comma-separated list |
| `--out` | required | Output folder (created) |
| `--max-steps` | 30 | Maximum LLM turns |
| `--max-cost-inr` | 20 | Hard cap on Bodhan spend for this run |
| `--call-timeout` | 120 | Seconds per tool call |
| `--transport` | `inprocess` | `inprocess` or `stdio` (spawns `python -m bodhan_mcp`) |
| `--verbose` | off | Log every step to stderr |

**The MCP server on its own (stdio):**

```bash
python -m bodhan_mcp
```

Off-the-shelf client: **MCP Inspector 2.15.0**. Tested with its CLI mode: `tools/list` returns all 10 tools, and a bad call (`translate_text` with `target_language=fr`) returns a structured `unsupported_language` error instead of crashing.

```bash
npx @modelcontextprotocol/inspector python -m bodhan_mcp                       # UI
npx @modelcontextprotocol/inspector --cli python -m bodhan_mcp --method tools/list
```

**Claude Desktop** (`claude_desktop_config.json`), or any client that launches stdio servers:

```json
{
  "mcpServers": {
    "bodhan": {
      "command": "/abs/path/to/examples/bodhan_tool_agent/.venv/bin/python",
      "args": ["-m", "bodhan_mcp"],
      "env": {
        "BODHAN_API_KEY": "<your key>",
        "BODHAN_WORKDIR": "/abs/path/for/tool/outputs"
      }
    }
  }
}
```

**Streamable HTTP with a bearer token** (stretch goal):

```bash
export BODHAN_MCP_TOKEN=<a long random string>
python -m bodhan_mcp --http --port 8765
# clients connect to http://127.0.0.1:8765/mcp with header  Authorization: Bearer <token>
```

## Tools

| Tool | What it does | Limits handled inside | Cost |
| --- | --- | --- | --- |
| `inspect_file` | Kind, duration, pages, size, detected language, cost estimates; lists a directory | — | free |
| `read_text` | Slice of a text/Markdown/transcript file, flagged untrusted, with injection warnings | Keeps long text out of context | free |
| `transcribe_audio` | Any audio/video, any length, `language="auto"` or a code → timestamped transcript JSON + .txt | 30 s cap (VAD windows ≤ 28 s at pauses), silence dropped, unsupported containers converted | ₹0.10 / speech minute |
| `translate_text` | Text or file → target language; Markdown-aware; per-segment for transcripts | Context window and `truncated` (chunk, split, retry); tables, code, math preserved | ₹0.20 / 10k output tokens |
| `synthesize_speech` | Text or file → MP3/WAV, labelled AI-generated; optional back-check and repair | Sentence chunking (~220 chars), joins with pauses, repetition/omission repair | ₹6 / 10k chars (+ back-check) |
| `ocr_document` | PDF, image(s) or folder → Markdown + per-page JSON | PNG/JPEG only (PDF rendered), one image per call, 502 → higher `max_tokens`, headings rebuilt, furniture dropped | ₹0.20 / page |
| `verify_speech` | Back-transcribe audio, CER vs expected text, repetition, omission | Same windowing as transcription | ₹0.10 / min |
| `verify_translation` | Script, numbers, structure, length, untranslated spans; optional back-translation | — | free (back-translation: translate price) |
| `make_subtitles` | SRT/VTT from transcript or translated segments | 42 chars × 2 lines, ~7 s cues | free |
| `list_languages_and_voices` | Support matrix per model, voices, styles (also resources `bodhan://languages`, `bodhan://voices`) | — | free |

Errors are structured JSON: `{"code", "message", "hint", "retryable"}` with codes such as `file_not_found`, `unsupported_language`, `budget_exceeded`, `rate_limited`, `model_output_truncated`. Bad input never crashes the server.

## How it works

1. **Inventory (code).** `inspect_file` on every input: kind, duration, pages, script, cost estimates.
2. **Plan (LLM).** `submit_plan`: goal, interpretation, assumptions, target languages, steps, feasibility. Unsupported or impossible tasks stop here.
3. **Act (LLM + tools).** Tool calls, in parallel when independent. Intermediate results are files; the LLM sees short previews.
4. **Verify (code gate).** Every generated audio file and translation must have a passing check before `finish` counts as completed.
5. **Repair or stop.** One repair per failed piece, then `partial` with the failures listed. Budgets stop the run cleanly at any point.

- [Architecture diagram](docs/architecture.md)
- [Agent loop flow chart](docs/agent_flow.md)
- [Design notes: tool granularity, verification, budgets, recovery, untrusted content, LLM decision points](docs/design_notes.md)

## Outputs

```
out/
  result.md        what was done, the answer, assumptions, links to artifacts, warnings
  artifacts/       deliverables only: .mp3, .srt, .md, .txt
  trace.jsonl      one line per step: type, tool, arguments, result summary, latency, cost, cache hits
  report.json      status (completed|partial|impossible|failed), steps, verification results, cost, time
  work/            intermediates (transcripts, chunks, page images); BODHAN_WORKDIR for the run
```

## Verification

- **Speech:** every generated file is back-transcribed with Indic-Transcribe and compared with its input text (character error rate, duration per character, repetition). A failing chunk is re-synthesised once with a different split.
- **Translation:** target script, numbers carried over, Markdown structure (headings, lists, table shape), length ratio, untranslated spans, never `truncated`. Optional back-translation to English.
- **Self-evaluation:** `make self-eval` runs [tasks/demo_tasks.json](tasks/demo_tasks.json) (12 tasks, including impossible, injection, missing-file and tiny-budget cases) through the CLI and scores status, artifacts, output script, table shape and budget from `report.json`. Results go to `outputs/self_eval.json`.

## Budgets

- `--max-cost-inr` is enforced inside the Bodhan client: each paid call is estimated first and refused if it would cross the cap. Spend never exceeds the cap; the run stops with `partial` and says what was done.
- Disk cache (`.bodhan_cache/`) keyed by endpoint, parameters and input bytes, plus in-flight dedupe: the same input is never paid for twice, and reruns are free.
- Speech synthesis costs tens of times more per character than translation (₹6 per 10k input chars vs ₹0.20 per 10k output tokens), so text is finished and checked before it is spoken, and summaries are sized to the requested duration.

## Safety

- **Only Bodhan models** do speech-to-text, translation, TTS and OCR. No other ASR/MT/TTS/OCR library is installed or called.
- **Indic-text guard:** the LLM may write English only. The agent rejects inline Indian-language text passed to `translate_text`/`synthesize_speech` and any Indic script in `write_text_artifact`; Indian-language text only flows file-to-file between Bodhan tools.
- **Untrusted content:** OCR, transcript and file text is treated as data. Tools flag instruction-like lines (`injection_warnings`); the agent wraps file content as data in the prompt and reports detected injections in `result.md`.
- **Labelled audio:** generated audio carries an "AI-generated speech (Bodhan Indic-Speak)" metadata tag and `result.md` says so. Voices come from Bodhan's fixed library; no voice cloning.
- **No secrets in the repo:** keys come from the environment; `.env` is git-ignored.

## Results

Run on 2026-11-16/17 against the hosted Bodhan API, with **`gemini-3.5-flash-lite`** as the planner LLM (Google's OpenAI-compatible endpoint). Every run's `result.md`, `report.json`, `trace.jsonl` and text artifacts are committed under [`demo_runs/`](demo_runs/); audio artifacts are kept out of git. Scores come from `scripts/self_eval.py` ([`demo_runs/self_eval.json`](demo_runs/self_eval.json)), which checks status, artifact kinds, output script, tables, budget, verification and latency. It does not judge translation quality; that needs a reader.

| Task | Inputs | Status | Pass | Cost (₹) | Time (s) | Output / trace |
| --- | --- | --- | --- | --- | --- | --- |
| Two-minute Tamil audio summary of a chapter | 8-page scanned PDF | completed (audio 1:51) | ✅ | 2.600 | 94 | [run](demo_runs/tamil_audio_summary/result.md) |
| Hindi voice note → English subtitles | 3 min WAV | completed | ✅ | 0.044 | 65 | [run](demo_runs/hindi_note_english_srt/result.md) |
| Worksheet → Marathi, keep the table | PNG | completed | ✅ | 0.003* | 12 | [run](demo_runs/worksheet_marathi_table/result.md) |
| Which clips are Telugu? Translate those | 5 clips | completed | ✅ | 0.041 | 21 | [run](demo_runs/telugu_clips_to_english/result.md) |
| Paragraph aloud in Bengali, female voice | TXT | completed | ✅ | 0.372 | 42 | [run](demo_runs/bengali_read_aloud/result.md) |
| Translate this video into French (impossible) | MP4 | impossible | ✅ | 0.000 | 4 | [run](demo_runs/video_to_french_impossible/result.md) |
| Clip in an unstated language → English | WAV (Kannada) | completed | ✅ | 0.012 | 23 | [run](demo_runs/unstated_language_clip/result.md) |
| Worksheet with an injected instruction → Hindi | PNG | completed, injection reported | ✅ | 0.203 | 30 | [run](demo_runs/injection_worksheet/result.md) |
| Notice with an injected instruction → Tamil | TXT | completed, injection reported | ✅ | 0.003 | 14 | [run](demo_runs/injection_notice_text/result.md) |
| Missing input file | — | impossible | ✅ | 0.000 | 4 | [run](demo_runs/missing_file/result.md) |
| Budget too small (`--max-cost-inr 0.05`) | TXT | partial: translated, then stopped before speech synthesis that would exceed the budget | ✅ | 0.000 | 8 | [run](demo_runs/budget_too_small/result.md) |
| Ambiguous: "for my grandmother in Chennai" | TXT | completed (assumed Tamil, made Tamil audio; assumption recorded) | ✅ | 0.465 | 37 | [run](demo_runs/ambiguous_grandmother/result.md) |

\* The worksheet's OCR call was a cache hit from an earlier smoke test; a cold run adds ₹0.20 for one page.

Self-eval pass rate: **12/12**, total Bodhan cost **₹3.74** for the suite. Demo video (≤ 5 min): **TBD (link)**.

Quality targets from the assignment, on this suite: every generated audio file and translation passed verification before the run finished; no paid input was sent to the same model twice within a run (cache); no run exceeded its `--max-cost-inr`; single-step tasks finished well under 30 s and multi-step tasks under 5 minutes (slowest: 94 s).

The Hindi voice-note run shows recovery in action: Indic-Transcribe returned HTTP 429 four times, the agent called the tool again, and windows that had already succeeded came from the cache at no cost, so the trace has five `transcribe_audio` calls but each audio window was billed once. Transcription concurrency now defaults to 2 to avoid the throttling in the first place.

Second planner check: the suite was also run with `openai/gpt-oss-120b` on Groq's free tier. Every task whose run fit that tier's quota completed, 5 of 5 (Hindi SRT, Marathi table, Bengali read-aloud, unstated-language clip, and the French request refused as impossible); the slower runs exceeded the suite's latency targets only because of rate-limit waits. The other three hit the free tier's limits (a single request over 8,000 tokens per minute, then the daily token cap) and ended as honest `partial` results listing what was done. The remaining four tasks were not reached before the run was stopped. That run exposed a real bug, now fixed with tests: the LLM client ignored the provider's `Retry-After` and gave up on 429s too early.

Issues found during the demo runs and fixed (each with a regression test): Gemini rejects the second turn unless each tool call's `thought_signature` is echoed back; the language detector split Devanagari words at vowel signs, so Marathi was reported as Hindi.

## Limitations

- Language detection for audio uses the script of a first-window transcription. Very short clips, code-mixed speech and languages that share a script (Hindi/Marathi/Nepali in Devanagari, Bengali/Assamese) can be misidentified; the guess and its confidence are reported, not hidden.
- Translation checks are structural (script, numbers, tables, length). They catch truncation and dropped sections, not subtle meaning errors; back-translation is optional and costs extra.
- Speech verification uses Indic-Transcribe as the judge, so its own errors add noise to the CER. Thresholds are not yet tuned per language.
- The summary duration is targeted from an estimated characters-per-second rate; actual length can differ by 20–30%.
- OCR quality on photos with strong perspective or handwriting is whatever Indic-OCR returns; we do not deskew beyond format normalisation.
- Runs are not resumable mid-task; the cache makes a rerun cheap instead.
- Only the hosted API was used; self-hosted weights were not tested.
- The hosted API differed from its reference on 2026-11-16, and the client follows observed behaviour: `POST /translate` returned 404, so translation goes through `POST /v1/chat/completions` with `model: indic-translate`; Indic-Speak returned MP3 rather than the documented WAV (the client accepts either); Indic-Speak and Indic-Transcribe returned 429 at four parallel requests and sent no rate-limit headers or Retry-After, so both default to 2 parallel requests (`BODHAN_CONCURRENCY_SPEAK`, `BODHAN_CONCURRENCY_TRANSCRIBE`) and rate-limit retries back off from 2 s instead of 0.5 s.

## AI assistance

I built this with Claude Code (Anthropic) as the implementation tool. Claude Code wrote the code, tests and docs, ran the live API checks, and fixed the bugs those checks turned up. My part:

- **Scope and direction:** I picked this option out of the four, set the constraints (a hard budget on Bodhan credits, the planner LLM provider and model, keys per model), and decided when each stage was good enough to commit.
- **Review and understanding:** I read through the code and the design, and worked through the architecture until I could explain each decision and the alternatives it beat: capability-level tools, file-path data flow, the plan-first loop with the verification gate in code, budget reservations, back-transcription checks on speech, and the guards on untrusted text.
- **Demo:** I ran the demo suite.

I can walk through any part of the code, why it is built the way it is, and how I would extend it.

No assistant is used at runtime: every LLM call in the shipped agent goes to the endpoint set by `LLM_*`.

---

Licence and data: all demo inputs are generated locally by `scripts/make_sample_data.py` (original English text, Pillow renders, Bodhan synthetic voices); the Cookbook's `sample_data/sample_hindi.wav` is reused if present. No media is committed.
