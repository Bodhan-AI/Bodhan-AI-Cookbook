# Design notes

## Goals and the rules that shape everything

1. Every speech-to-text, translation, speech synthesis and OCR operation goes to a Bodhan model, through the MCP tools.
   There is no other ASR, MT, TTS or OCR anywhere, not even as a fallback.
2. The LLM never writes Indian-language text. This is enforced in code, not by prompt alone (see "Guards").
3. Status comes from checks, not from the LLM's claim. Impossible tasks end with an explanation and no files.
4. A small, reliable tool set beats a large one.

## Tool granularity

Three options were considered:

| Option | Example | Problem |
| --- | --- | --- |
| One tool per raw endpoint | `transcribe_window(wav ≤ 30 s)` | Every caller must chunk audio, split sentences, render PDFs. Third-party clients get it wrong. |
| One tool per task | `make_audio_summary(pdf, lang, minutes)` | Hides control; fails on any task shape not foreseen; untestable against hidden tasks. |
| **One tool per model capability, limits handled inside** | `transcribe_audio(path, language?)` | Chosen. |

Each of the four model tools owns its model's limits (30 s windows, short TTS inputs, translation size and silent
truncation, one image per OCR request, no PDFs) and its own self-check. The planner still decides order and
arguments, which is what lets one agent handle summaries, subtitles, read-alouds and language identification without
code changes. `translate_segments` exists separately from `translate_text` because subtitles need timings carried
through translation, which plain text loses. Helpers (`inspect_file`, `read_text`, `write_text`, `write_subtitles`)
are free and local; `write_text` gives the LLM a sanctioned way to put English it wrote into the pipeline.

Media moves only as paths. Results carry a 500-character preview at most, so a 30-page OCR job or a 15-minute
transcript never floods the context; `read_text` pages through files when the LLM needs more.

## Verification

| Output | Check | Where |
| --- | --- | --- |
| Speech | Each chunk is back-transcribed with Indic-Transcribe; character error rate against the chunk text; repetition (back-transcript much longer than the text, or repeated 4-grams) and omission (much shorter) are flagged | `synthesize_speech` |
| Speech recovery | A flagged chunk is re-split into smaller pieces and re-synthesized once; the take with lower CER is kept; what remains flagged is reported, never hidden | `synthesize_speech` |
| Translation | Numbers in source and output compared after mapping all Indic digit sets to ASCII; share of letters in the target script; output/source length ratio; `truncated` flag | `translate_text` |
| Tables | Row and column counts of each Markdown table before and after; a broken grid is rebuilt cell by cell | `translate_text` |
| OCR | Low-confidence blocks, empty pages, garbled text, per page | `ocr_document` |
| Deliverables | Exists and non-empty; audio was produced with verification on and has no flagged chunk; translations pass the checks above; SRT parses with ordered timestamps; a duration named in the task ("two-minute") is met within ±25% | `bodhan_agent/verify.py` |
| Task fit | An LLM judge reads the task, answer, deliverable metadata and short previews and lists concrete problems | `agent._judge` |

Any failure goes back to the LLM once with the exact problems. After that the run ends as `partial`, and the
problems are listed in `result.md` and `report.json`.

## Budgets

- **Steps:** every tool call counts; identical repeat calls are answered from memory and do not count.
- **Rupees:** one ledger is shared by the agent and the server. Every Bodhan call reserves its estimated cost first
  and is refused (`budget_exceeded`) if that would pass `--max-cost-inr`. Whole jobs (a narration, a PDF, a long
  recording) are estimated up front, so a job that cannot fit is refused before any part of it is paid for.
- **Time:** `--max-minutes` for the run and `--tool-timeout-s` per tool call; HTTP timeouts per Bodhan request.
- **No waste:** every response is cached under a SHA-256 of model, endpoint, parameters and input bytes, so the same
  input is never sent to the same model twice, within a run or across reruns. Cache hits are counted in `report.json`.

## Error recovery

| Failure | Response |
| --- | --- |
| 429, 5xx, timeouts | Exponential backoff with jitter, honouring `Retry-After`; at most 4 retries per request |
| Bad arguments | Structured error with a hint; the LLM fixes and retries |
| Translation truncated | Re-sent in halves, up to 3 levels |
| OCR 502 (block too long) | The page is retried as two overlapping halves; a page that still fails is flagged, the rest continue |
| TTS repeats or omits | Re-split and re-synthesize once, keep the better take, report the rest |
| LLM rejects `tools` or writes tool calls as text | Switch to a JSON-in-text tool protocol and keep going |
| LLM unavailable | Retries with backoff, then the run ends as `failed` with partial outputs, trace and report written |

## Where the LLM decides

| Decision | Why the LLM | Fallback |
| --- | --- | --- |
| Plan: feasibility, assumptions, steps, target languages | Ambiguous wording, unstated languages | One retry asking for JSON only; then act without a written plan and record that |
| Next tool and arguments | Unseen task shapes | Structured tool errors it can correct |
| Choosing among Devanagari or Bengali-script languages when a clip's language is ambiguous | Reading the transcript and the task context | Script default (Hindi, Bengali) recorded as an assumption |
| English summaries and explanations | Its real strength | n/a |
| Hitting a length target | Draft English, measure the real audio, adjust once | Accept within ±25% (the verification threshold) and report the miss |
| Whether the task is satisfied | Catches "answered a different question" | Code checks alone decide the status |

