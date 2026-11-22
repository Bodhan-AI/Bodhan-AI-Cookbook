# Engineering Troubleshooting Log — Bodhan Tool Server & Task Agent

**Project:** Agentic Engineer take-home, Option 4 — MCP Tool Server and Task Agent
**Date of observed fixes:** 22 November 2026 (India Standard Time)
**Purpose:** Record reproducible failures, what we actually tried, why the fix works, and what still needs verification.
**Evidence:** User-run VS Code terminal output, `result.md`, `trace.jsonl`, `report.json`, and the successful connection diagnostic. Never paste credentials into this file.

> **Updated status (22 Nov 2026):** The Indic-Translate fallback works, and **a full live English-to-Bengali translation + female TTS + back-ASR verification run completed successfully at 14:13:01 IST**. Groq did not return HTTP 429 during this run. **One complete live demo is verified.** Independent MCP Inspector discovery, two basic tool invocations and a live OCR tool call have also succeeded. The complete Marathi worksheet run failed due to a Bodhan translation 429 and a false-success reporting bug; a patch is prepared but requires live verification. Other demo tasks, public video and PR remain pending. See `docs/LIVE_VALIDATION_RESULTS.md`.

## Summary of issues

| # | Issue / symptom | Observed attempts and outcomes | Current status |
|---|---|---|---|
| 1 | Windows: FFmpeg missing and artifact paths reported with `\` instead of `/` | Initial Windows test run: **18 passed, 4 failed**; after installation and Windows patch: **22 passed** | Fixed in local environment |
| 2 | Bodhan translation call returned `HTTP 404` | **Two unsuccessful live test runs** were shared: an end-to-end agent run (with two individual translation failures), and a standalone translation diagnostic. A third shared live test run succeeded using an API fallback. | Translation connectivity fixed; server-side cause of original 404 unconfirmed |
| 3 | Groq LLM returned `HTTP 429` | Seen during initial agent run; bounded retry/backoff patch added; **subsequent full live run completed all LLM turns** | Mitigated; not reproduced during successful run (account limits still apply) |
| 4 | Tests were passing only against mocks | Offline test count progressed **22 → 25 → 27**; real Bengali full pipeline completed | First live multi-step task proven; wider tests pending |
| 5 | First successful end-to-end live agent run | 22 Nov 2026, 14:12:41 to 14:13:01 IST; agent steps 7, real translation + speech + verification | Success for this task; 5+ remaining demo scenarios |
| 6 | Worksheet OCR succeeded but Marathi translation returned HTTP 429; `completed` was a false positive | 22 Nov 2026; one failed Marathi full-agent run (21.87 s), 17 budget reservations recorded | Regression fix prepared, live retest pending |

**Counting convention:** "Test run" means one command executing a live probe or agent workflow. It does *not* mean each API request inside that run. Based on outputs shared so far, **2 failed live translation test runs preceded the successful third live test run**. The initial full-agent run contained **two** unsuccessful `translate_text` tool calls; the successful diagnostic tried `/translate` (404) and then the alternative chat route (success). We should not claim that the fix happened on a particular "fourth attempt" without another timestamped log.

**Fix time:** The successful result was shared on **22 November 2026**, approximately **2:07–2:08 PM IST** according to the screenshot capture/upload timing. The diagnostic console output itself has **no wall-clock timestamp**, so this is an **approximation, not an exact recorded completion time**.

---

## Issue 1 — Windows-only setup failures

### Problem

The first Windows setup completed package installation, but the test run ended with **4 failures and 18 passes**. Some media functionality required `ffmpeg` and `ffprobe`, which Windows couldn't find. Separately, the agent returned `artifacts\summary.md` while the test expected portable `artifacts/summary.md`.

### What we tried

1. Installed project Python packages successfully using `setup_windows.ps1`.
2. Examined the four failing tests rather than changing model code blindly.
3. Installed/made FFmpeg available on `PATH` and adjusted artifact path reporting to use forward slashes (`Path.as_posix()`).
4. Updated the Windows setup script to fail clearly when dependencies or tests fail.
5. Reran the tests on Windows: **22 passed**.

### Final solution and why it works

FFmpeg handles audio conversion/chunking needed by real transcription and speech tasks. Converting **reported** artifact paths to POSIX-style separators makes the results consistent on both Windows and Linux without changing how Windows accesses files.

### Status

**Resolved for Windows setup.** Later runs passed **25** and then **27** offline tests. Tests are simulated; this does not validate live speech quality.

---

## Issue 2 — Bodhan Indic-Translate HTTP 404

### Exact problem

The agent attempted to translate an English paragraph into Bengali. The underlying call to `POST https://api.bodhan.ai/translate` returned **HTTP 404**. The first live agent run therefore produced no Bengali audio artifacts. A subsequent standalone translation probe also returned 404 even though its diagnostic showed the correct base URL:

