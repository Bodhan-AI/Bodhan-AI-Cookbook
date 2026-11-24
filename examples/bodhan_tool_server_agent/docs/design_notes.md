# Design notes

Diagrams (Mermaid): [architecture.md](architecture.md) (system, server internals, per-tool limit handling, paid-call path, trust boundaries) and [agent_flow.md](agent_flow.md) (lifecycle, guards, a real sequence, status rules, trace). Two summary charts are also in the [README](../README.md#how-it-works).

## Tool list and why

13 MCP tools, one per model plus free helpers. Each model tool owns its limits, so a third-party agent never plans around chunk sizes.

| Tool | Model | Limit it hides |
|---|---|---|
| `transcribe_audio` | Indic-Transcribe | 30 s window, WAV/FLAC/OGG/MP3 only: ffmpeg to 16 kHz WAV, cut at silences into 28 s chunks (at the quietest point when a window has no silence), parallel calls, language detected from the first chunk and passed to the others only when no look-alike language (Hindi/Marathi) is possible, chunk-level segments |
| `translate_text` | Indic-Translate | output length (measured: a 2,737-char Hindi text went through in one request with no truncation, so the 1200-char chunk is a safe margin): sentence split, truncation (`finish_reason=length`) halves and retries, order kept, `mode=markdown` keeps headings and tables |
| `translate_segments` | Indic-Translate | subtitle cues translated one by one, timing kept |
| `synthesize_speech` | Indic-Speak | ~30 s per call (measured: the API cuts audio at 30.5 s without an error, 217 Hindi chars gave 16.6 s, 397 and 597 gave the 30.5 s cap): 220-char chunks, a warning if any chunk reaches 29.5 s, joined to one MP3, AI-generated ID3 tag, identical chunks synthesised once, `redo_chunks` for repair |
| `ocr_document` | Indic-OCR | one image per request, no PDFs: PDF pages rendered, EXIF/HEIC handled, rotated retry for empty-but-inked pages, partial result if budget ends |
| `inspect_media`, `detect_language`, `build_srt`, `list_languages`, `list_voices`, `estimate_cost`, `read_text_file`, `write_text_file` | none (free) | deterministic helpers |

Granularity: capability-level tools beat low-level chunk tools (noise for the planner) and one mega-tool (nothing to verify between steps). Files travel as paths inside a sandbox; no media is ever inlined into model context. Outputs are typed (pydantic, MCP `outputSchema`); errors are JSON (`code`, `message`, `hint`, `retryable`).

## Agent loop

Hand-written loop (no agent framework) over MCP stdio with any OpenAI-compatible LLM: preflight `inspect_media` on all files, `submit_plan` (interpretation, assumptions, languages), act through tools (parallel when independent), verify in code, finalize. The LLM plans, picks tools and writes English. Code decides status. The plan is mandatory: every other tool call is refused until `submit_plan` has been called, and a model that is refused four times ends the run as `failed` with nothing executed.

Why no framework: the brief scores orchestration judgment and what plain code does better than the LLM; budgets, verification and refusals are deterministic code, the hidden run uses their own LLM endpoint (no provider-only features), and every step stays visible in `trace.jsonl`. Cost: no built-in resume.

## Verification

- Generated audio is back-transcribed; character-trigram similarity, dropped phrases (3+ words, fail above 3%), a per-chunk comparison (a chunk with under 60 percent of its trigrams in the back-transcript counts as lost) and requested duration are checked. Chunks that lost words are re-synthesised alone once, then re-verified. A requested length that is missed is sent back to the model once before it finishes.
- Translations: truncation, script, length ratio, Markdown structure, back-translation coverage sample (English sources). Subtitles: cue count, timing, script.
- Transcripts: a looping chunk is re-transcribed once without silent edges; a Latin-script transcript with no language given gets a warning.
- Tool warnings reach `report.json` and `result.md` and downgrade `completed` to `completed_with_warnings`.

## Budgets

`--max-steps`, `--max-cost-inr`, per-call timeout (scaled by input size), wall clock. The cost cap is enforced inside the server: a ledger with reserve/release per paid call, so parallel calls cannot overshoot. The LLM is never shown the cap (it refused on cost by itself when it saw it). When money runs out the run stops as `budget_exhausted`, keeps what was paid for (e.g. 2 of 8 OCR pages), spends nothing more, and `result.md` lists done vs not done. A drained Bodhan balance is `INSUFFICIENT_CREDITS` (never retried).

## Error recovery

Backoff on 429/5xx/timeouts (5 tries, 5-40 s for rate limits), no retry on other 4xx, per-model concurrency limits, persistent disk cache, OCR 502 raises `max_tokens`, empty or failed LLM replies (a provider timeout or 5xx) retried up to 3 times, a model that stops calling tools ends the run as `partial`.

## Honest failure

Unsupported languages are refused by code from the Transcribe/Translate/Speak language lists (the plan names the languages; unknown names are never refused). An "unsupported" finish is accepted only for a reason code can verify (language, missing file, beyond the tools), never for cost. Missing files, encrypted PDFs and page-cap cases end with an explanation and no artifacts.

## Untrusted content

Tool previews are wrapped in `<untrusted_content>`; English and Hindi/Marathi injection phrases are flagged; Indic text (native script, or romanized Hindi by a marker-word heuristic) in LLM arguments, plan or summary is rejected; the agent has no shell and writes only inside the sandbox. Live runs on English and Hindi injection files translated the text as data and flagged it.
