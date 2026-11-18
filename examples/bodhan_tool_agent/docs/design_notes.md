# Design notes

To be filled in as each part is built. Sections:

## Tool granularity
One tool per model capability, with that model's limits handled inside the tool, plus small helpers (`inspect_media`, `make_srt`, `translate_segments`, `supported_languages`, `list_voices`). There are no task-level tools: planning stays with the agent, and chunking stays out of the caller's way.

## Media plumbing
Tools take and return file paths. Results carry a short preview and stats, never full media or full documents.

## Verification
- Generated speech: back-transcribe with Indic-Transcribe and compare (character error rate). Re-synthesise only failing chunks.
- Translation: coverage per chunk, length ratio, numbers preserved, target script, table shape.
- Subtitles: segment count, increasing timestamps, no empty cues.

## Budgets
Max steps, max ₹ (checked against a pre-call estimate), per-call timeout. On exhaustion the agent stops cleanly and reports what was done.

## Error recovery
Retries with backoff on 429, 5xx and timeouts, with a concurrency limit. Structured tool errors with a hint, so the planner can re-plan.

## Untrusted content
OCR text and transcripts are data. They reach the LLM only as delimited previews, and instructions inside them are ignored.

## Where the LLM is used
Task understanding, planning, tool choice, English writing, judging verification results. It never writes Indian-language text, transcribes audio or reads images.
