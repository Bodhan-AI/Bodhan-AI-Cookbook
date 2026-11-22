# Live validation results — 22 November 2026

> Evidence is based on local run reports, terminal logs and manually reviewed text shared during development. These are not hidden-set results. No media or API secrets are stored in the public repository.

## Environment and independent client

- Windows / PowerShell, Python in `.venv`, ffmpeg installed after initial failures.
- 37 local automated tests passed in the Windows development project. Cookbook single-recipe validator returned zero blocking findings in the submission clone (two non-blocking Groq URL warnings for the *general-purpose agent LLM*).
- MCP Inspector `tools/list`: nine tools discovered.
- MCP Inspector `tools/call inspect_files` on `sample_data`: two files returned, `ok: true`.
- MCP Inspector `tools/call read_text`: 129-character synthetic English paragraph returned, `ok: true`.
- MCP Inspector `tools/call ocr_document`: `pages_ok: 1`, `pages_failed: 0`, `isError: false`; Markdown table and mathematical expression extracted. `needs_review: true` for two low-confidence **empty** header/footer layout blocks.

## Live task results

| Scenario | Outcome | Measured evidence | Caveats |
| --- | --- | --- | --- |
| English classroom paragraph → Bengali female-voice narration | **Completed** | 7 agent steps, 19.39 s, estimated ₹0.1071, ~12.032 s WAV. Indic-Speak selected voice Ishita; back-ASR character similarity 0.975. | Similarity is not human speech-quality scoring. |
| Hindi WAV → English transcript and subtitle SRT | **Completed** | 19 agent steps, 121.67 s, estimated ₹0.0266. One SRT cue spans `00:00:00,000`–`00:00:06,485`; correct English sentence. | Coarse single-window cue, not word-aligned; two recoverable tool errors and redundant request in trace. |
| Scanned worksheet → Marathi Markdown | **Partial** | Agent OCR succeeded but subsequent translation hit rate limits and a misleading `completed` status. Later a **direct MCP translation** generated Marathi Markdown, followed by offline checks passing numbers, table shape, numeric cells and LaTeX. | Not a successful *autonomous agent* completion. Original generated QA showed `numbers_preserved: false`, while later document audit passed; human Marathi review still pending. |
| Unsupported French translation (text input) | **Expected refusal** | `not_completed`, 1 step, 1.14 s, ₹0, zero artifacts and tool events. | Exact assignment impossible-video example not yet run. |
| Tamil narrated summary from scanned chapter | **Not demonstrated** | — | Requires suitable rights-cleared scanned PDF and live run. |
| Identify Telugu among five clips and translate those | **Not demonstrated** | — | Requires five rights-cleared audio files and live run. |

## Translation endpoint incident

The originally documented `POST /translate` returned HTTP 404 for a valid test. An alternate Bodhan model route at `POST /v1/chat/completions` (`model=indic-translate`) succeeded; a short live Bengali diagnostic returned 22 output characters. This is still **Bodhan** translation, not a general-purpose LLM translation fallback. Later longer Marathi requests encountered HTTP 429, demonstrating the need for throttling, bounded retries, and honest partial status.

## Verification limitations and recommendations

1. Report both application status and actual artifacts. A `completed` status is insufficient when a required translation was never produced.
2. Treat numeric and table-format validators as structural tests, **not** proof of correct Marathi meaning.
3. Back-ASR similarity and backtranslation score are diagnostic metrics, not native speaker review.
4. Before final PR, link real demo media and traces from an accessible public location, without API keys or private data.
5. Record a public ≤5-minute demo of real runs (and the impossible request). The video URL is not yet supplied.
6. Run the full repository's `make check` and confirm `git diff --cached --check` in the final PR branch.