```text
Bodhan base host: https://api.bodhan.ai
Effective translation route: https://api.bodhan.ai/translate
Translation probe FAILED: BODHAN_API_ERROR translate: HTTP 404
```

**Important:** A 404 means this request was not served at that endpoint; it does **not** independently establish the backend's underlying cause (deployment, account/model route access, gateway configuration, etc.). We did not find evidence that the configured base URL or the key was wrong.

### Attempt history — verified commands / outcomes

| Test round | Action | Observed result | Conclusion |
|---|---|---|---|
| 1 | Full Bengali translation-and-speech agent run | `translate_text` failed with `HTTP 404` twice; later an LLM request returned `429`; run ended `failed` with zero artifacts | Could not complete the requested task; separate API and rate-limit issues |
| 2 | `python diagnose_live.py --probe-translation` after base-URL/retry patch | 404 at correctly formed `/translate` URL | Base-URL correction and general retries alone were insufficient |
| 3 | Same diagnostic after adding a translation-specific fallback | `Translation probe SUCCESS; output characters: 22; cached: False`; `Successful translation route: /v1/chat/completions` | The alternative Bodhan route worked with the configured key and live input |

### Final solution (code behavior)

In `src/bodhan_shared/gateway.py`, `Gateway.translate()` now:

1. Builds the normal Indic-Translate REST payload: `{"text": source_text, "target_language": "bn"}`.
2. Calls **Bodhan** `POST /translate` first.
3. **Only if the error is HTTP 404**, retries using Bodhan's other documented interface: `POST /v1/chat/completions` with `model="indic-translate"`, the source text in `messages`, and `target_language_code="bn"`.
4. Reads the translated text from `choices[0].message.content`, normalizes it to the same internal `{"translation": ...}` shape, and caches it.
5. Does **not** silently switch models/providers for 401/403/429 or other failures. If both interfaces fail, returns both sanitized errors to help debugging.

### Why this works — plain English

The first API address was not serving translation requests for this setup. Instead of changing translation engines, we used **another officially supported entrance to the same Bodhan translation model**. We changed how we asked the service for translation, not which service produced it. A successful response was saved in `.bodhan_cache/` for reuse.

### Proof / result

The third shared test run printed:

```text
........................... [100%]
27 passed in 12.09s
Translation probe SUCCESS; output characters: 22 cached: False
Successful translation route: /v1/chat/completions
```

A cache JSON file contained the Bengali translation. `cached: False` matters: it indicates the result was obtained in that diagnostic run, not merely replayed from an earlier cached response.

### Status

**Live translation connectivity: verified successful on 22 Nov 2026.** The complete English → Bengali female-voice speech pipeline **was subsequently verified by a separate live agent run** (see Issue 5 below).

---

## Issue 3 — Groq LLM HTTP 429

### Problem

The first full agent run stopped with `LLM_HTTP_ERROR: LLM call HTTP 429`, after it had already called tools and encountered the translation 404. This was a separate issue. HTTP 429 commonly means the provider is imposing a rate/usage limit; the exact Groq account limit was not visible in the provided logs.

### What we tried

1. Checked the trace: the LLM did successfully plan several steps before the error.
2. Added **bounded retries** for LLM 429 responses, using `Retry-After` where available and limited backoff rather than uncontrolled rapid retries.
3. Added regression tests against simulated rate-limit responses. Those offline tests passed.

### Status

**Mitigated and observed successful for the sample task.** A subsequent full agent run made four successful LLM decision turns (steps 1, 3, 5, 7) and finished without HTTP 429. The logs do not prove account rate limits have disappeared or whether retry logic was exercised. If 429 recurs in larger workloads, inspect actual provider limits and retry headers.

---

## Issue 4 — Mock tests versus live model validation

Local test progression:

