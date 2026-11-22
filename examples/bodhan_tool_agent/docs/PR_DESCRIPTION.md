<!-- PR title: [Agentic Eng Assignment] Tool server and agent — Preetha -->
<!-- Paste this into the pull request. Replace the three TODO lines (video link, MCP Inspector, your own words). -->

## Summary

An MCP server (`bodhan_mcp`) that exposes Bodhan's four Indic models as nine file-based tools, and a command-line task agent (`bodhan_agent`) that plans, calls those tools, verifies every output and reports honestly. Model limits (30-second audio windows, short speech inputs, translation chunking, one page per OCR call) are handled inside the tools, and budgets, checks and the final report are enforced by plain code, not by the LLM.

**Type:** new recipe
**Path:** `examples/bodhan_tool_agent/`
**Bodhan models used:** indic-transcribe, indic-translate, indic-speak, indic-ocr

## Checklist

- [x] I started from `examples/TEMPLATE/` (new recipes) and the folder matches the required layout
- [x] `README.md` explains what the recipe does, how to set it up, and how to run it
- [x] Dependencies are pinned (`name==version`) in `requirements.txt`
- [x] `.env.example` lists every variable; **no real key anywhere in the diff**
- [x] Notebook outputs are cleared (no notebooks in this recipe)
- [x] Only current model ids from `scripts/bodhan_api_rules.json` are used (`python scripts/validate_recipe.py examples/bodhan_tool_agent`: no findings)
- [x] `make check` passes locally for this recipe (the one failing check is in `examples/AndroidSpeak`, which this PR does not touch)
- [x] I ran the recipe end to end against `https://api.bodhan.ai` with my own key

## How to run

```bash
cd examples/bodhan_tool_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # needs ffmpeg on the system
cp .env.example .env                   # fill in the four Bodhan keys and LLM_BASE_URL, LLM_MODEL, LLM_API_KEY

# The task agent
python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" --files path/to/inputs/ --out ./out --max-steps 30 --max-cost-inr 20

# The MCP server on its own (stdio)
python -m bodhan_mcp

# Offline tests
pytest
```

## Architecture

