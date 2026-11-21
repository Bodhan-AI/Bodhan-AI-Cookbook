# Design notes

## Tool granularity

**One tool per capability, with the model's limits handled inside.** I avoided both one tool per raw endpoint and one tool per task.

- Raw endpoints would push 30-second audio windows, two-sentence speech requests and page rendering onto the planner. That is exactly what the brief says callers should never handle, and a weak third-party LLM would get it wrong.
- Task-level macros ("pdf_to_tamil_audio_summary") would hide control and edge toward special-casing the demo tasks.

So `transcribe_audio` accepts an hour of MP4 and `ocr_document` an 8-page PDF, but chaining them is the planner's job. Four free helpers (`inspect_media`, `detect_language`, `read_text`, `estimate_cost`) exist only where the LLM would otherwise guess.

Every tool:
- takes file paths and returns `result_path`, so media never enters the LLM context;
- returns the same result shape;
- describes its limits, costs and next steps in its description, so a client that has never seen this agent can chain the tools from descriptions alone.

The limits are measured, not assumed ([probes/FINDINGS.md](probes/FINDINGS.md)). Bodhan silently drops audio past ~30 s in both transcription and speech, and silently truncates long translations (`finish_reason: "length"`). So the tools cut below those limits and re-split when truncation is detected.

## Where the LLM is used, and why

| Decision point | What it decides | Fallback if it fails |
|---|---|---|
| plan | The task as structured data: intent, languages, expected outputs, assumptions, steps | Re-ask once; then continue with an empty plan and record that |
| act (loop) | Which tool next, with which arguments; English summaries and explanations | No tool call: nudge twice, then treat its text as the final answer. LLM unreachable: stop and report `failed`/`partial` with the reason |
| finish | The English answer for `result.md` | Indian-language text in it is rejected once, then stripped |

Plain code does everything that must not depend on the grader's LLM: refusing impossible tasks (preflight), budgets, the Indian-language-text rules, duplicate detection, verification, repair and the final status. The LLM is reached only through Chat Completions with portable fields (`tools`, `tool_choice: "auto"`). Arguments are validated in code, and a malformed call costs one step with an actionable error, not a crash.

## The Indian-language rule, enforced in code

- The LLM **never sees** Indian-language text. Tool results are redacted to `<Indian-language text: Tamil, 312 characters…>` placeholders before they reach it. To understand a Hindi document, it calls `translate_text(target_language="en")` and reads the English.
- The LLM **never writes** it. Indian-language text in tool arguments is rejected, so the LLM must pass file paths. `write_text_artifact` accepts English only, and the final answer is checked too.
- Indian-language text in `result.md` is copied verbatim from the files Bodhan produced.
- Indian-language text inside the task itself is moved into a file before the LLM sees the task.

## Verification

| Output | Check | On failure |
|---|---|---|
| Generated speech | Transcribe back with indic-transcribe; character similarity ≥ 60% with the exact spoken text; duration within 0.4–2.5× the expected length | Re-synthesise with smaller requests (new input, not a resend), re-verify once |
| Translation (delivered and intermediate) | No truncated window; ≥ 60% of letters in the target script; plausible length ratio; tables kept their rows and columns | Re-translate with smaller windows, re-verify once |
| Subtitles | Well-formed cues, increasing times, text in every cue, covers ≥ 70% of the audio | Reported |
| OCR / transcription | No failed pages or segments | Reported |

If the budget can't cover verification, it is skipped and reported as skipped, never as passed.

## Budgets

- **Steps:** one step is one tool call the LLM chose. LLM calls are capped at 3 × steps + 10, to stop loops.
- **Cost:** before every call, code asks the server's `estimate_cost` for an upper bound, and rejects the call if it would push spend past `--max-cost-inr`. Speech estimates include the back-transcription. Preflight refuses outright when even the minimum cost is over budget.
- **Time:** a per-call timeout plus a wall-clock limit. The run stops cleanly with partial results when time runs out.

## Error recovery

- **Retries:** 429/502/503/504 and timeouts are retried with exponential backoff and jitter. A 500 is retried once only (the probes showed a bad voice causes a 500), and 400/401/422 and out-of-credit 429s are never retried.
- **Concurrency:** limited per model (4 requests at a time).
- **No waste:** a content-addressed SQLite cache means the same input is never sent to the same model twice, even across reruns, and concurrent identical requests share one call.
- **Partial failures:** a failed page or audio segment is marked in the output and reported, while the rest is kept.
- **Errors the LLM can fix:** they come back with a hint (for example, "translate first, then pass the output path").

## Untrusted content

File text reaches the LLM only inside tool results labelled as untrusted data. Text that looks like instructions ("ignore previous instructions…") is flagged in the result and in `report.json`. The agent's budgets, tools and status can't be changed by anything the model reads.

## What I chose not to do

- No self-hosting.
- No streamable-HTTP transport yet (it's a stretch goal).
- No resumable runs.
- No web UI.
- Transliteration is an optional extra, because the brief requires only four keys.
