# Task Result (SUCCESS)

**Task:** Which of these clips are in Telugu? Translate those into English
**Status:** `success`
**Total Bodhan Cost:** ₹0.0000 / ₹10.00
**Steps Used:** 13 / 30 | **Runtime:** 18.70s | **Cache Hits:** 9

## Answer & Summary

Probed all five clips for language. Clip_01.wav and clip_04.wav were detected as Telugu; clip_02.wav (Hindi), clip_03.wav (Tamil), and clip_05.wav (Bengali) were excluded. The two Telugu clips were transcribed, translated into English, and published as a single Markdown report.

## Detected Input Languages (Probes)

| File | Detected Language | Confidence | Ambiguous | Method |
| --- | --- | --- | --- | --- |
| `clip_01.wav` | `te` | 1.00 | False | `audio_probe_10s+unicode_script` |
| `clip_02.wav` | `hi` | 0.98 | False | `audio_probe_10s+unicode_script_plus_stopwords` |
| `clip_03.wav` | `ta` | 1.00 | False | `audio_probe_10s+unicode_script` |
| `clip_04.wav` | `te` | 1.00 | False | `audio_probe_10s+unicode_script` |
| `clip_05.wav` | `bn` | 0.98 | False | `audio_probe_10s+unicode_script_plus_stopwords` |

## Artifacts Produced

- **d1** (`markdown`): [artifacts/telugu_clips_translated_en.md](artifacts/telugu_clips_translated_en.md)

## Assumptions

- The spoken language of each clip is unstated, so each clip must be probed with language detection before filtering for Telugu.
- Telugu is supported by indic-transcribe (te) and indic-translate (te), so Telugu clips can be transcribed and then translated to English via Bodhan models.
- Clips are filtered by detected language. Only clips detected as Telugu are transcribed and translated; the others are excluded from the output.
- The deliverable is a single markdown file containing one section per Telugu clip with the clip filename, the English translation, and the detected language.
- Estimated cost is well under the ₹10 budget: total clip duration is about 58 seconds for transcription plus five language probes.
- The ProbeFacts are authoritative: clip_01 and clip_04 are Telugu, and the other three clips are not.
- Only the two Telugu clips receive transcription and translation steps; the other three are excluded.
- The English translations are produced with the default script for English output.
- The deliverable is one Markdown file with one labeled section per Telugu clip.

## Verification Summary

| Deliverable | Kind | Passed | Checks |
| --- | --- | --- | --- |
| `d1` | `markdown` | PASS | PASS file_non_empty (telugu_clips_translated_en.md (394 bytes)); PASS indic_provenance (All Indic text verified from Bodhan/input); PASS translation_chunk_coverage (chunks_in=1, chunks_out=1) |

## Trace & Report

- Structured report: [report.json](report.json)
- Step-by-step trace: [trace.jsonl](trace.jsonl)
- Visual trace viewer: [trace.html](trace.html)
