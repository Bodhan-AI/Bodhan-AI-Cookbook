# Design notes

Diagrams: [architecture.md](architecture.md) and [agent-flow.md](agent-flow.md).

## 1. Tool list and granularity

| Tool | Model | Why it exists |
|---|---|---|
| `transcribe_audio` | Indic-Transcribe | Speech to text. Windows, language guess and timed segments are inside. |
| `translate_text` | Indic-Translate | Text to text. Chunking and layout preservation are inside. |
| `speak_text` | Indic-Speak | Text to WAV. Splitting, joining, voice choice, cache and AI-generated notice are inside. |
| `ocr_document` | Indic-OCR | Image or PDF to Markdown. Page rendering and per-page calls are inside. |
| `make_subtitles` | Indic-Translate (optional) | SRT from timed segments, so the LLM never writes timestamps or subtitle text. |
| `inspect_media` | none (free) | Kind, duration, pages, estimated cost, "can Bodhan do this at all". |
| `detect_language` | none (free) | Language by script, for inputs whose language is unstated. |
| `read_text_file`, `write_text_file` | none (free) | Read text inputs and write Markdown outputs. Writes are confined to the output folder. |

**Granularity decision: one tool per model, plus a few helpers.** Two alternatives were rejected:

- *Raw one-call-per-API-request tools* push chunking, paging and joining onto the planner. That wastes steps and
  context, and a weak LLM gets it wrong. The assignment says callers must never handle limits, so each tool absorbs
  its own model's limits.
- *High-level tools such as "translate this document"* hide control: the planner cannot choose the target
  language of a transcript, skip OCR for a text file, or stop before TTS when the budget is thin. Hidden tasks
  vary too much for fixed pipelines (and would drift toward demo-specific logic).

The one exception is `make_subtitles`, which is higher-level on purpose: subtitles need timings from audio and
one translation per cue, and nothing about that is a decision for the LLM. The workflow shapes
(`translate_document`, `speak_in_language`, `subtitle_audio`) are exposed as MCP *prompts*, not as extra tools.

**Media plumbing.** Every tool takes and returns file paths. Long outputs (OCR Markdown, transcripts, translations)
are written to `out/work/` and returned as `*_path` with only a short preview inline (`output.inline_chars`, default
3000). The planner passes the path to the next tool as `text_path`. No base64 anywhere.

**Typed contracts and errors.** Each tool has a Pydantic result model with `ok`, its payload and `cost_inr`. Failure is
`ok=false` with `error {code, message, retryable}`. Messages say what to change. Bad input never raises through MCP.

## 2. What the LLM does, and what plain code does

| Decision | Owner | Why |
|---|---|---|
| Understand the task, pick tools and order, choose target language and voice | LLM | Open-ended; this is what the planner is for. |
| Decide a task is impossible (French, dubbing, missing file) and explain | LLM, guided by the system prompt and `bodhan://languages` | Needs judgement; the harness also downgrades unverified success. |
| English summaries and assumptions in `finish_task` | LLM | English only. Indian-language text is quoted verbatim from tool results. |
| Chunking, windowing, paging, joining | Plain code in tools | Deterministic, testable, free. |
| Language of unlabelled audio or pages | Plain code (script/char-set) | Allowed by the assignment; free; the LLM only reads the result. |
| Cost estimates, budget refusals, per-call timeouts | Plain code in the harness | A budget must not depend on LLM compliance. |
| Verification | Plain code (back-transcription goes through the transcribe tool) | Independent of the LLM's opinion of its own work. |
| Injection screening | Plain code tripwire plus structure | See section 5. |

If the LLM fails (no tool call, malformed `finish_task`), the run is nudged once, then ends as `partial` with the
trace intact. It never claims success without a recorded `finish_task` and passing checks.

## 3. Verification

Runs after `finish_task` (or after the planner stops without it), over every output produced since the last check:

- **Audio:** back-transcribe the WAV with Indic-Transcribe and compare with the spoken text (similarity and length
  ratio). This catches Indic-Speak repeating or omitting content.