Everything else is deterministic code: inputs, file types, language support, chunking, budgets, number and script
checks, SRT timing, final status.

## Guards and untrusted content

- Any free-text argument the LLM writes (`text`, `content`, the final `answer`) that contains an Indic script is refused
  with an explanation. Paths, language codes and voice names are exempt. So every Indian-language word in the output
  provably came from Indic-Translate, Indic-Transcribe or Indic-OCR.
- File contents, transcripts and OCR text reach the LLM only inside `<untrusted_content>` tags. A pattern check flags
  instruction-like text ("ignore previous instructions", requests for keys or email), adds a warning inside the tag and
  records it in `report.json`.
- The agent has no shell, network, delete or email tool, and writes only into the run's output folder; the server
  reads only from allowed folders. An injected instruction has nothing dangerous to call.

## Language support

Language codes and names resolve through one table mirroring the Cookbook's `scripts/bodhan_api_rules.json`. A task
asking for output in a language no model supports ("into French") is refused in preflight. Indic-Speak's preview-tier
languages work but produce a quality warning.

## What the live runs changed

Offline tests passed from the start; the hosted API and a real LLM still found fifteen problems. Each one became a fix
and, where possible, a regression test built from the real output.

| Found in a live run | Fix |
| --- | --- |
| The documented `POST /translate` returns 404 on the hosted service | A probe showed Indic-Translate answers on `/v1/chat/completions` (text as one user turn, target language as a top-level field); the client uses that route and reads `finish_reason: length` as truncation |
| Groq's free tier caps requests at 8K tokens per minute; the client gave up after 15 s while Groq asked for 19 s | The LLM client waits as long as the provider asks (Retry-After or "try again in Ns") |
| Requests grew past 8K tokens as tool results piled up (413) | Shorter tool descriptions and compact schemas for the LLM (about 2,800 to 1,700 tokens), only the newest three tool results in full, and a compact-and-retry on 413 |
| A run that lost its LLM after making the MP3 reported "failed" with no files | Outputs already made are delivered as partial, with their automatic checks |
| The model skipped one of five input clips | finish checks that every input file was used or named in the answer |
| Indic-Speak reads "180°" as "one hundred and eighty degrees"; plain CER flagged correct speech as repetition | Number-aware alignment: each number is a slot that accepts any spoken form; repeats and gaps elsewhere still count |
| The CER normaliser stripped Indic vowel signs (Python's `\w` does not match combining marks) | Normalisation keeps letters, marks and digits |
| The model re-ran speech with `verify=false` to get past a warning | The agent forces verification on; the prompt says checks are never worked around |
| The planner never saw the tool list, invented `summarize_text`, and the agent gave up | The planner gets the real tool catalogue; the prompt says summarising is the LLM's own job; a give-up for a "missing capability" is challenged once |
| The judge objected to a Marathi term the agent may not edit, causing a 15-call loop | The judge reports only fixable problems; wording notes go to result.md for a human |
| Reading a translation with read_text replaced the record of who produced it, so its checks were skipped | Read-only tools never overwrite file provenance |
| The Tamil summary was synthesized three times (₹4.70) while the model corrected its length after hearing it, and a later run kept retrying because the prompt and the check disagreed on the tolerance (20% vs 25%) | translate_text reports `estimated_speech_s` so length is fixed before paying for speech; one 25% tolerance everywhere; a warning when few tool calls remain; outputs are salvaged on a budget or step stop |
| Indic-Speak silently skipped a bracketed Tamil phrase on both takes; back-transcription caught it and the run ended partial | Brackets become pauses before synthesis; the real chunk is a regression test |
| Per-page OCR files lost their page number (`.page01` was read as a file extension), so the model mistook `chapter_2.md` and the rest for drafts and spent ten tool calls on them | Pages are saved as `chapter_page01.md` and so on; the OCR test checks the names |
| A Tamil summary written with angle labels ("∠AOC = 125°") was read correctly, but the speech check flagged every such chunk: the voice says the labels and "=" as English words in Tamil script, which no spelling of the symbols matches. The model then re-synthesized the same text with four voices (₹6.17) | Labels and symbols outside the text's own script become slots like numbers, each with a budget that absorbs a spoken label but never a repeated sentence; the prompt asks for maths in words in text meant for speech and forbids re-synthesizing flagged text with another voice |

## What I would do next

- Word-level subtitle timing by aligning back-transcripts of sub-windows.
- A video dubbing tool (mux translated speech back into the video), since "translate this video" is a natural ask.
- A judge-consistency check (judge twice, compare) and a small labelled set to calibrate the CER threshold per language.
- Streamable HTTP transport with token auth, and a small web UI that shows the live trace.
