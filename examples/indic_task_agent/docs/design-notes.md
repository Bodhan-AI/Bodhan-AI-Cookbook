# Design notes

## Why six tools

One tool per model would push chunking, retries and file plumbing onto the planner. A single "do the task" tool would hide control. The middle path: each tool is one *capability with its limit absorbed* (a whole PDF, a recording of any length, a document of any size), plus two free helpers (`probe_media`, `detect_language`) so nothing is bought blind. Files go in and out as paths, so audio, pages and Indic text never enter a model's context.

## Where the LLM is, and is not

The LLM plans (one JSON call, one repair), writes English prose (`write_text`) and judges English-to-English meaning. It never reads, writes or corrects Indic text: tools exchange files, arguments carrying Indic characters are refused, and the trace holds paths, codes and numbers only. Text inside any file is data; an inventory flag marks files that try to give orders.

## Plan, then execute

Python prices the plan (15 percent margin), reserves cost before every call, settles after, and stops cleanly at the step or rupee limit. Steps may carry:
- `when` — run only if a tool-reported code matches (translate only the clips detected as Telugu; the rest skip at no cost);
- `fit` — before speech is bought, measure the final-language text and redo the cheap upstream steps to hit a requested duration.

## Verification (status is derived, never claimed)

| Artifact | Checks |
|---|---|
| Audio | parses; duration vs prediction; back-transcription similarity; repeated 5-grams; requested duration |
| Translation | not truncated; digits preserved (with multiplicity); length ratio; table row consistency; round-trip meaning (English sources only, warns rather than fails) |
| English text | no Indian-script characters; length |
| SRT | cue count, order, line length |
| Plan | every promised final output exists, else `partial` |

## Recovery and cost

Retries with backoff and `Retry-After`; concurrency cap of 2 (4 produced 429s live); a timeout is never re-sent (it may already be billed); a marker file is left before each paid call; results are cached by content hash; `--resume` reuses the plan and finished steps.

## Found only by running it live

A clip shorter than the window was split at a pause; `write_text` wrote to the working directory; every refusal was labelled `UNSUPPORTED_LANGUAGE`; Tamil speech ran 22 percent over a two-minute request because speech length follows the final text; speech clips ran serially (153 s). Each is fixed and has a test.

## Limits

Subtitle timing is interpolated within each window. The meaning check cannot read Indic text, so Indic-to-Indic and Indic-source checks rely on structure, back-transcription and numbers. Speech-model repetition is detected, not repaired. Demo media is synthetic (made with Bodhan's own models) and cleaner than real recordings.
