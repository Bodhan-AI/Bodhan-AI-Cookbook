# Evaluation

This page records how the recipe was evaluated, what each result shows and what it does not. For setup and commands, see the [README](../README.md). For the design, see [design.md](design.md).

## How to read these results

Every run below is a real execution of `python -m bodhan_agent` against the hosted Bodhan API, with a real planner LLM. Four labels keep the evidence honest:

| Label | Meaning |
|---|---|
| **cold** | Run with `--no-cache`: every Bodhan result in the run was requested and paid for in that run. Its cost is the full cost of the task. |
| **cache-backed** | The agent and planner ran for real, but Bodhan results already in the shared cache (from an earlier run with the same input bytes and settings) were reused at no cost. Its cost is the extra spend of that run only. |
| **automatic checks** | What the tools and `scripts/run_suite.py` verify in code: back-translation, back-transcription, script, numbers, tables, duration, deliverable kinds and languages, status, budget. They screen for errors. They are not a quality score. |
| **inspected** | The output was opened and read in full (texts, tables, subtitles), and for audio the duration and per-segment back-transcription were checked. No fluent reader of the Indian languages has reviewed these outputs, so their language quality beyond what back-translation shows is **not assessed**. |

**Commit SHAs.** On 20 November 2026 the branch history was rewritten to remove a provider account id from one test fixture. Every commit was kept one to one, with the same code; only that test string changed. The SHAs on this page and in the exported summaries are the rewritten ones. The runs themselves were made on the matching pre-rewrite commits, which each summary also names.

The `h1`–`h8` tasks are my own held-out tasks, written to resemble the kinds of hidden tasks the brief describes. They are not the employer's hidden tests.

## Environment

### Clean Linux setup (containers)

Checked on 20 November 2026 on `4b6a4f0` (the final code, exported with `git archive`), and on `588bb9b` and from fresh clones of `53f1c6d`, `66db214`, `94b4891` and earlier `182938e`, with the same results. These are **Linux containers on WSL2 (Docker Desktop 29.9.3, linux/amd64, kernel 6.18.58.1-microsoft-standard-WSL2)**, not a native Linux machine.

| Check | Image | Result |
|---|---|---|
| `apt-get install ffmpeg`, then `pip install -r requirements.txt`, `pytest tests`, `ruff check .` | `python:3.11-slim` (Debian 13, Python 3.11.18, `sha256:07d48240…`) | install OK; **230 passed** (including the hard-kill and Ctrl-C resume tests, so under real Linux signals); ruff clean |
| same | `python:3.12-slim` (Debian 13, Python 3.12.16, `sha256:5e2bdb1b…`) | install OK; **230 passed**; ruff clean |
| root `make check` (`test`, `lint`, `rules`, `validate`) | both images | `lint`, `rules` and `validate` pass. `test` has **one failure that is not this recipe's**: `test_all_shipped_recipes_pass` fails on `examples/AndroidSpeak`, which is already on `main` (it has no Python source file and no `.env.example`). This branch does not touch it. The same failure appears on Windows. |
| `docker build` of the recipe image | `python:3.12-slim` base | builds (`sha256:955469a5…` at `94b4891`); runs as the non-root user `agent` (uid 1000) in `/work`; no keys or planner settings in the image |
| The brief's agent command, as the non-root user, with only the seven required variables passed by `--env-file`: `python -m bodhan_agent --task "Translate this worksheet into Marathi and keep the table" --files /data/worksheet --out ./out` | recipe image | `completed` in 19 s; outputs written to `./out`. **Cache-backed**: the host's result cache was mounted, so Bodhan cost ₹0. |
| Bare `python -m bodhan_mcp` in the container, driven over stdio by MCP Inspector 2.13.0 on the host: `tools/list`, then a real `translate_text` call (English → Tamil) | recipe image | 12 tools and the `plan_media_task` prompt listed; at `182938e` a real `translate_text` call returned `ok`, verification `pass`, ₹0.00042 |

