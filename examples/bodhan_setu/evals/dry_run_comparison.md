# Bodhan MCP dry-run comparison

> Historical pre-fixture baseline. Missing-media outcomes below describe the earlier run and are superseded by `fixture_validation.md`. Translation/transliteration HTTP 404s and the prior cache behavior remain useful API observations.

## Summary

This comparison called the configured Bodhan MCP server directly with an MCP stdio test client for each of the 16 design-task records, once with `dry_run=true` where the tool supports it and once with `dry_run=false`. This evaluates server/tool behavior, not Copilot planning. Raw MCP responses are in [dry_run_comparison_raw.json](/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/evals/dry_run_comparison_raw.json).

| Measure | `dry_run=true` | `dry_run=false` |
|---|---:|---:|
| Task records attempted | 16/16 | 16/16 |
| Records with required fixtures available | 2/16 (video cases) | 2/16 (video cases) |
| Full task verifiers passed | 0/16 | 2/16* |
| Successful returned output for an otherwise valid model operation | 0 | 1 (video transcription, cache hit) |
| Paid provider errors | 0 | 3 HTTP 404s (2 transliterations, 1 translation) |

*The two non-dry verifier passes are the expected rejection of an unsupported transliteration script and video transcription returning a transcript. The transcript call was a cache hit, so no fresh transcription request or charge was demonstrated. The invalid-script case does not exercise a paid model operation.

The `dry_run=true` pass is a preflight comparison: by design it cannot satisfy transcript, translation, or audio artifact verifiers. It returned estimates for transliteration and video transcription, but all 16 full task completion verifiers are therefore unexecuted in that mode. The high-level video tool rejects both modes because the voice library is incomplete.

## Mode-specific observations

- **Dry run:** Hindi-to-Roman and Roman-to-Hindi transliteration estimates each returned ₹0.002 for 100 estimated output tokens and `will_call_api=false`. Video transcription estimated ₹0.01088 for 6.528 seconds. The video chain estimated transcription at ₹0.01088 and translation at ₹0.005 for 250 output tokens. Invalid `script="cyrillic"` was rejected before any API call. `translate_video` failed before estimating because the voice library is marked incomplete.
- **Without dry run:** both valid transliteration requests and the valid text translation request returned HTTP 404 (`{"detail":"Not Found"}`), with no output and no cost disclosed. Video transcription returned the Hindi transcript, but `cache_hit=true` and `cost_inr=0.0`; this is a cached result. In the video chain, transcription was also a cache hit and Tamil translation returned HTTP 404, so synthesis and muxing were skipped. Invalid Cyrillic script was rejected in both modes.
- **Fixture-dependent cases:** all audio, image, PDF, and text fixture paths required by 10 task records are absent from `evals/media/`. Both modes returned missing-file errors for those cases. The video fixture was available.
- **Manifest-only probe:** `cache_same_input_different_lang_01` used the text quoted in the media manifest because its referenced file is missing. This tested the endpoint only; it is not a valid execution of the benchmark task and is excluded from coverage and pass rate.

## Coverage and pass rate

- **Dry-run task coverage:** 0/16 full task verifiers. This mode evaluated cost estimates, argument validation, local extraction, and missing-input handling, not task completion.
- **No-dry-run task coverage:** 2/16 full task verifiers were applicable and completed (unsupported-script rejection and video transcription output). Both passed: **2/2 (100%) among those two verifiable cases**. Across the whole suite this is **2/16 (12.5%) verified passes**; 14 were not fully verified because inputs were missing, provider calls errored, or the voice library blocked the workflow.
- **Call/cost evidence:** non-dry calls were made for two transliterations, video transcription, video-chain transcription, and two translations. The four valid provider attempts that errored or were not cached disclose no cost; the two transcriptions returned cached results at ₹0.00. Actual billing for failed requests is unknown. No synthesis call ran. Dry-run estimates are estimates, not actual spend.

## Per-case results

| Case | With `dry_run=true` | With `dry_run=false` |
|---|---|---|
| `stt_ambiguous_language_01` | Missing WAV; no estimate | Missing WAV; no transcription |
| `ocr_table_translate_01` | Missing PNG; no estimate | Missing PNG; no OCR |
| `pdf_summary_audio_01` | Missing PDF; no estimate | Missing PDF; no OCR |
| `clip_filter_telugu_01` | Missing audio directory | Missing audio directory |
| `unsupported_french_01` | Missing WAV; target support untested | Missing WAV; target support untested |
| `ocr_injection_data_01` | Missing PNG; injection handling untested | Missing PNG; injection handling untested |
| `audio_chunk_boundary_01` | Missing WAV; no chunking | Missing WAV; no chunking |
| `cache_same_input_different_lang_01` | Manifest text probe estimated ₹0.0011 (not benchmark execution) | Manifest text probe returned HTTP 404 (not benchmark execution) |
| `transliterate_hindi_roman_01` | Estimate ₹0.002; no paid call | HTTP 404; no output or cost disclosed |
| `transliterate_hindi_native_01` | Estimate ₹0.002; no paid call | HTTP 404; no output or cost disclosed |
| `transliteration_unsupported_script_01` | Unsupported script rejected; no paid call | Unsupported script rejected; no paid call |
| `transcribe_translate_transliterate_01` | Missing WAV; no chain estimate | Missing WAV; no transcription |
| `ocr_translate_transliterate_01` | Missing PDF | Missing PDF |
| `video_transcription_01` | Estimate ₹0.01088; no model call | Hindi transcript returned from cache; `cost_inr=0` |
| `video_translation_dub_01` | Extraction succeeded; transcription estimate ₹0.01088; translation estimate ₹0.005 | Extraction/transcription succeeded (cached); translation HTTP 404; no synthesis or mux |
| `video_translation_high_level_01` | Blocked: incomplete voice reference library | Blocked: incomplete voice reference library |

No task, verifier, source, or expected-result records were modified. The report and raw-response JSON are the generated evaluation artifacts.

## API-reference follow-up

The linked Bodhan API reference documents `POST /translate` and `POST /transliterate` at `https://api.bodhan.ai`; these match the server's configured base URL and routes. It specifies that translation has no `source_language` request field and reports billed output usage as `usage.completion_tokens`. The server was updated to omit `source_language` from translation requests, avoid treating it as a cache-key input, and calculate translation/transliteration costs from `usage.completion_tokens` when present. A direct minimal request to each documented endpoint, using the configured base URL and bearer keys, still returned HTTP 404 with `{"detail":"Not Found"}`. Therefore the observed 404 is not explained by the extra source-language field; the configured API host or deployed route availability remains inconsistent with the reference. No additional paid retries were made after this check.