- Initial Windows environment: **18 passed, 4 failed**.
- After Windows-specific setup fixes: **22 passed**.
- After connection diagnostics and LLM rate-limit improvements: **25 passed**.
- After adding the 404 translation fallback and regression tests: **27 passed**.
- Separate real API diagnostic: Indic-Translate **successful**, via the chat interface.

Offline tests cover interface shapes, error handling, filesystem behavior, and simulated agent flows. After those tests, the Bengali demo succeeded against live APIs; however, **one real sample task** still cannot establish generalization, native-speaker fluency, or the hidden-task success rate.

---

## Issue 5 — First complete live agent run (resolved for the sample task)

### What was tested

Run date: **22 November 2026 (IST)**. Terminal start **14:12:41**, terminal end **14:13:01** (recorded by `Get-Date`). The task was: "Translate the attached paragraph into Bengali and read it aloud with a female voice." Input was the included `sample_data/paragraph.txt`. Credentials remained in a local untracked `.env`.

### Observed behavior and proof

- Command exit: success; `report.json`: `status=completed`, `error=null`, **7 steps**.
- Tool trace: `inspect_files` → `translate_text` → `speak_text`; all tool calls have `ok=true`. Four LLM decision events completed.
- Translation output: `artifacts/translated.paragraph.bn.md` (Bengali). Source meaning preserved in the short example. `translated.paragraph.bn.qa.json`: `numbers_preserved=true`, `math_preserved=true`, `roundtrip_similarity=0.777`, no warnings.
- Speech output: `artifacts/speech.bn.8f0060d.wav`, female Bengali voice **Ishita** in the agent's summary. Valid mono PCM WAV, **24 kHz, 12.032 s**. Agent wrote a script and checked the generated audio by Bodhan back-transcription.
- Speech QA: `checked=true`, **character similarity 0.975**, with the back-transcription and segment timestamps saved alongside the WAV.
- Elapsed in report: **19.39 seconds**; tool speech pipeline 14.46 s. Estimated Bodhan charges **₹0.1071**, against a **₹3** configured ceiling. Groq costs excluded; actual billing not verified.
- No audio or text outputs were fabricated. `report.json` listed **7 existing artifact files**.

### What this resolves, and what it does not

This proves the configured general LLM, Bodhan translation model, Bodhan speech model and Bodhan transcription model can cooperate for one complete real task. The earlier Groq HTTP 429 did not recur in this execution, but quota behavior under larger loads is not yet established. **Indic-OCR has not yet been exercised live**, and the third-party MCP client, Hindi subtitles, worksheet OCR and five-clip identification also remain to test. Script similarity is not a native-speaker rating and `roundtrip_similarity=0.777` is not a certified translation-accuracy percentage.

### Outcome

**Live task: passed; 1 of 6 demonstration scenarios confirmed.** For the next experiments, keep separate output directories, retain traces, and record both successes and failures without overstating what was validated.

---

## Completed live validation and next tasks

From `examples/bodhan_task_agent` on Windows PowerShell, with credentials only in the **uncommitted** `.env`:

