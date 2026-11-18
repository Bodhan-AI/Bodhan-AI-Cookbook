# Design notes

To be filled in as each part is built. Sections:

## Tool granularity
One tool per model capability, with that model's limits handled inside the tool, plus small free helpers (`inspect_media`, `make_srt`, `read_text`, `translate_segments`, `supported_languages`, `list_voices`). There are no task-level tools such as "make an audio summary": planning stays with the agent, where it can be seen and traced, and no caller ever has to chunk audio, text or pages.

Specific choices, each driven by what the live API does ([api_facts.md](api_facts.md)):
- **Transcription windows** cut at the quietest point between 2/3 of the limit and 29.5 s. A hard cut (no silence found) gets a 0.6 s overlap, and the duplicated words are removed when the text is stitched back together. Silent windows are skipped. The API returns no timestamps, so segment times come from our own cuts, and the `subtitle` style simply uses windows of 7 s or less. That costs the same, because billing is per audio second.
- **Language detection** uses Unicode script only, which the rules allow. Devanagari and Bengali script are flagged as ambiguous, so the caller can decide.
- **Tables** are translated cell by cell, because sent whole the model dropped the `|---|` separator row.
- **Speech** is packed into pieces of about 250 characters at sentence ends, matching the documented guidance of "a sentence or two".
- **PDFs** are always read with OCR, even digital ones, so all text extraction goes through a Bodhan model.

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