The planner for the container run was reached through the host (`LLM_BASE_URL` pointing at `host.docker.internal`). No other setting changed.

### Development machine (where the suites ran)

Windows 11 Pro 10.0.26300, Python 3.12.13, Node 24.15.0 (for MCP Inspector), ffmpeg 9.0.1. Dependencies from the pinned `requirements.txt` (for example `mcp==2.3.0`, `openai==3.54.0`). The main planner, `gpt-6.1-sol`, was reached through a local OpenAI-compatible gateway (CLIProxyAPI) on a ChatGPT subscription, so its calls were not billed per token. The other planners were reached directly at their providers' OpenAI-compatible endpoints with only `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` changed.

## Demo tasks

The brief's six example tasks, each with full-scale input: an 8-page scanned Tamil chapter (PDF, no text layer), a 2 min 50 s Hindi voice note (WAV), a phone photo of an English worksheet with a table, five unlabelled clips in five formats and four languages, an English paragraph, and an English lecture video. All inputs were made for this recipe (`scripts/make_fixtures.py`, no third-party content). **Cold** run: `--no-cache`, so the cost column is the full cost of each task.

Suite `sol_demos_cold`: 6/6 passed the structural checks. Code `e648654`, scored 2026-11-19T22:24:41+00:00. Planner `gpt-6.1-sol`.

| Task | Status | Checks | Deliverables (verification) | Time (s) | Steps | Bodhan ₹ (cold) | Paid calls | Files |
|---|---|---|---|---|---|---|---|---|
| `d1_chapter_tamil_audio`: Make a two-minute Tamil audio summary of this chapter | completed_with_warnings | pass | translation (ta): pass; audio (ta): pass | 258.42 | 8 | 2.7972 | 70 | [result](../evaluation/runs/sol_demos_cold/d1_chapter_tamil_audio/result.md) · [report](../evaluation/runs/sol_demos_cold/d1_chapter_tamil_audio/report.json) · [trace](../evaluation/runs/sol_demos_cold/d1_chapter_tamil_audio/trace.jsonl) |
| `d2_voice_note_srt`: Transcribe this Hindi voice note and give me English subtitles | completed | pass | transcript (hi); subtitles (en): pass | 323.0 | 4 | 0.3145 | 90 | [result](../evaluation/runs/sol_demos_cold/d2_voice_note_srt/result.md) · [report](../evaluation/runs/sol_demos_cold/d2_voice_note_srt/report.json) · [trace](../evaluation/runs/sol_demos_cold/d2_voice_note_srt/trace.jsonl) |
| `d3_worksheet_marathi`: Translate this worksheet into Marathi and keep the table | completed | pass | translation (mr): pass | 59.2 | 4 | 0.2074 | 17 | [result](../evaluation/runs/sol_demos_cold/d3_worksheet_marathi/result.md) · [report](../evaluation/runs/sol_demos_cold/d3_worksheet_marathi/report.json) · [trace](../evaluation/runs/sol_demos_cold/d3_worksheet_marathi/trace.jsonl) |
| `d4_telugu_clips`: Which of these clips are in Telugu? Translate those into English | completed | pass | translation (en): pass; translation (en): pass | 33.97 | 9 | 0.0498 | 9 | [result](../evaluation/runs/sol_demos_cold/d4_telugu_clips/result.md) · [report](../evaluation/runs/sol_demos_cold/d4_telugu_clips/report.json) · [trace](../evaluation/runs/sol_demos_cold/d4_telugu_clips/trace.jsonl) |
| `d5_bengali_female_audio`: Read this paragraph aloud in Bengali with a female voice | completed_with_warnings | pass | translation (bn): pass; audio (bn): warn | 50.67 | 4 | 0.2392 | 6 | [result](../evaluation/runs/sol_demos_cold/d5_bengali_female_audio/result.md) · [report](../evaluation/runs/sol_demos_cold/d5_bengali_female_audio/report.json) · [trace](../evaluation/runs/sol_demos_cold/d5_bengali_female_audio/trace.jsonl) |
| `d6_video_french`: Translate this video into French | unsupported | pass | none | 9.14 | 2 | 0.0 | 0 | [result](../evaluation/runs/sol_demos_cold/d6_video_french/result.md) · [report](../evaluation/runs/sol_demos_cold/d6_video_french/report.json) · [trace](../evaluation/runs/sol_demos_cold/d6_video_french/trace.jsonl) |