```powershell
Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
.\.venv\Scripts\python.exe -m bodhan_agent `
  --task "Translate the attached paragraph into Bengali and read it aloud with a female voice" `
  --files .\sample_data\paragraph.txt `
  --out .\outputs\demo_bengali_live_01 `
  --max-steps 30 `
  --max-cost-inr 3
Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
```

**Result (22 Nov 2026): completed successfully** (see Issue 5). This command is retained as a reproducibility record, **not** an outstanding step. Prioritize the remaining five demonstration scenarios and independent MCP Inspector validation. Never paste your `.env`, API keys, or unredacted Authorization headers into logs or public issues.

---

## Future issue entry template

Copy this format for each new failure:

```markdown
## Issue N — <short, exact failure title>
**Date (IST):** YYYY-MM-DD
**First failure time (IST):** HH:MM:SS (if recorded)
**Resolved at (IST):** HH:MM:SS or "not resolved"
**Environment:** Windows/Python version, relevant library versions
**Command:** (redact keys and personal file paths if public)
**Observed error:** exact non-secret error code/message
**Failed attempts:** N test executions; list each change and result
**Root cause:** confirmed / probable / still unknown
**Final fix:** file/function and what changed
**Proof:** test output + one real output, if applicable
**Status:** resolved / partially mitigated / open
**Known limitations / follow-up:** ...
```

The run `trace.jsonl` records **elapsed time**, not the absolute wall-clock time. Use `Get-Date` before and after live commands when an exact timestamp matters.

---

## Issue 6 — Marathi worksheet: Bodhan rate limit and false `completed` status

**Observed on:** 22 November 2026, before the recorded terminal time of **14:39:01 IST**. The failed command did not record its own absolute start time; its `report.json` gives **21.87 seconds** elapsed.

**Input and objective:** `sample_data/worksheet_demo.png` → Bodhan Indic-OCR → Marathi Markdown, retaining its three-column/three-item table, numbers, math and LaTeX. No question solving.

**Evidence:** User-supplied `report(1).json`, `trace(1).jsonl`, `result(1).md`, and OCR page JSON from `outputs/demo_marathi_live_01/`.

### Exact problem

- CLI printed `status: completed`, `steps: 7`, and `estimated_bodhan_cost_inr: 0.2159`.
- `inspect_files` succeeded; `ocr_document` succeeded with `pages_ok=1`, `pages_failed=0`.
- `translate_text` returned `ok=false` with `BODHAN_TRANSLATE_UNAVAILABLE`:
  - documented REST `/translate` → **HTTP 404**;
  - alternate documented `/v1/chat/completions` with `indic-translate` → **HTTP 429**, message "Maximum rate limit reached for the model".
- Only `worksheet_demo.ocr.md` and its `.pages.json` existed: **no Marathi Markdown**. The LLM's final text acknowledged translation failure, but the agent classified the task `completed` because *some* artifact existed.
- The report lists **17 estimated translation reservations**, reflecting excessive fragment-level calls to translate separate lines and table cells. These budget entries **are not proof that 17 distinct HTTP requests were billed**.

### Root-cause analysis

1. **API limitation (confirmed):** Bodhan's working translation chat route refused a request with HTTP 429. The exact quota policy/reset time cannot be inferred from this trace.
2. **Request amplification (confirmed in code):** `_translate_structured` made many small calls (up to one per line/table cell), contributing to excessive requests. It is plausible this triggered the rate limit; the server's quota details were not captured, so the causal link is not proven.
3. **Completion validation bug (confirmed):** `missing_requested_outputs()` only checked audio/subtitles, not translated documents, so the OCR input became a false-success artifact.

### Local fix prepared (not yet live-verified)

- `src/bodhan_shared/tools.py`: attempt one Bodhan whole-document translation for short documents; only accept it when tables retain row/column structure, protected numbers and formulas match, and Markdown headings/lists are preserved. Otherwise use the existing conservative segmented fallback. Target-language text is **always generated by Bodhan**.
- `src/bodhan_shared/gateway.py`: mark Bodhan HTTP 429 as retryable and respect a bounded `Retry-After` wait during retry attempts.
- `src/bodhan_agent/runner.py`: require a translated artifact when the task requests translation; an English `.ocr.md` file alone no longer passes. Include errors and unmet requirements in `report.json`, with `partial` status when OCR succeeded but translation failed.
- Added four focused regression tests, including one reproducing the OCR-only false-completion scenario and another checking a single structured translation request.

**Offline proof:** 31 tests passed on Linux with model HTTP responses simulated. The fix remains **unverified against live Bodhan rate limits**, so no claim of full resolution is made yet.

**Next test:** Use MCP Inspector to call `translate_text` on the **already extracted** `artifacts/worksheet_demo.ocr.md`, without invoking OCR again. Check `ok`, the Marathi output file, table shape, numbers, LaTeX, and the `.qa.json`. If the one-request attempt still receives HTTP 429, inspect model quota/usage on Bodhan's console or contact `ishvinder@bodhan.ai` (without keys). If it succeeds, rerun the complete image-to-Marathi agent workflow in a *new* output directory to validate the automatic path.

**Status:** Failed live demo; coding remediation prepared, pending live verification.

---

## Issue 7 — MCP Inspector translation: 60-second timeout and Windows Unicode response error

**Reported:** 22 November 2026 (IST); exact local execution times were not recorded. **Attempts documented:** two independent Inspector invocations, following the earlier Marathi failure in Issue 6.

### Attempt 1 — Inspector deadline

Command: `npx.cmd -y @modelcontextprotocol/inspector --cli .\.venv\Scripts\python.exe -m bodhan_mcp --method tools/call --tool-name translate_text --tool-arg target_language=mr --tool-arg input_path=artifacts/worksheet_demo.ocr.md --tool-arg output_path=artifacts/worksheet_demo.translated.mr.md`

Result: Inspector reported **`Request timed out after 1m00s (tools/call)`**. This is a **client-side timeout**, and its message does not prove whether the underlying Bodhan call ultimately completed. Bodhan's rate limits and the server's bounded retry logic may prolong the request, but the specific underlying outcome was not captured.

### Attempt 2 — Unicode on Windows

Same command returned **`MCP protocol error: 'charmap' codec can't encode characters in position 293-308`**, followed by **`Invalid request`**.

