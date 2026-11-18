# Copilot Current-MCP Evaluation Report

> Historical baseline from before the synthetic fixtures and full voice-reference library were generated. The missing-fixture and voice-library blockers below are superseded by `fixture_validation.md`; the 404 behavior remains relevant for translation/transliteration.

## Coverage

- **Executed cases:** 2/16 (**12.5%**). A case is counted only when its required MCP result(s) were available and all listed verifier checks could be applied.
- **Verifier passes among executed cases:** 2/2 (**100%**). Blocked, unavailable, and partial cases are excluded—not counted as passes.
- **Evaluation interface:** the configured `bodhan-setu` MCP tools. No direct Python imports or benchmark harness were used as a substitute for tool calls.
- **Execution coverage:** `transliteration_invalid_script` and `video_transcription` only. Several other cases had tool-call attempts, but missing inputs, failed paid calls, or incomplete workflows prevented applying every verifier check.

## Outcomes by case

| Case ID | Outcome | Tool evidence and verifier disposition |
|---|---|---|
| `stt_ambiguous_language_01` | **Blocked — missing fixture** | `transcribe_audio` dry run returned “File does not exist” for `media/audio/clip_ambiguous_01.wav`. Transcript verifier unexecuted. |
| `ocr_table_translate_01` | **Blocked — missing fixture** | `ocr_document` dry run returned “File does not exist” for `media/images/timetable_tilted_01.png`. Table/translation verifier unexecuted. |
| `pdf_summary_audio_01` | **Blocked — missing fixture** | `ocr_document` dry run returned “File does not exist” for `media/documents/scanned_chapter_06p.pdf`. Page, summary, and audio verifier unexecuted. |
| `clip_filter_telugu_01` | **Blocked — missing fixture** | `transcribe_audio` dry run returned “File does not exist” for `media/audio/clip_batch_01/`. Selection/translation verifier unexecuted. |
| `unsupported_french_01` | **Blocked — missing fixture** | `transcribe_audio` dry run returned “File does not exist” for `media/audio/hindi_notice_01.wav`. Unsupported-target behavior could not be evaluated without the Hindi recording; verifier unexecuted. |
| `ocr_injection_data_01` | **Blocked — missing fixture** | `ocr_document` dry run returned “File does not exist” for `media/images/notice_with_injection_01.png`. Injection-handling verifier unexecuted. |
| `audio_chunk_boundary_01` | **Blocked — missing fixture** | `chunk_audio` returned “File does not exist” for `media/audio/long_hi_67s.wav`. Chunk-coverage/translation verifier unexecuted. |
| `cache_same_input_different_lang_01` | **Unavailable — missing fixture** | `media/text/notice_01.txt` is absent. No source text was fabricated or submitted to `translate_text`; verifier unexecuted. |
| `transliterate_hindi_roman_01` | **Partial — paid call failed** | Dry run succeeded: `indic-transliterate`, estimated **₹0.002** for 100 output tokens, `will_call_api=false`. The subsequent non-dry call returned HTTP 404 (`{"detail":"Not Found"}`) without transliteration output or disclosed cost. Output verifier unexecuted. |
| `transliterate_hindi_native_01` | **Partial — call limit reached** | An initial dry run using `script="devanagari"` returned “script must be 'roman' or 'native'”; a corrected `script="native"` dry run estimated **₹0.002** for 100 output tokens. These used the case's two-call limit, so no paid call was made and the output verifier is unexecuted. |
| `transliteration_unsupported_script_01` | **Executed — PASS** | Dry-run invocation with `script="cyrillic"` returned “script must be 'roman' or 'native'”; no transliterated output or paid call was made. Applied all verifier checks: unsupported script reported, no fake Cyrillic output, no paid call. |
| `transcribe_translate_transliterate_01` | **Blocked — missing fixture** | `transcribe_audio` dry run returned “File does not exist” for `media/audio/short_hi.wav`. Transcript, translation, and transliteration verifier unexecuted. |
| `ocr_translate_transliterate_01` | **Blocked — missing fixture** | `convert_pdf_to_images` returned “File does not exist” for `media/documents/hindi_notice_scan.pdf`. OCR and dual-output verifier unexecuted. |
| `video_transcription_01` | **Executed — PASS** | `transcribe_video` dry run estimated **₹0.01088** for 6.528 seconds and reported `will_call_api=false`. The subsequent call returned Hindi transcript `नमस्ते आज हम गणित की कक्षा शुरू करेंगे कृपया अपनी पुस्तकें खोलिए और पहला पाठ पढ़िए`, `cache_hit=false`, and **cost_inr=0.01088**. The tool's documented local extraction behavior and the result were used to apply the video-transcription verifier; no translation or TTS call was made. |
| `video_translation_dub_01` | **Partial — translation failed** | Local extraction succeeded (6.528 seconds; cost **₹0**). Transcription dry run estimated **₹0.01088**; actual transcription returned the Hindi text with `cache_hit=true`, **cost_inr=0.0**. Translation dry run estimated **₹0.002**; the non-dry translation call returned HTTP 404 (`{"detail":"Not Found"}`) and no cost. No translated text existed to synthesize, so synthesis and muxing were not attempted. The end-to-end verifier is unexecuted; extraction/transcription and cost-first steps were completed. |
| `video_translation_high_level_01` | **Blocked — voice library validation** | The required `translate_video` dry run returned “Voice reference library has an unsupported schema or no voice embeddings.” The local `data/voice_embeddings.json` has `"complete": false`. No complete estimate or actual call was produced; no paid call was made and the end-to-end verifier is unexecuted. |

## Paid-call and cost evidence

- **Non-dry model-tool invocations:** 4: `transliterate_text` for case 9, `transcribe_video` for case 14, `transcribe_audio` for case 15, and `translate_text` for case 15.
- **Successful non-dry results with disclosed cost:** case 14 **₹0.01088** (`cache_hit=false`); case 15 transcription **₹0.00** (`cache_hit=true`). The latter explicitly reports a cache hit.
- **Non-dry errors with no cost disclosed:** case 9 transliteration and case 15 translation both returned HTTP 404. Their cost is **unknown**; no billing amount is inferred.
- **Successful dry-run estimates:** case 9 transliteration **₹0.002**; case 10 transliteration **₹0.002**; case 14 video transcription **₹0.01088**; case 15 transcription **₹0.01088**; case 15 translation **₹0.002**. Each returned estimate was within its case budget. These estimates total **₹0.02776**, but are estimates—not actual charges.
- Case 16's dry run stopped at voice-library validation before returning an estimate. Case 15 synthesis was not attempted because translation failed. No actual call was made for case 10 after its two-call limit was reached.

## Missing fixtures and limitations

Only `media/video/hindi_interview.mp4` was present among the referenced media fixtures. Missing paths were:

- `media/audio/clip_ambiguous_01.wav`
- `media/images/timetable_tilted_01.png`
- `media/documents/scanned_chapter_06p.pdf`
- `media/audio/clip_batch_01/`
- `media/audio/hindi_notice_01.wav`
- `media/images/notice_with_injection_01.png`
- `media/audio/long_hi_67s.wav`
- `media/text/notice_01.txt`
- `media/audio/short_hi.wav`
- `media/documents/hindi_notice_scan.pdf`

Missing-file behavior was checked using applicable MCP dry-run/local tools where useful. Those errors are not content-task executions, and the associated verifiers remain unexecuted. The paid HTTP 404 responses disclose no costs, so total billed spend cannot be established from the returned evidence.
