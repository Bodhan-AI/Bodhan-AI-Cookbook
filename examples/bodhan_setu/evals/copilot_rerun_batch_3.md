# Bodhan Setu Batch 3 Rerun Report

Execution coverage: 4/4 assigned cases attempted (100%). Pass rate: 4/4 cases passed (100%). The unsupported-script case passes because the tool rejected Cyrillic, returned no fabricated output, and made no paid call, satisfying every verifier check.

## transliterate_hindi_roman_01

- **Status:** PASS
- **Attempted calls:** 2 (1 dry run, 1 paid transliteration)
- **Verifier checks:** All satisfied: `uses_indic_transliterate_not_translate`, `dry_run_precedes_paid_call`, `roman_script_output`, `same_hindi_language_preserved`, `no_meaning_translation`.
- **Cost/cache evidence:** Dry-run estimate 0.0004 INR; paid call 0.0 INR; `cache_hit=true`.
- **Output artifact:** `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_transliterate_hindi_roman_01/roman.txt`
- **Evidence:** `Namaste, aap kaise hain?`

## transliterate_hindi_native_01

- **Status:** PASS
- **Attempted calls:** 2 (1 dry run, 1 paid transliteration)
- **Verifier checks:** All satisfied: `uses_indic_transliterate`, `dry_run_precedes_paid_call`, `native_devanagari_output`, `roman_input_meaning_preserved`.
- **Cost/cache evidence:** Dry-run estimate 0.00032 INR; paid call 0.0 INR; `cache_hit=true`.
- **Output artifact:** `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_transliterate_hindi_native_01/native.txt`
- **Evidence:** `आपका स्वागत है`

## transliteration_unsupported_script_01

- **Status:** PASS
- **Attempted calls:** 1 (1 rejected dry-run script-validation call; 0 paid calls)
- **Verifier checks:** All satisfied: `unsupported_script_reported`, `no_fake_cyrillic_output`, `no_paid_call`.
- **Cost/cache evidence:** 0.0 INR; no paid call or cache entry.
- **Output artifact:** `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_transliteration_unsupported_script_01/unsupported.txt`
- **Evidence:** The tool explicitly rejected `script="cyrillic"`: only `roman` and `native` are supported.

## transcribe_translate_transliterate_01

- **Status:** PASS
- **Attempted calls:** 6 (2 transcription calls, 2 translation calls, 2 transliteration calls)
- **Verifier checks:** All satisfied: `audio_transcribed_once`, `transcript_translated_to_english`, `original_transcript_transliterated_to_roman_hindi`, `outputs_kept_distinct`, `dry_run_precedes_each_paid_step`, `independent_text_branches_may_run_in_parallel`.
- **Cost/cache evidence:** Paid transcription 0.008853 INR (`cache_hit=false`); paid translation 0.00024 INR (`cache_hit=false`); paid transliteration 0.0 INR (`cache_hit=true`). Total paid cost 0.009093 INR, below the 0.2 INR limit. Dry-run estimates: transcription 0.008853 INR, translation 0.00128 INR, transliteration 0.00128 INR.
- **Output artifacts:**
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_transcribe_translate_transliterate_01/transcript_hi.txt`
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_transcribe_translate_transliterate_01/translation_en.txt`
  - `/Users/irsh/Documents/workdir/tasks/bodhan/examples/bodhan_setu/outputs/rerun_transcribe_translate_transliterate_01/transcript_roman_hi.txt`