Confirmed code path: the MCP server used `json.dumps(response, ensure_ascii=False)` to write the outer JSON-RPC response to `sys.stdout`. Under a legacy Windows pipe encoding (for example cp1252), Marathi characters cannot be written, so Python throws `UnicodeEncodeError`. The server's exception handler misleadingly emitted a generic protocol error. This **does not prove the translation failed**; an output Markdown file may already have been written before serialization.

### Proposed, locally tested fix

- The MCP server now emits **ASCII-safe outer JSON** using `json.dumps(response, ensure_ascii=True)`. A JSON consumer reconstructs the original Marathi characters. The internal `structuredContent` and text remain semantically unchanged.
- The server's stderr exception text is also safely escaped before writing.
- A new direct diagnostic script `diagnose_marathi_direct.py` invokes the same `ToolHub.translate_text` without Inspector's 60-second timeout, with a budget ceiling and guard against duplicate requests when an output file already exists.
- Two new tests simulate Windows `cp1252` stdout and validate successful Unicode round-tripping.

**Offline validation:** **33 tests passed** in the development environment (previous 31 plus 2 new tests), with fake model responses and no Bodhan charges. **Live fix status: not yet verified on the user's Windows machine.**

### Next verification

1. Check whether `artifacts/worksheet_demo.translated.mr.md` already exists before invoking the API again.
2. Apply the targeted patch and run offline tests.
3. If no translated file exists, run `python diagnose_marathi_direct.py` to bypass the Inspector deadline, and inspect error/result. Do not hammer the API if it returns 429.
4. If successful, verify the actual Marathi text, table columns and rows, values, time and LaTeX against the English source.
5. Only then mark the worksheet-to-Marathi demonstration successful. Do not infer a successful live end-to-end run from tests alone.

---

## Issue 8 — Marathi output saved, but numeric preservation fails QA

**Observed:** 22 November 2026, approximately 14:55 IST from the user's screenshot (not a logged execution timestamp).

**Evidence:** `artifacts/worksheet_demo.translated.mr.md` exists and contains a Marathi worksheet. `worksheet_demo.translated.mr.qa.json` reports:

```json
{
  "numbers_preserved": false,
  "math_preserved": true,
  "roundtrip_similarity": 0.798
}
```

Additional warnings include `document-batch-rejected` and repeated `protected-token-regenerated` entries. The screenshot shows a retained three-column table with rows for notebook (2 × 30), pencil (3 × 5) and eraser (1 × 10). The visible classroom-time line does not preserve the original `9:00 AM` formatting exactly.

### What failed vs. what worked

- **Worked:** Bodhan generated a Marathi-language Markdown file and saved it on disk. The earlier MCP transport exception did **not** establish that translation failed.
- **Worked:** Table values in the visible screenshot and LaTeX preservation reported as true.
- **Not yet verified:** The entire document's numeric integrity. The QA flag is explicitly **false**. The translated document has not been checked with a native speaker.
- **Unproven:** Which numeric token caused the comparison to fail. The full Marathi file is not available in the diagnostic workspace; the screenshot displays only part of the document.

### Diagnosis and safe next step

1. Preserve the original Marathi Markdown and its original `.qa.json` unchanged for evidence.
2. Run `audit_marathi_output.py` offline against the existing English OCR Markdown and translated Marathi Markdown. The script compares Unicode-normalized numeric tokens and math, checks table dimensions and numeric cells, and writes `artifacts/worksheet_demo.numeric_audit.json`.
3. If a difference is reported, inspect the specific line(s); only accept a repair after verifying the source meaning. Do not use a general-purpose LLM to write Marathi translation text. A genuine retranslation must use Bodhan Indic-Translate.
4. Once the numeric mismatch is resolved, retest the **complete image-to-Marathi agent** on a *fresh run* and assess its generated QA. A manually repaired direct-tool artifact is evidence of tool functionality, **not** proof of successful autonomous end-to-end execution.