Diagram: [docs/DESIGN.md#architecture](examples/bodhan_tool_agent/docs/DESIGN.md#architecture)

- **`bodhan_mcp/tools.py`**: the nine tools in one registry; the server and the agent both use it.
- **`bodhan_mcp/server.py`**: publishes the registry over stdio with the MCP SDK; structured errors; two resources (voices, languages).
- **`bodhan_mcp/client.py`**: the only code that calls Bodhan; cache, budget check, concurrency limit, retries with backoff.
- **`bodhan_mcp/media.py`, `textops.py`, `langs.py`**: deterministic helpers (pause detection, chunking, checks, subtitles, script detection).
- **`bodhan_agent/agent.py`**: the loop, budgets, the finish check, and the trace and report writers.

## Agent flow

Flow chart: [docs/DESIGN.md#agent-flow](examples/bodhan_tool_agent/docs/DESIGN.md#agent-flow)

Inventory (code) → plan (LLM) → act (LLM chooses, code runs) → verify (code) → retry or re-plan → finish check (code) → stop. The loop also stops when the step budget or the rupee budget runs out, and still writes `result.md`, `trace.jsonl` and `report.json`.

## Tool design

| Tool | Purpose | Limit handled inside |
| --- | --- | --- |
| `list_capabilities` | Languages, voices, styles, prices | none |
| `inspect_files` | Kind, duration, pages, script of each input | none |
| `transcribe_audio` | Any-length audio or video to transcript and timed segments | Pause detection; windows under 30 s; silence skipped |
| `translate_text` | File, segments or short text to another language | Paragraph-boundary chunks; tables kept; code skipped |
| `synthesize_speech` | Text to MP3 or WAV | Sentence-sized requests; back-transcription check; repeat and omission recovery |
| `ocr_document` | Image or scanned PDF to Markdown | One page per request; failed pages reported |
| `read_text` / `write_text` | Bounded reading; saving English text | Size limits; Indian-language text rejected in `write_text` |
| `make_subtitles` | Segments to SRT or VTT | none (no model) |

**Why this granularity.** One tool per API call would push audio cutting and character counting onto the planner. One tool per task would only handle the demo tasks. One tool per capability, with limits handled inside, lets the planner think in user-level steps, and a new task is a new sequence of the same tools. Media moves by file path; results carry paths, check reports and previews of at most 500 characters.

## Where the LLM is used, and why

| Decision | Fallback if it goes wrong |
| --- | --- |
| Understanding the task and choosing the tool sequence | Structured tool errors with hints; step budget |
| Resolving ambiguity (language, which file) | Assumption recorded in `result.md` |
| Writing English summaries and the final answer | Indian-language text rejected in code; status and file list come from code |
| Deciding to retry, re-plan or stop | Budgets end loops; the finish check rejects false "completed" claims |

The LLM never writes Indian-language text, never transcribes, reads images or produces speech. `write_text` and the finish check reject Indian-script text from the LLM.

## Bodhan models used

| Model | Access | Settings |
| --- | --- | --- |
| indic-transcribe | Hosted API | `language` passed when known; windows of at most 28 s |
| indic-translate | Hosted API | `script` native by default; `roman` and `codemix` available |
| indic-speak | Hosted API | Requests of at most 280 characters; optional `style` |
| indic-ocr | Hosted API | `table_format: markdown`; pages rendered at 170 DPI |

LLM tested with: `openai/gpt-oss-120b` on Groq (main), with `gemini-3.5-flash-lite` (Google, OpenAI-compatible endpoint) as an automatic fallback. `report.json` records which model answered each call.

**Note on the translation route.** The Cookbook documents `POST /translate`, which returned 404 for my keys. Translation works through `POST /v1/chat/completions` with `model: indic-translate` and a `target_language` field, so the client uses that route.

## Results

Offline: 92 tests pass (`pytest`), covering each tool's chunking and limit handling, structured errors, the cache, budgets, speech repeat and omission recovery, prompt-injection detection, LLM fallback and rate-limit handling, a multi-step end-to-end task, and the real server driven over stdio by the SDK's client.

Demo suite (`python -m bodhan_agent.selfeval --suite demo/tasks.json`): **8 of 8 passed** against the live API. Each task's `result.md`, `trace.jsonl` and `report.json` are in [`docs/demo_outputs/`](docs/demo_outputs/), linked per task from the README.

| Task | Status | Verdict |
| --- | --- | --- |
| 01 Read this paragraph aloud in Bengali with a female voice | completed | PASS |
| 02 Transcribe this Hindi voice note and give me English subtitles | completed | PASS |
| 03 Translate this worksheet into Marathi and keep the table | completed | PASS |
| 04 Which of these clips are in Telugu? Translate those into English | completed | PASS |
| 05 Make a two-minute Tamil audio summary of this chapter | completed | PASS |
| 06 Translate this paragraph into French (unsupported) | impossible | PASS: refused honestly |
| 07 Read this paragraph aloud in Hindi with a ₹0.0001 budget | impossible | PASS: stopped before spending |
| 08 Translate this notice into Tamil (file contains injected instructions) | completed | PASS: injection ignored and reported |

MCP client tested: MCP Inspector v2.16.0 (`npx @modelcontextprotocol/inspector python -m bodhan_mcp`): all nine tools and both resources listed; `list_capabilities` ran; bad inputs (a missing file, and a request to read `.env`) returned structured errors (`file_not_found`, `path_not_allowed`) without crashing or disconnecting. The server is also driven over stdio by the SDK's client in `tests/test_mcp_server.py`.

### Stress tests (worst cases)

`demo/stress/` holds 45 harder cases in four groups, each scored PASS, FAIL (a behaviour to fix) or LIMIT (a deliberately extreme case the system did not handle):

- **Speech (a01–a18):** real recordings from Google FLEURS (CC BY 4.0) with simulated damage: noise, clipping, low volume, reverb, overlapping speakers, long silences, wrong language, very short and very long audio. Character error rate is measured against FLEURS's own transcripts.
- **Pages (d01–d10):** generated pages with blur, rotation, low contrast, two columns, tables and a Hindi page.
- **Real scans (r01–r05):** a 1913 Census of India table and Hindi books from 1911 and 1934 (public domain), a Tamil book (CC0, Tamil Virtual Academy), and handwritten Bengali pages (Bongabdo, CC BY 4.0). These have no answer key, so they are scored on behaviour: honest status, text actually read, tables kept.
- **Text and tasks (t01–t12):** empty and unsupported files, numbers and units, tables with LaTeX, a long document, mixed scripts, an unsupported language, vague and out-of-scope tasks, step budgets, and two prompt-injection files.

| Group | Passed | Did not pass |
| --- | --- | --- |
| Text and tasks | 12 / 12 | none |
| Pages | 9 / 10 (including d08: a 12-page scan to a one-minute Telugu audio summary, ₹3.42) | d02 blurry low-resolution photo: LIMIT, 5 of 6 table rows read, so the agent reported `partial` |
| Real scans | 3 / 5 | r01 census table: Bodhan OCR returned "the page was not transcribed", so the agent reported `impossible`. r03 Hindi print from 1911: only 45 characters read, reported `partial` |
| Speech | 15 / 18 | a11 instrumental music: the transcription model produced 371 characters of words. a12 French audio: transcribed as if supported (LIMIT). a14 Marathi with no language given: assumed Hindi (LIMIT) |

Character error rate on real FLEURS speech: 1% clean, 8% for a three-minute file, 13–36% under heavy noise, two speakers or code-switching.

Fixes made because of these runs: a recording with no speech (or a few stray letters from noise) no longer produces a transcript file, and the answer must say so; subtler injected instructions are detected; vague tasks must record an assumption; Bodhan inputs the model reports it cannot process are not retried; malformed tool calls from the LLM are retried and then routed to the fallback; the rate-limited main model is used again as soon as its limit resets.

## Demo video

TODO link to a recording of five minutes or less showing several tasks, including the impossible one.

## Trade-offs and known limitations

- Language detection uses the script only, so Hindi and Marathi cannot be told apart; the agent records its assumption.
- Pause detection is an energy threshold; loud music can be treated as speech.
- Translation checks are structural (coverage, script, digits, tables), not semantic.
- The speech check threshold is fixed at a character error rate of 0.35, not tuned per language.
- A failed speech chunk is deliberately sent to the model again; every other repeated input is served from the cache.
- The rupee budget covers Bodhan models; LLM tokens are counted but not priced.
- No dubbed-video output; stdio transport only.
- The transcription model can produce words from instrumental music (stress case a11); there is no music detector.
- Bodhan OCR cannot read some very old or dense scans (stress cases r01, r03) and loses rows on blurry photos (d02); the agent reports these as impossible or partial instead of inventing text.
- Speech and scan test data is downloaded from public, licensed datasets (FLEURS, Bongabdo, Wikimedia Commons) by scripts; none of it is committed.
- Prompt-injection detection is pattern-based. It catches the common phrasings and the agent never acts on file contents as instructions, but a cleverly worded injection may go unreported.
- `gpt-oss-120b` on Groq occasionally emits a call to a tool that does not exist; the call is asked again and, if it repeats, answered by the fallback model.
- Real-scan and handwriting cases have no reference text, so they measure behaviour, not accuracy.
- The 13 production-tier Indic-Speak languages are not listed in the Cookbook rules, so all 22 languages with voices are accepted.

Stretch goals done: MCP resources (voice library and languages), parallel tool calls, a self-evaluation script with a pass rate, a 45-case stress suite with real speech and real scans, and LLM fallback across providers.

## Cost and runtime

Per demo task, from each run's `report.json` (the runs linked under Results):

| Task | Status | Steps | Bodhan cost | Time |
| --- | --- | --- | --- | --- |
| 01 Bengali read-aloud | completed | 5 | ₹0.000 (cached) | 10 s |
| 02 Hindi voice note to English subtitles | completed | 6 | ₹0.000 (cached) | 5 s |
| 03 Worksheet to Marathi | completed | 4 | ₹0.200 | 6 s |
| 04 Which clips are Telugu | completed | 7 | ₹0.000 (cached) | 7 s |
| 05 Six-page chapter to Tamil audio summary | completed | 9 | ₹0.823 (OCR cached from an earlier run that stopped at its ₹1.80 budget, which cost ₹1.861) | 46 s |
| 06 French (impossible) | impossible | 1 | ₹0.000 | 2 s |
| 07 Budget too small | impossible | 1 | ₹0.000 | 1 s |
| 08 Injected notice to Tamil | completed | 7 | ₹0.000 (cached) | 5 s |

"Cached" means the same input had already been sent to the same model in an earlier run, so the result came from the local cache at no cost; a first run pays the model price once. Times are for these cached runs; first runs are slower, mostly waiting on the Bodhan API. Every task stayed under its `--max-cost-inr` budget, and all are within the latency targets (single-step under 30 s, multi-step under 5 minutes).

All testing for this PR, including the full stress suite and every first run, cost ₹12.08 of Bodhan credit across 320 requests (OCR ₹7.00, speech ₹2.74, transcription ₹2.09, translation ₹0.25; see the screenshot in the README's Cost section). The LLM was `openai/gpt-oss-120b` on Groq's pay-as-you-go tier: US$0.12 for every run in this PR.

## AI assistance

I used Claude as a coding assistant for a large share of this recipe: drafting the tool and agent code, the offline test suite with a fake API, and the documentation. I chose the design (TODO: describe in your own words what you decided and changed), ran it against the live API, and can explain every line.
