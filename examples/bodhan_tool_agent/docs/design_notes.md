# Design notes

Why the tools and the agent are shaped the way they are. The architecture is in [architecture.md](architecture.md), the agent loop in [agent_flow.md](agent_flow.md), and what the live API returns in [api_facts.md](api_facts.md).

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

## Where the LLM is used, and the fallback for each

| Decision point | What the LLM decides | If it fails |
|---|---|---|
| Plan | Deliverables (kind, language, duration), steps, assumptions for ambiguous wording | One schema-repair prompt, then a minimal fallback plan; the act loop works the task out step by step and records this as an assumption |
| Feasibility | Nothing: code checks languages against the model allowlists, catches unsupported targets named in the task ("into French"), and estimates minimum cost | n/a |
| Act loop | Which tool to call next, with what arguments; may call several in parallel | Malformed tool calls are re-requested. Tool calls written as JSON text are parsed (small local models do this). A provider without tool calling gets the tools described in text. 429s wait for the time the provider asks for |
| summarize_text | English summaries of English text, chunked to fit any length | Reported as a tool error; the loop can retry or give up honestly |
| Repair | How to fix a failed verification (one round) | Run ends as `partial`, listing what failed |
| Final answer | English explanation for the user | If it contains Indian-language text it wrote, the answer is withheld and the artifacts stand alone |

Code, not the LLM, decides: file discovery and inspection, language support, cost bounds, budgets, forced arguments (`out_dir`, `verify`), result compaction, verification, artifact selection, and every output file. The LLM never writes Indian-language text (enforced by a guard on every authored argument), never transcribes audio and never reads images.

## Context and rate-limit budget
Groq's free tier allows 8,000 tokens per minute. The agent sends compact tool specs to the LLM: the first paragraph of each description, trimmed parameter descriptions, `anyOf null` flattened. That is about 2,100 tokens instead of 3,100, while the server keeps full descriptions for third-party clients. Tool results older than the last 6 are folded into one-line summaries written by code.

## Untrusted content
OCR text, transcripts and text files are data. Previews reach the LLM only inside `<untrusted_content>` tags, the system prompt says to ignore instructions inside them, and summaries are produced in a separate LLM call that sees the text as data. A guard also rejects any attempt by the LLM to write Indian-language text, so injected content cannot be "rewritten" into outputs.
