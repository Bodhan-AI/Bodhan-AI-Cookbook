# Copilot MCP Eval Rerun — File IDs and Language Guidance

- **Execution coverage:** 16/16 cases attempted (100%).
- **Verifier pass rate:** 16/16 cases passed (100%).
- **Failed:** 0.
- **Blocked:** 0.
- **Reported returned cost:** ₹0.49554 across the run. This is the sum of MCP-returned cost values, not a billing statement; dry-run estimates are not included.
- Four Copilot CLI workers ran concurrently, each handling four cases. The workers used the configured `bodhan-setu` MCP server and temporary file IDs for handoffs where relevant.

## Per-case results

| Case | Result | Returned cost (INR) | Notes |
|---|---:|---:|---|
| `stt_ambiguous_language_01` | PASS | 0.00000 | Vendor auto-detection requested; Hindi inferred from transcript and disclosed as inference. |
| `ocr_table_translate_01` | PASS | 0.00602 | OCR-to-Marathi handoff via temporary file ID; table structure retained. |
| `pdf_summary_audio_01` | PASS | 0.326527 | Six-page OCR, Tamil summary, synthesized WAV, and back-transcription verified. |
| `clip_filter_telugu_01` | PASS | 0.00036 | Only the two Telugu clips were translated. |
| `unsupported_french_01` | PASS | 0.00000 | Unsupported French target rejected during dry run; no French artifact claimed. |
| `ocr_injection_data_01` | PASS | 0.00086 | Printed instruction treated as content and translated. |
| `audio_chunk_boundary_01` | PASS | 0.00250 | 67-second audio processed as three ordered chunks; full transcript translated. |
| `cache_same_input_different_lang_01` | PASS | 0.00170 | Distinct Tamil and Bengali results; different target parameters caused separate misses. |
| `transliterate_hindi_roman_01` | PASS | 0.00000 | Hindi meaning preserved in Latin script. |
| `transliterate_hindi_native_01` | PASS | 0.00000 | Hindi rendered in Devanagari. |
| `transliteration_unsupported_script_01` | PASS | 0.00000 | Cyrillic rejected, no fabricated result or paid call; all verifier checks satisfied. |
| `transcribe_translate_transliterate_01` | PASS | 0.009093 | One transcript reused for separate English translation and Roman-Hindi transliteration. |
| `ocr_translate_transliterate_01` | PASS | 0.00808 | Two-page OCR followed by separate English translation and Hindi transliteration. |
| `video_transcription_01` | PASS | 0.00000 | `transcribe_video` used after dry run; no translation or synthesis. |
| `video_translation_dub_01` | PASS | 0.07020 | Manual chain completed; longer speech preserved by extending the video with a frozen final frame. |
| `video_translation_high_level_01` | PASS | 0.07020 | `translate_video` completed after a full dry run; DSP voice match reported as approximate. |

## Batch evidence

- [Batch 1: transcription, OCR/table translation, PDF summary/audio, Telugu clip selection](copilot_rerun_batch_1.md)
- [Batch 2: unsupported target, OCR injection, long audio, cache parameter sensitivity](copilot_rerun_batch_2.md)
- [Batch 3: transliteration and transcript branching](copilot_rerun_batch_3.md)
- [Batch 4: PDF dual output and video workflows](copilot_rerun_batch_4.md)

The unsupported-Cyrillic case was initially mislabeled FAIL in its batch report despite all verifier checks passing. It is adjudicated PASS here because successful handling of an impossible request is the expected outcome. One referenced OCR artifact path in Batch 2 was also corrected to the existing translated artifact; OCR text was passed onward via its temporary file ID.

## Cost and cache audit

The ₹0.49554 total is the sum of returned costs for cache misses; dry-run estimates were not charged. A cache miss means Redis had no valid record for that exact tool/model/input/parameter key at call time. The reports do not contain a pre-run snapshot of every serialized key, so the exact reason for each remaining miss cannot be reconstructed after the fact.

One avoidable repeat synthesis was identified in `video_translation_high_level_01` (₹0.0702). `translate_video` passed the selected voice's catalog language (`sd`, Moomal's recording language) to Indic-Speak even though the requested output was Tamil. Bodhan allows any voice to speak any supported language, so this created a different cache key from the manual Tamil dubbing step. The high-level tool also cached audio under a temporary directory that is deleted after muxing; Redis retained a record pointing to the now-missing WAV, and the cache correctly rejected that broken artifact on later lookups. Both issues are fixed: synthesis now uses the requested target language, and cache audio is kept in a persistent cache-artifact directory for the cache TTL.

Other spend included the PDF summary's translation/synthesis and the back-transcription requested by `verify_pages_summary_and_audio` (`back_transcription_covers_key_facts`, ₹0.059307); those were distinct content/audio inputs. Several translation misses also came from exact text or script parameters differing from the cached request. The cache key normalization now treats omitted `script` and `script="native"` as equivalent and strips only leading/trailing whitespace before text model calls. Transcription keys intentionally remain distinct when a known language is supplied versus when vendor auto-detection is requested.

After the fixes, I made no paid calls. I verified exact duplicate Tamil notice translation and Tamil synthesis requests against existing Redis entries: both returned `cache_hit=true` and `cost_inr=0.0`. The synthesis cache restored its WAV to a temporary destination successfully.