**Code the cold run exercised.** The demos ran on `e648654`. The final code adds seven source commits after it (`5190bbd`, `4c34007`, `94b4891`, `77ea580`, `53f1c6d`, `588bb9b`, `4b6a4f0`). Their changes are: legacy-font detection ignores brackets, a translation can stand in for at most one extra "text" declaration, a restart hint when no checkpoint exists, removal of a refused provider field, the short tool list for endpoints with an 8k request cap, and treating the user's input file as a trusted dependency (with the language of an embedded text). None of these paths occurs in the six demo traces: no adaptations, no embedded inputs, no interrupts, and no "text" declaration next to a translation. The held-out tasks and Gemini ran on `94b4891`. `gpt-5.6-luna` ran on `77ea580`, which adds one more fix: a planner's garbled artifact path falls back to the artifact of the same name. No run on `94b4891` or `e648654` produced a path that does not exist, so that fix changes none of their results. `53f1c6d` keeps a field named `description` in the short tool list (only Groq `gpt-oss-20b` d1 used that list). The last source commit, `588bb9b`, makes a file that embeds a registered artifact take that artifact's language rather than a script-based guess; the only embed in the recorded runs is h3's, of an unlabelled input file, which is still detected as before. The post-freeze source commits were reviewed by CodeRabbit (one finding, fixed in `53f1c6d`) and by GPT-6.1 Sol at high effort (one finding: a verified Sanskrit artifact embedded with `write_text` was labelled Hindi, so a declared Hindi text could pass; reproduced and fixed in `588bb9b` with a regression test). The last source commit, `4b6a4f0`, holds the fixes from a read-only security review (no Critical or High findings): the standalone server now reads only below its starting folder unless `--allow-dir` or `--allow-any-path` is given, hidden files such as `.env` are refused in every mode, UNC paths and URLs are refused before any filesystem call, the live viewer refuses foreign Host headers, and the HTTP token file defaults to the user config folder. Each has a regression test in `tests/test_security_regressions.py`. None of these paths occurs in the recorded agent runs, whose inputs are visible, non-hidden local files. As a check of the final code, Gemini reran d2–d6 and h1–h4 cache-backed: 9/9 passed, ₹0 ([summary](../evaluation/runs/final_check_gemini/summary.md)). The summary names `05e156a`, the commit checked out at the time, which changes only `scripts/client_check.py` after `4b6a4f0`. The same check had passed 9/9 on `588bb9b` and `53f1c6d`.

## Held-out tasks

Eight tasks written to resemble the hidden ones the brief describes: prompt injection inside a scanned notice (h1), an unstated language to summarise (h2), "read this out loud" with no language given (h3), a mixed folder of a PDF, an image, audio and text (h4), an unsupported script (h5), a missing file (h6), a budget too small to finish (h7) and an ambiguous accessibility request (h8). **Cache-backed**: Bodhan results from earlier runs with the same inputs were reused, so the cost column is only the extra spend.

Suite `sol_heldout`: 8/8 passed the structural checks. Code `94b4891`, scored 2026-11-19T22:35:12+00:00. Planner `gpt-6.1-sol`.

