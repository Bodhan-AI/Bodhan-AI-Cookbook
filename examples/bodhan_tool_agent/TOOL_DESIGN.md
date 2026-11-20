> The rationale for the MCP tool surface. This file is one of the three documents a reviewer will read first (SPEC, ARCHITECTURE, this).

## Principle

A tool must be usable by a **third-party planner that has never seen this codebase**, from the tool's description and schema alone. That means three properties, in priority order:

1. **Correct on bad input.** Every tool returns a `ToolErrorPayload` for every invalid input the schema allows. No exceptions propagate.
2. **Limit-absorbing.** The caller passes a file; the tool handles chunking, pagination, retries, and remapping internally. Callers never think about 30 seconds or 32K tokens.
3. **Composable.** The output of one tool must be directly usable as the input to another without human translation. Transcripts have segment timestamps because SRT needs them. OCR returns Markdown so translate can preserve structure.

## Granularity decision

Three candidate shapes were considered:

| Shape | Pros | Cons | Verdict |
|---|---|---|---|
| One tool per **model** (4 tools total) | Simple schemas | Planner must chunk audio and paginate PDFs → expensive, unreliable | Rejected |
| One tool per **task** (`do_summary`, `make_subtitles`) | Tiny planner | Hides retries, verification, and cost control; can't be composed for unseen tasks | Rejected |
| **Model-level + plumbing helpers** (8 tools) | Planner sees decision points; limits stay inside tools; helpers prevent planner reimplementing loops | Slightly larger surface | **Chosen** |

The eight tools are the minimal set that lets a planner express every task family in SPEC §12 without writing plumbing.

## Tool catalogue

### `transcribe_audio(input_path, language_hint=None)`
- **For:** any audio → transcript, with segment timestamps.
- **Not for:** identifying language only (though it returns `language`); use a cheap call if you only need ID.
- **Absorbs:** chunking at pauses (≥250 ms silence, target 25 s, hard 30 s), timestamp remapping to global, retries, cost aggregation.
- **Returns:** `transcript`, `language`, `language_confidence`, `segments[{start_s,end_s,text}]`, `duration_s`, `cost_inr`.
- **Errors:** `FILE_NOT_FOUND`, `UNREADABLE_MEDIA`, `UNSUPPORTED_LANGUAGE`, `UPSTREAM_*`.
- **Cost:** ₹0.10 × audio-minutes.
- **Note:** language ID is unreliable for Hindi (~0.26), Bhojpuri (~0.05), Maithili (~0.36), Urdu (~0.49) — the returned `language_confidence` must be surfaced to the agent, not hidden.

### `translate_text(text, source_lang, target_lang)`
- **For:** translating text, preserving Markdown structure (headings, lists, tables).
- **Not for:** generating Indic text from scratch (that's TTS text prep — do it by translating English).
- **Absorbs:** 32K-context pagination split on structural boundaries with overlap; never splits inside a Markdown table; never splits an equation; sequential call with continuation.
- **Returns:** `translated`, `cost_inr`.
- **Errors:** `UNSUPPORTED_LANGUAGE`, `INPUT_TOO_LONG` (with current tokens + suggestion to chunk upstream), `INVALID_ARGUMENT` (missing src/tgt).
- **Cost:** ₹0.20 × output-token-10K.

### `synthesize_speech(text, language, voice_id)`
- **For:** TTS on short inputs.
- **Not for:** voice cloning (unsupported by model) or English (English not in the model's supported voices for most recipes — check `SUPPORTED_LANGS`).
- **Absorbs:** pre-call character cap; AI-generated label written into the output file metadata **and** a sidecar `.ai.txt`.
- **Returns:** `path`, `duration_s`, `chars`, `ai_generated=True`, `cost_inr`.
- **Errors:** `UNSUPPORTED_LANGUAGE`, `INPUT_TOO_LONG` (with cost estimate for the requested size), `INVALID_ARGUMENT` (voice not in library for language).
- **Cost:** ₹6.00 × chars / 10K — **the dominant cost in the system.** Never call redundantly.

### `ocr_page(image_path)`
- **For:** a single page or image → Markdown.
- **Not for:** multi-page documents (use `ocr_document`).
- **Returns:** `markdown`, `page`, `cost_inr`.
- **Errors:** `FILE_NOT_FOUND`, `UNREADABLE_MEDIA`.
- **Cost:** ₹0.20 per image.

### `ocr_document(pdf_path)`
- **For:** multi-page PDFs. Loops `ocr_page` per rendered page at 200 DPI and assembles in reading order.
- **Not for:** single images (`ocr_page`).
- **Returns:** `markdown`, `pages`, `cost_inr`.
- **Errors:** `FILE_NOT_FOUND`, `UNREADABLE_MEDIA`.
- **Cost:** ₹0.20 × pages.

### `inspect_media(path)`
- **For:** cheap, local metadata before deciding what to do — kind, duration, pages, format, size.
- **Not for:** content extraction.
- **Returns:** `kind` (`audio|image|pdf|text|video|unknown`), `duration_s`, `pages`, `format`, `size_mb`.
- **Errors:** `FILE_NOT_FOUND` only.
- **Cost:** free (no model).

### `chunk_audio(path)`
- **For:** third-party clients that want to drive chunking themselves.
- **Not for:** anything `transcribe_audio` already does — it chunks internally.
- **Returns:** list of `{path, start_s, end_s}`.
- **Errors:** `FILE_NOT_FOUND`, `UNREADABLE_MEDIA` (per Standing Rule F4).
- **Cost:** free.

### `get_voice_library()`
- **For:** discovering valid `voice_id` values per language before `synthesize_speech`.
- **Returns:** `{language: [voice_id, …]}`.
- **Also exposed as MCP resource** (`voices://library`).
- **Errors:** none (per Standing Rule F4).
- **Cost:** free.

## Media plumbing rules

- **Path in, path out.** Tools accept a file path and return a file path. Never inline base64 in schemas, results, or error details.
- **No shared temp dirs assumed.** Each tool reads/writes under a caller-provided directory or under `out/artifacts/` (the agent sets this).
- **Never trust a caller path.** Validate that the resolved absolute path exists, is readable, and (for the agent) is inside `--files`. Reject with structured error, never raise.
- **Clean up intermediate chunks** when a run finishes unless the caller asked for them via `chunk_audio`.

## Testing each tool

Every tool needs, at minimum:

- Happy path with a valid fixture.
- Bad path → `FILE_NOT_FOUND`.
- Bad media (empty file, wrong format) → `UNREADABLE_MEDIA`.
- Unsupported language (for language-taking tools) → `UNSUPPORTED_LANGUAGE`.
- Size limit exceeded (for `translate_text`, `synthesize_speech`) → `INPUT_TOO_LONG` with the correct payload.

Limit-absorbing tools additionally need:

- `transcribe_audio`: chunk boundary on a 3-min fixture; timestamps monotonic and match global; no data loss.
- `translate_text`: 40K-token fixture split correctly; table never split; overlap preserved.
- `ocr_document`: 3-page fixture → 3 calls to the underlying OCR (countable via mock); order preserved.

## Anti-goals

- Do **not** add a `do_everything` tool.
- Do **not** add file-type dispatch in tools (that's `inspect_media` + `resolver`'s job).
- Do **not** return partial data + an error; return either a complete result or a complete error.
- Do **not** leak stack traces. Errors are `{code, message, hint, details}`, and `details` must not contain API keys, full paths outside `--files`, or user content.
