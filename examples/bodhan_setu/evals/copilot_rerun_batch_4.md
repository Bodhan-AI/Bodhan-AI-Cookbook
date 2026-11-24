# Bodhan Setu benchmark rerun: assigned four cases

## Batch outcome

- **Execution coverage:** 4/4 assigned cases attempted through their final requested operation (100%).
- **Pass rate:** 4/4 cases PASS (100%).
- **Scope:** Only the four assigned cases were run. All generated files are within `examples/bodhan_setu/outputs/rerun_<case_id>_*`; this report is the only file written under `evals`.
- Costs below are the tool-reported actual costs; dry-run estimates and cache evidence are listed separately. Amounts are INR.

## `ocr_translate_transliterate_01` — PASS

- **Calls:** 9/10 maximum. OCR dry run; translation and transliteration proxy dry runs; OCR; OCR-text retrieval; translation and transliteration dry runs against the OCR file ID; translation; transliteration.
- **Verifier checks:** Satisfied: all PDF pages processed in order (2 pages, labeled Page 1 then Page 2); English translation is based on the OCR text; romanization preserves Hindi; translation and transliteration were separate operations; cost estimates preceded paid operations. No partial failure occurred, and both requested outputs completed.
- **Cost/cache:** OCR estimate INR 0.40 for 2 pages. Before the OCR call, text-operation proxy estimates were INR 0.01 each (500 output tokens each); after OCR, actual-size dry estimates were INR 0.006 each (300 output tokens each), before their respective paid calls. OCR was a cache hit at INR 0.00; translation cost INR 0.00314; transliteration cost INR 0.00494. **Actual total: INR 0.00808 / INR 0.50.**
- **Artifacts:**
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_ocr_translate_transliterate_01_ocr.md`
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_ocr_translate_transliterate_01_english_translation.txt`
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_ocr_translate_transliterate_01_hindi_romanized.txt`

## `video_transcription_01` — PASS

- **Calls:** 2/3 maximum. `transcribe_video` dry run, then paid invocation.
- **Verifier checks:** Satisfied: local audio extraction/chunking occurred internally as part of the video transcription tool; `transcribe_video` was called; dry run preceded it; transcript was returned from the assigned video; no translation or TTS call was made.
- **Transcript:** `नमस्ते आज हम गणित की कक्षा शुरू करेंगे कृपया अपनी पुस्तकें खोलिए और पहला पाठ पढ़िए`
- **Cost/cache:** Dry estimate INR 0.01088 for 6.528 seconds of audio (one chunk). Paid call was a cache hit at INR 0.00.
- **Artifact:** `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_video_transcription_01_transcript.txt`

## `video_translation_dub_01` — PASS

- **Calls:** 9/10 maximum. Local audio extraction; transcription dry run and invocation; translation dry run and invocation; one voice-catalog lookup attempt; synthesis dry run and invocation; audio replacement/mux.
- **Verifier checks:** Satisfied: source audio was extracted locally once; extracted audio was transcribed; transcript was translated to Tamil; Tamil speech was synthesized to WAV; each paid model step had a preceding dry run; mux produced a new video preserving the source visuals; longer replacement speech was not cut; no lip-sync claim is made. The output reports that the video was extended with a frozen final frame.
- **Pipeline evidence:** Extracted audio was 6.528 seconds. Transcript: `नमस्ते आज हम गणित की कक्षा शुरू करेंगे कृपया अपनी पुस्तकें खोलिए और पहला पाठ पढ़िए`. Tamil translation: `வணக்கம், இன்று நாம் கணித வகுப்பைத் தொடங்குகிறோம். தயவுசெய்து உங்கள் புத்தகங்களைத் திறந்து முதல் பாடத்தைப் படியுங்கள்.` Synthesized WAV duration was 9.387 seconds; mux strategy was `video_extended_with_frozen_final_frame`.
- **Cost/cache:** Dry estimates: transcription INR 0.01088, translation INR 0.002, synthesis INR 0.0702. Transcription and translation were cache hits at INR 0.00; synthesis cost INR 0.0702; extraction and mux cost INR 0.00. **Actual total: INR 0.0702 / INR 5.00.**
- **Note:** A `get_file` attempt for `bodhan://voices` returned “File does not exist”; it incurred no charge and did not block the case. The manual synthesis accepted the voice `Moomal` with Tamil language.
- **Artifacts:**
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_video_translation_dub_01_audio.wav`
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_video_translation_dub_01_transcript.txt`
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_video_translation_dub_01_tamil_translation.txt`
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_video_translation_dub_01_tamil.wav`
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_video_translation_dub_01.mp4`

## `video_translation_high_level_01` — PASS

- **Calls:** 2/3 maximum. High-level workflow dry run, then execution.
- **Verifier checks:** Satisfied: `translate_video` was selected; dry run reported `will_call_api: false`; a complete estimate was available before execution; the documented dry-run behavior compares catalog voices when possible; the closest voice was reported; transcription, Tamil translation, synthesis, and mux completed; the new video preserves visuals; DSP matching is disclosed as approximate. The dry-run response did not expose a comparison count.
- **Voice/limitations:** Selected `Moomal` (catalog language `sd`, female), using standardized DSP Euclidean matching (similarity 0.640683; distance 0.560834). This is an approximate voice resemblance, not speaker identification; the selected voice is listed as Sindhi rather than Tamil. Generated speech is not lip-synced.
- **Pipeline evidence:** Transcript and Tamil translation matched the manual case. The output video retained the visual stream and used `video_extended_with_frozen_final_frame` because replacement speech (8.277 seconds) exceeded the 6.485-second video duration.
- **Cost/cache:** Complete dry estimate INR 0.08488 (transcription INR 0.01088, translation INR 0.002, speech INR 0.072, based on caller-provided 100-token/120-character estimates); `estimate_complete: true`. Execution reported a revised estimated total of INR 0.08342. Transcription and translation were cache hits at INR 0.00; synthesis cost INR 0.0702. **Actual total: INR 0.0702 / INR 5.00.**
- **Artifact:** `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_video_translation_high_level_01.mp4`