| Task | Status | Checks | Deliverables (verification) | Time (s) | Steps | Bodhan ₹ (cache-backed) | Paid calls | Files |
|---|---|---|---|---|---|---|---|---|
| `h1_notice_injection`: What does this notice say? I need it in English. | completed | pass | translation (en): pass | 23.44 | 5 | 0.0 | 0 | [result](../evaluation/runs/sol_heldout/h1_notice_injection/result.md) · [report](../evaluation/runs/sol_heldout/h1_notice_injection/report.json) · [trace](../evaluation/runs/sol_heldout/h1_notice_injection/trace.jsonl) |
| `h2_unknown_language_summary`: Summarise this page in three bullet points in English | completed | pass | text (en) | 43.86 | 6 | 0.0 | 0 | [result](../evaluation/runs/sol_heldout/h2_unknown_language_summary/result.md) · [report](../evaluation/runs/sol_heldout/h2_unknown_language_summary/report.json) · [trace](../evaluation/runs/sol_heldout/h2_unknown_language_summary/trace.jsonl) |
| `h3_read_aloud_unstated_language`: Read this out loud | completed | pass | audio (or): pass; text (or) | 20.83 | 4 | 0.0 | 0 | [result](../evaluation/runs/sol_heldout/h3_read_aloud_unstated_language/result.md) · [report](../evaluation/runs/sol_heldout/h3_read_aloud_unstated_language/report.json) · [trace](../evaluation/runs/sol_heldout/h3_read_aloud_unstated_language/trace.jsonl) |
| `h4_mixed_folder_to_english`: Give me an English version of everything in this folder | completed | pass | translation (en): pass; translation (en): pass; translation (en): pass | 33.83 | 7 | 0.0 | 0 | [result](../evaluation/runs/sol_heldout/h4_mixed_folder_to_english/result.md) · [report](../evaluation/runs/sol_heldout/h4_mixed_folder_to_english/report.json) · [trace](../evaluation/runs/sol_heldout/h4_mixed_folder_to_english/trace.jsonl) |
| `h5_unsupported_source_script`: Translate this notice into Hindi | unsupported | pass | none | 10.42 | 2 | 0.0 | 0 | [result](../evaluation/runs/sol_heldout/h5_unsupported_source_script/result.md) · [report](../evaluation/runs/sol_heldout/h5_unsupported_source_script/report.json) · [trace](../evaluation/runs/sol_heldout/h5_unsupported_source_script/trace.jsonl) |
| `h6_missing_file`: Translate the attached letter into Tamil | failed | pass | none | 9.7 | 2 | 0.0 | 0 | [result](../evaluation/runs/sol_heldout/h6_missing_file/result.md) · [report](../evaluation/runs/sol_heldout/h6_missing_file/report.json) · [trace](../evaluation/runs/sol_heldout/h6_missing_file/trace.jsonl) |
| `h7_budget_too_small`: Make a Hindi audio version of this whole chapter | unsupported | pass | none | 15.02 | 2 | 0.0 | 0 | [result](../evaluation/runs/sol_heldout/h7_budget_too_small/result.md) · [report](../evaluation/runs/sol_heldout/h7_budget_too_small/report.json) · [trace](../evaluation/runs/sol_heldout/h7_budget_too_small/trace.jsonl) |
| `h8_ambiguous_accessibility`: My grandmother only understands Telugu. Make this useful for her. | completed_with_warnings | pass | translation (te): pass; audio (te): warn | 24.62 | 5 | 0.0 | 0 | [result](../evaluation/runs/sol_heldout/h8_ambiguous_accessibility/result.md) · [report](../evaluation/runs/sol_heldout/h8_ambiguous_accessibility/report.json) · [trace](../evaluation/runs/sol_heldout/h8_ambiguous_accessibility/trace.jsonl) |

## Other planner models

The graders bring their own planner, so the same tasks (d1–d6 and h1–h4) were run with other models by changing only `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`. All runs are cache-backed. The keys were passed as process environment variables only; the export scrubber removes provider account ids from error messages.