**Offline audit code:** Four focused tests passed (no Bodhan calls); full project suite with patch: 37 tests passed in a separate Linux environment. The user had independently reported 33 passing tests on Windows before applying this audit-only patch.

**Status:** Partially resolved. Marathi text was generated; numeric integrity and fully autonomous end-to-end success remain open.

### Follow-up — exact numeric and structural audit passed

**Date:** 22 November 2026 (IST). **Exact audit time:** not supplied in the terminal output; do not infer a timestamp. **Evidence:** user-shared PowerShell output and `artifacts/worksheet_demo.numeric_audit.json`.

The updated project ran `python -m pytest -q` with **37 tests passing in 10.67 seconds**. The user then ran `python audit_marathi_output.py` on the already saved English OCR Markdown and Marathi translation; no model requests were made during this audit. Output:

```text
PASS numbers_preserved_in_order
PASS table_shape_preserved
PASS table_numeric_cells_preserved
PASS math_preserved_exactly
STRUCTURAL_CHECKS_PASS. Human review of Marathi meaning is still required.
```

The numeric audit reported **15 source tokens and 15 target tokens**, identical after digit normalization, with **zero numeric mismatches**, **zero table numeric cell issues**, and an identical LaTeX fragment: `$12 + 8 = \underline{\hspace{1.5cm}}$`. Specifically, the classroom time `9:00`, table amounts 2/30, 3/5, 1/10, and equation values 12, 8, 1.5 were preserved. The target does not necessarily retain `AM` formatting or punctuation exactly; these are outside the numeric-token check.

**Interpretation:** The existing `translated.mr.qa.json` had `numbers_preserved: false` and warnings about regenerated protected tokens. That coarse checker is **inconsistent with the final-file audit** for the numeric and structural properties tested. Do not delete or silently change the original QA result; attach the offline audit as complementary evidence and investigate/check the production QA comparison logic before claiming it is corrected. The underlying reason for that false flag is not yet proven.

**Updated status:** **Numeric/table/LaTeX integrity validated for this one saved Marathi worksheet.** Native-speaker linguistic review remains pending. **Autonomous image-to-Marathi demo not yet validated end-to-end:** the earlier agent run produced OCR only, and the Marathi output was subsequently generated through a direct tool invocation. The final-file audit is not a substitute for an end-to-end agent run on a new task or for live provider quota validation.

**Next priorities:** (1) preserve the audit and translated Markdown in demo evidence; (2) run a Hindi-audio-to-English-subtitles task with Bodhan models; (3) test an impossible-language task; (4) prepare remaining demo traces, video, clean-clone validation, PR, and final SHA before the 23 November deadline.


---

## Issue 9 — Hindi audio → English SRT: recoverable tool errors and redundant translation

**Observed:** 22 November 2026 (IST). The exact local start/end clock times for this run were not supplied. The run reports **121.67 seconds** elapsed.

**Task:** `sample_data/sample_hindi.wav` → Indic-Transcribe Hindi ASR → Indic-Translate English → English `.srt` with timestamped segments. Run folder: `outputs/demo_hindi_subtitles_live_01`.

**Evidence:** User-supplied `report(2).json`, `trace(2).jsonl`, `result(2).md`, `english_subtitles.srt`, `english_transcript.md`, Hindi and English segment JSON, and translation QA files.

### Result: completed with artifacts, but not without recoverable errors

- Reported `status=completed`, `error=null`, `unmet_requirements=[]`, 19 trace steps, estimated Bodhan cost **₹0.0266** (Groq cost excluded).
- Indic-Transcribe produced one Hindi segment from **0.000 to 6.485 seconds**.
- Indic-Translate generated English text: *"Hello, today we will start our math class. Please open your books and read the first page."*
- `artifacts/english_subtitles.srt` exists and has **one syntactically valid SRT cue** from `00:00:00,000` to `00:00:06,485`, exactly matching the English segment text and time boundaries. This was independently checked against the uploaded SRT/segment files.
- `artifacts/english_transcript.md` is the translated **English text**; it should not be described as an English-language ASR output. Hindi ASR was used on Hindi audio.
- Short single-segment samples exercise basic subtitle generation only, **not multi-segment alignment or long-audio chunking**.

### Tool failure A — wrong path supplied by LLM (recovered)

