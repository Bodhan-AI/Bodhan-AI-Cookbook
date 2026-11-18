# Bodhan Setu benchmark rerun — batch 1

## Coverage and outcomes

- **Execution coverage:** 4/4 assigned cases attempted.
- **Pass rate:** 4/4 attempted cases passed all listed checks.
- Dry runs are included in tool-call counts. Costs below are the paid-run cost evidence returned by Bodhan Setu; dry-run estimates are not billed.

| Case | Calls / limit | Estimated cost / budget | Actual cost / budget | Cache evidence | Result |
|---|---:|---:|---:|---|---|
| `stt_ambiguous_language_01` | 2 / 3 | ₹0.0045 / ₹1.00 | ₹0.0000 / ₹1.00 | Transcription cache hit | **PASS** |
| `ocr_table_translate_01` | 5 / 6 | ₹0.2080 / ₹2.00 | ₹0.00602 / ₹2.00 | OCR cache hit; translation miss | **PASS** |
| `pdf_summary_audio_01` | 9 / 16 | ₹1.527507 / ₹8.00 | ₹0.326527 / ₹8.00 | OCR hit; translation, synthesis, and back-transcription misses | **PASS** |
| `clip_filter_telugu_01` | 14 / 14 | ₹0.033818 / ₹3.00 | ₹0.00036 / ₹3.00 | All five transcriptions hit cache; both translations missed cache | **PASS** |

## Case evidence

### `stt_ambiguous_language_01` — PASS

**Calls:** `transcribe_audio` dry run (₹0.0045 estimated, 2.7 s, one chunk), then paid transcription (cache hit, ₹0.00). The transcript is “विद्यालय आज दोपहर तीन बजे बंद होगा” and matches the reference after normalization (WER 0). The script indicates Hindi; this is explicitly disclosed as an inference because the API returned no detected-language field.

**Checks:** `transcript_artifact_exists` — satisfied; `normalized_wer_against_reference_below_threshold` — satisfied; `language_reported_or_uncertainty_disclosed` — satisfied.

**Artifact:** `examples/bodhan_setu/outputs/rerun_stt_ambiguous_language_01_transcript.md`

### `ocr_table_translate_01` — PASS

**Calls:** `ocr_document` dry run (₹0.20 estimated), paid OCR (cache hit, ₹0.00; produced a one-page OCR artifact and temporary file ID); `translate_text` dry run (₹0.008 estimated, 400 estimated output tokens), paid Marathi translation (cache miss, ₹0.00602; used the OCR file ID); `get_file` to inspect the translated artifact. Total dry-run estimate: ₹0.208.

**Checks:** `translation_artifact_exists` — satisfied; `all_nonempty_reference_cells_present_semantically` — satisfied; `row_order_preserved` — satisfied; `no_unrequested_cells_added` — satisfied. The four weekday rows and all three time-slot values are present in order. OCR also found a note below the table; it is translated outside the table and does not add table cells.

**Artifacts:** `examples/bodhan_setu/outputs/rerun_ocr_table_translate_01_ocr.md`, `examples/bodhan_setu/outputs/rerun_ocr_table_translate_01_translation.md`

### `pdf_summary_audio_01` — PASS

**Calls:** `ocr_document` dry run (₹1.20 estimated, six pages), paid OCR (cache hit, ₹0.00; reported all six pages and returned a temporary text file ID); `get_file` to inspect OCR; `translate_text` dry run (₹0.0024 estimated, 120 estimated output tokens), paid Tamil translation (cache miss, ₹0.00202); `synthesize_speech` dry run (₹0.2658 estimated, voice `Anitha`, Tamil), paid synthesis (cache miss, ₹0.2652); `transcribe_audio` dry run (₹0.059307 estimated, 35.584 s, two chunks), paid back-transcription (cache miss, ₹0.059307). The returned back-transcription confirms the WAV was consumable by transcription.

**Checks:** `all_six_pages_processed` — satisfied; `summary_within_target_duration_estimate` — satisfied (three-sentence summary, 35.584-second audio); `target_language_supported` — satisfied (`ta`, Tamil; translation and synthesis completed); `audio_artifact_decodes` — satisfied (successful back-transcription of the generated audio); `back_transcription_covers_key_facts` — satisfied (garden, 120-litre barrel, six hours of sunlight, and avoiding chemical sprays are present).

**Artifacts:** `examples/bodhan_setu/outputs/rerun_pdf_summary_audio_01_source.md`, `examples/bodhan_setu/outputs/rerun_pdf_summary_audio_01.md`, `examples/bodhan_setu/outputs/rerun_pdf_summary_audio_01.wav`

### `clip_filter_telugu_01` — PASS

**Calls:** `transcribe_audio` dry run for each of the five files in `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/evals/media/audio/clip_batch_01/`; paid transcription for each (all cache hits, ₹0.00 total); `translate_text` dry run and paid call for each of the two Telugu transcripts (80 estimated output tokens each; cache misses; ₹0.00016 and ₹0.00020). Non-Telugu transcripts were not submitted for translation. The Hindi result reused a cached transcript originally associated with the identical `clip_ambiguous_01.wav` input; its text matched the supplied Hindi clip.

**Checks:** `only_telugu_reference_files_selected` — satisfied (`telugu_01.wav`, `telugu_02.wav` only); `all_selected_files_transcribed` — satisfied; `all_selected_transcripts_translated` — satisfied; `non_telugu_files_not_sent_for_translation` — satisfied.

**Artifact:** `examples/bodhan_setu/outputs/rerun_clip_filter_telugu_01.md`