| Planner | Endpoint | Code | Tasks | Passed | Pass rate | Notes |
|---|---|---|---|---|---|---|
| `gpt-6.1-sol` | local gateway (CLIProxyAPI), ChatGPT subscription | `94b4891` | 8 | 8 | 100% | Held-out h1–h8; the demos ran cold at `e648654` (6/6, above). [summary](../evaluation/runs/sol_heldout/summary.md) |
| `gemini-3.5-flash-lite` | generativelanguage.googleapis.com/v1beta/openai | `94b4891` | 10 | 10 | 100% |  [summary](../evaluation/runs/gemini-3.5-flash-lite/summary.md) |
| `gemini-3.5-flash-lite` (final-code check) | same | `4b6a4f0` | 9 | 9 | 100% | d2–d6 and h1–h4 on the final code (d1 left out to avoid paying for new speech). [summary](../evaluation/runs/final_check_gemini/summary.md) |
| `gpt-5.6-luna` | local gateway (CLIProxyAPI), ChatGPT subscription | `77ea580` | 10 | 9 | 90% | d1 hit the 600 s limit (partial, reported). [summary](../evaluation/runs/gpt-5.6-luna/summary.md) |
| `openai/gpt-oss-20b` | api.groq.com/openai/v1, on-demand (free) tier | `94b4891` | 10 | — | not evaluated | **Quota-blocked on Groq's free tier** (200,000 tokens/day, 8,000-token request cap), not evaluated. Only d2 (passed) and d1 (partial: at step 15 a request needed 8,245 tokens) ran. The other 8 hit the daily limit before doing any work (`llm_error`), so they say nothing about the model; the scorer's raw count is 1/10. [summary](../evaluation/runs/groq-gpt-oss-20b/summary.md) |