- **Translation:** not truncated, same line and table-row structure, plausible length ratio, target script present.
- **Subtitles:** valid SRT, cue count consistent with the source segments, target script.

A failure triggers **one repair round**: the planner is told what failed (for audio: call `speak_text` again with
`regenerate=true`, since the cache would otherwise return the same bad take). If it still fails, the status becomes
`partial` and `report.json` lists the failed check. A check skipped for budget reasons is reported as `skipped`, not
`passed`. Checks are heuristic: they cannot judge translation quality or voice naturalness (see limitations).

## 4. Budgets and recovery

- **Steps:** `--max-steps` counts planning (LLM) turns, checked before every LLM call.
- **Cost:** `--max-cost-inr`. Before each call a lower-bound estimate (characters for TTS, duration for STT, pages for
  OCR) is compared with what is left; a call that cannot fit is refused and not made. Costs come from tool results.
  LLM token cost is not counted.
- **Timeouts:** `--call-timeout-s` per tool call; a timeout becomes a retryable error.
- **On exhaustion:** deterministic `finish-budget-exhausted` (no LLM): status `budget_exhausted`, calls made, outputs
  produced but unverified, and what to raise.
- **API errors:** 429, 5xx and timeouts are retried with exponential backoff (1 s, 2 s, 4 s), at most 3 attempts, one
  Bodhan request at a time (the API reports a parallel limit). 413 or `truncated` halves the chunk and retries.
- **Partial failure:** if chunk N fails, the error names it and no partial result is returned as success.
- **Cost control:** content-hash cache per server process (the same chunk, window or page is never sent to the same
  model twice); `speak_text` is the dearest call, so the harness verifies after the planner is done rather than after
  each take, and `regenerate=true` is the only way to pay for a second take. `inspect_media` shows the estimated cost
  before anything is spent.

## 5. Untrusted content and impossible tasks

- Only the `Task:` line of the first message is an instruction. The system prompt says tool results are data.
- `find_injection` screens for instruction-like phrases at three points: text input files before the agent starts,
  the text going into `translate_text` / `speak_text` (inline or by `text_path`, checked before the paid call), and
  the text coming out of OCR, transcription, translation and file reads. **A hit halts the run**: the graph goes to
  the deterministic `finish-blocked` node (status `blocked`). No further LLM or tool call is made, the flagged result
  is never shown to the planner, and any artifact the flagged call wrote is deleted. The hit is recorded as a `security`
  line in `trace.jsonl`, in `report.json` `security_flags` and in a "Security notes" section in `result.md`.
  Trade-off: a file that merely *quotes* an injection-like phrase is refused too (false positives stop the task).
- The patterns are English. An injection written in an Indian language inside an image or audio is caught only once
  it is translated to English; adding Indian-language patterns would mean hand-writing Indian-language text.
  It is a tripwire, not a defence. The structural defence is that no tool can run commands, writes are confined to the
  output folder, and the LLM never needs the text content to decide what to call next (it passes paths).
- **Impossible tasks** (French or other unsupported language, video dubbing, missing file, unrelated task) end with
  `finish_task(status="impossible")`, an explanation and no artifact. `bodhan://languages` lists what each tool accepts.

## 6. Reproducibility and testing

- Pinned dependencies in `pyproject.toml`; config and prices in `config/config.yaml`; secrets only from environment.
- `tests/` covers chunking and limits per tool (mocked HTTP, no network), the MCP server over stdio and HTTP using the
  official MCP Python client, the agent loop with a scripted LLM, and untrusted-text handling.
- `evals/` is a golden set that runs the real agent with stubbed Bodhan tools (Rs 0) or live (capped).

## 7. Limitations

See the README. In short: verification is heuristic; the cache is per process; subtitle timing is distributed by text
length (no word timestamps from the API); chunks translate independently, so context is lost at chunk edges;
language-by-script cannot split languages that share a script (Devanagari) beyond a best guess.