`translate_text` was initially invoked with an absolute path containing an incorrect folder spelling, producing `PATH_OUTSIDE_WORKSPACE`. The agent recovered by providing the recognized Hindi text directly to `translate_text` and requesting target `en`. This shows path validation working, but also an avoidable planner mistake. Prefer tool-returned relative paths from the active run rather than reconstructed absolute paths.

### Tool failure B — unsupported file extension (recovered)

The LLM attempted `save_english_text(path="artifacts/hindi_segments.json", ...)`. The tool properly rejected the `.json` path with `INVALID_OUTPUT`: only `.txt` / `.md` are supported. The agent then used `translate_segments` on the actual segment JSON and `create_subtitles` to write `.srt`. This is safe failure/recovery, not a task-fatal error.

### Unnecessary call / efficiency finding

After generating the requested SRT, the agent invoked `translate_text` again with **target_language=`hi` on already-Hindi text**. This produced an unnecessary artifact and consumed part of the budget. The 19-step / 121.67-second run is too verbose for a short 6.5-second clip, although it remains below the assignment's five-minute multi-step latency target. Do not claim this run demonstrates efficient planning; minimize redundant calls and stop after verification succeeds.

### Quality caveat

The Hindi ASR text includes `पहला पाथ`, where context suggests the spoken phrase was intended to be `पहला पृष्ठ` / `पहला पेज`. The English translation says "first page," which is contextually reasonable, but a native listener should verify the original audio. A one-cue SRT with coarse window timestamps does not validate word alignment.

**Current status:** **Functional demo passed**, with recoverable tool errors, wasted model request and ASR-level caveat documented. No code patch has been applied for these findings yet. Include the SRT, transcript, report, and (redacted) trace in demo evidence; exclude keys, personal paths and unrelated files from the public PR.

**Next steps:** (1) test an impossible task, ensuring honest refusal without invented artifacts; (2) exercise a longer/multi-segment audio case if practical; (3) verify the image-to-Marathi task *through the autonomous agent* rather than only direct tool; (4) record the ≥6 demo results, test setup in a clean clone, produce the ≤5-minute demo and submit the PR/commit SHA.


---

## Issue 10 — Unsupported French request: correct refusal, generic error and Windows display encoding

**Date:** 22 November 2026 (IST). **Exact execution timestamp:** not recorded in the supplied terminal output; do not invent one.

**Task:** Translate `sample_data/paragraph.txt` into French and save Markdown. Output: `outputs/demo_unsupported_french_01/`.

### Observed outcome — negative test PASSED

The user ran the autonomous agent with `--max-steps 20 --max-cost-inr 1`. The report says:

- `status`: `not_completed` (appropriate for an unsupported task)
- `error.code`: `REQUIRED_OUTPUT_MISSING`
- `steps`: **1**, `elapsed_seconds`: **1.14**
- `tool_events`: **[]**, `budget_events`: **[]**
- `artifacts`: **[]**, and independent PowerShell file listing was empty
- Estimated Bodhan cost: **₹0.00**, Groq cost not included

`result.md` explicitly says Indic-Translate supports the configured Indian languages and English, but **French (`fr`) is unsupported**, so it did not generate the requested output. No fabricated French text/file was produced. This is a successful *negative* test, not a completed French translation.

### Small imperfections (not blocking for this test)

1. **Generic error wording:** The `unmet_requirements` string reads `Requested translation was not generated (OCR-only output is insufficient)` even though this task involved an English text file and no OCR. The reason is overly generic. Recommend a more specific unsupported-language code/message in a future patch. Do not misreport this as an OCR failure.
2. **Console mojibake:** Windows PowerShell displayed Unicode punctuation as `Iâ€™m`/`Indicâ€‘Translate` when reading the UTF-8 Markdown. This is a terminal decoding issue, not evidence that the agent generated incorrect French. For PowerShell, use `Get-Content -Encoding UTF8 .\outputs\demo_unsupported_french_01\result.md`.

### Evaluation value and next steps

- Clear refusal instead of fabricated output demonstrates honest failure handling.
- No Bodhan operations occurred, so the test did not waste Bodhan credits.
- The assignment example uses an **MP4-to-French** impossible request; this tested **text-to-French** only. Run the exact video case if time permits and label these as distinct scenarios.
- For the PR, report this negative test accurately and link to the redacted `result.md` and `report.json`.

**Status:** Verified negative test passed. Optional improvement to error message and output encoding pending.