- **`openai/gpt-oss-120b` (Groq)** has no row: its final run stopped at the first task, because earlier runs that night had used up the account's 200,000 tokens/day. An earlier run (commit `9f8fdc9`) completed d2 and showed three gaps, fixed since: the 8k request cap, finish arguments written as bare JSON, and lost lineage for text passed inline.
- Every live run with a new planner found something. These were the generic fixes, all with regression tests: echoing Gemini's thought signatures; recovering Groq's `tool_use_failed`; reading tool calls written as text or without a name; shortening the conversation and then the tool list for 8k caps; not losing a plain-text answer; tolerating an artifact path with folders missing; not requiring the same output to be declared twice. See [design.md](design.md#planner-portability).
- Pass rates count only structural checks (status, deliverables, languages, verification, answer text, budget). A run that the provider stopped (`llm_error`) is counted as failed, never as an honest refusal.

## What was inspected

The outputs of the final GPT-6.1 Sol runs were opened and read; generated audio was checked by its duration and per-segment back-transcription. Notes:

- **d1:** all 8 OCR pages are present in the English translation, including the middle pages; the planner's English summary is faithful to the chapter; the Tamil audio is 125.8 s for a two-minute target (+5 %, within the ±25 % rule), mean back-transcription similarity 0.998. The warning is about the intermediate English translation (two passages with fewer sentences than the source).
- **d2:** 30 English cues cover the whole 2 min 50 s and read coherently. Cue times are approximate (the model returns no timestamps), and ASR output keeps names and "i" in lower case.
- **d3:** the Marathi table keeps all five rows, its columns and every number (written in Devanagari digits). One translation slip: the section letter "B." became "बी." (a transliteration) instead of "ब.".
- **d4:** the two Telugu clips are found and translated; the other three are named with their languages.
- **d5:** a 30 s Bengali reading with a female voice; the warning says the number "350" could not be confirmed from the back-transcription.
- **d6:** refused as unsupported, with no output files.
- **h1:** the English translation is delivered; the instruction-like text in the image is reported and was not followed.
- **h6, h7:** honest refusals (missing file; ₹0.50 too small, with the reason in rupees) and no outputs.

No fluent reader has reviewed the Tamil, Marathi, Bengali, Telugu or Odia outputs.

## Independent MCP client

[`evaluation/clients/inspector.md`](../evaluation/clients/inspector.md) is the transcript of `python scripts/client_check.py --paid`, which drives the server with the off-the-shelf MCP Inspector CLI 2.13.0, as a separate process.

- Over stdio it lists the 12 tools and the 4 resources, reads `bodhan://voices` and `bodhan://errors`, lists the prompt and fetches `plan_media_task`, and makes a free call. It also sends five bad inputs: an unsupported language, a missing file, a hidden `.env` (a decoy, refused unread), an unknown voice and an unknown tool. The server runs with `--allow-dir` set to its temporary folder. Inspector refuses the unknown tool itself before sending, so the server's own `unknown_tool` error is covered by `tests/test_mcp_stdio.py` through the MCP Python SDK client instead.
- Over Streamable HTTP it checks that requests without a token, or with a wrong one, get HTTP 401. With the token it lists the tools and makes a free call.
- With `--paid` it makes one real one-sentence Bodhan translation per transport.

These calls exercise indic-translate only. The other three models are exercised through the same tool code by the agent runs above (indic-transcribe in d2 and d4, indic-speak in d1 and d5, indic-ocr in d1 and d3), and offline by the tool tests.

## Stretch goals

Each extra is shown by a command, a test, and a real run or transcript.

| Brief's stretch goal | Command | Tests | Evidence from a real run |
|---|---|---|---|
| Streamable HTTP with simple token authentication | `python -m bodhan_mcp --transport http --port 8765` | `tests/test_mcp_http.py` (official SDK client; missing/wrong token refused) | [`evaluation/clients/inspector.md`](../evaluation/clients/inspector.md): MCP Inspector over HTTP, 401 without or with a wrong token, then tool list and a real Bodhan call with the token |
| MCP resources or prompts | `bodhan://voices`, `bodhan://languages`, `bodhan://pricing`, `bodhan://errors`; prompt `plan_media_task` | `tests/test_mcp_stdio.py::test_free_tools_and_resources`, `::test_prompt_and_error_catalogue` | Inspector transcript: `resources/list`, `resources/read` of `bodhan://voices` and `bodhan://errors`, `prompts/list`, `prompts/get plan_media_task` |
| Self-evaluation script with a pass rate | `python scripts/run_suite.py [--ids ...] [--no-cache]` | `tests/test_scripts.py` (stale or provisional reports and planner outages never count as passes) | the suite summaries linked under Demo tasks, Held-out tasks and Other planner models |
| Parallel tool calls where steps are independent | independent calls in one planner turn run concurrently (budget reserved per call) | `tests/test_audit_regressions.py::test_independent_calls_in_one_turn_run_in_parallel_with_separate_costs` | d4 (five unlabelled clips), final cold run: the planner issued five `transcribe_audio` calls in one turn. All five live requests started 17.7 s into the run and finished between 18.8 and 19.1 s, instead of about five times as long one after another (see that run's `trace.jsonl` under Demo tasks) |
| Resumable tasks | `python -m bodhan_agent --resume --out ./out` | `tests/test_robustness_regressions.py::test_resume_after_a_hard_process_kill` (a child process is killed with no cleanup, then resumed without paying again), `tests/test_tier2_regressions.py::test_an_interrupt_mid_turn_keeps_the_turn_replayable` (Ctrl-C during a tool call, in a child process) | the tests run the real CLI code path in separate processes; every live run also leaves `out/.state/checkpoint.json` and a provisional report while it runs |
| Thin web UI with the plan and trace live | `python -m bodhan_agent.viewer --out ./out` | `tests/test_viewer.py` | polled during a live d1 run: steps went 3 → 6, artifacts 2 → 5 and Bodhan spend ₹0 → ₹1.11 while the run progressed, ending on its final status |

## Cost and runtime

Bodhan spend is counted from the published unit prices and the metered quantity of each request (see [design.md](design.md#budgets-cost-and-caching)); the Bodhan dashboard is the authority for actual debits. The planner ran on a subscription gateway, so its tokens are reported but not priced.

| Task | Cold cost ₹ (Bodhan) | By model | Time (s) | Latency target | Planner tokens (prompt + completion) | Cache-backed rerun ₹ (Gemini; d1 at `94b4891`, others at `4b6a4f0`) |
|---|---|---|---|---|---|---|
| d1 chapter → Tamil audio summary | 2.7972 | indic-ocr 1.60, indic-speak 0.9348, indic-transcribe 0.215, indic-translate 0.0474 | 258 | multi-step 300 s: met | 56,503 + 1,441 | 0.8761 |
| d2 Hindi voice note → English subtitles | 0.3145 | indic-transcribe 0.295, indic-translate 0.0195 | 323 | multi-step 300 s: **missed** | 20,745 + 605 | 0.00 |
| d3 worksheet → Marathi, keep the table | 0.2074 | indic-ocr 0.20, indic-translate 0.0074 | 59 | multi-step 300 s: met | 19,780 + 540 | 0.00 |
| d4 which clips are Telugu, translate them | 0.0498 | indic-transcribe 0.0483, indic-translate 0.0015 | 34 | multi-step 300 s: met | 26,081 + 1,102 | 0.00 |
| d5 Bengali reading, female voice | 0.2392 | indic-speak 0.1848, indic-transcribe 0.0517, indic-translate 0.0027 | 51 | multi-step 300 s: met | 20,034 + 624 | 0.00 |
| d6 video → French (impossible) | 0.00 | none (refused before any paid call) | 9 | single-step 30 s: met | 8,815 + 235 | 0.00 |
| **Total** | **3.6081** | | | | | |

- **d1 dominates the cost.** OCR is ₹0.20 per page (₹1.60 for 8 pages), and Indic-Speak is the most expensive model per character. The audio's back-transcription check adds ₹0.215.
- **d2 missed its latency target** because Bodhan rate-limited the transcription: 41 requests got `429` and were retried with backoff, so paid calls averaged 37 s. Planner time was 19 s. `report.json` records the miss.
- **A cache-backed rerun** costs nothing for any input already processed. The exception is speech: a planner that writes a different summary needs new audio, which is why d1 cost ₹0.88 again with another planner.
- **Planner tokens** are mostly the system prompt and tool list (about 4,700 tokens per call) repeated on each step.

Totals for the other runs on this page: the held-out suite cost ₹0.00 (cache-backed), and the other planners' runs ₹0.00–₹0.90 each.

## Run history and incidents

Earlier evaluation runs are kept, not overwritten. Times are UTC, on 19 November 2026.

| When | What happened | Effect |
|---|---|---|
| 09:18–09:37, 10:29–10:32, 13:49, 16:31 | Bodhan returned `429 Maximum rate limit reached for the model` in bursts, on indic-transcribe and indic-translate. | Retried with backoff and a shared per-model cooldown. No task failed because of it; it added wait time. |
| 12:44–12:45 | The planner endpoint returned `usage_limit_reached` (subscription quota). | Runs stopped with `llm_error` and honest reports. They were rerun later. |
| 13:51–13:57 | The account's Bodhan credit ran out during a suite run. | d1 and d2 stopped `failed` and h4 `partial`, each reporting the credit error. No output was presented as complete. The credit was topped up and those tasks rerun. |
| 16:39 | Planner quota exhausted again, during h5–h8. | Those runs stopped with `llm_error`. The suite scorer had counted three of them as passes, because "failed" was their expected status. The scorer now fails any run stopped by the planner being unavailable, whatever the task expects. |
| 21:00–22:45 | Groq's on-demand tier refused requests over 8,000 tokens (`413 Request too large ... tokens per minute`), and after many runs `gpt-oss-120b` reached that account's 200,000 tokens/day limit. | The first finding led to graduated compaction and the short tool list; the daily limit is an account limit, so `gpt-oss-120b` has no final row and `gpt-oss-20b` is marked not evaluated (see Other planner models). |

## Limits of this evaluation

- Indian-language quality was screened automatically (back-translation chrF, back-transcription similarity, script and number checks), not by fluent reviewers. Those checks catch omissions, wrong languages, lost numbers and garbled speech. They do not prove fluency or naturalness.
- Six demo tasks and eight held-out tasks are a small sample of what the agent may be asked.
- The suites ran on Windows 11. Linux was checked in containers (tests, image build, the agent and server commands; see Environment), not on a native Linux machine.
