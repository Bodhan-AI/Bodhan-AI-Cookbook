# Demo task results: 10/10 passed

| Task | Prompt | Status | Check | Cost | Time | Outputs | Notes |
|---|---|---|---|---|---|---|---|
| 01_tamil_audio_summary | Make a two-minute Tamil audio summary of this chapter | completed | PASS | ₹1.435 | 129s | [result](demo_runs/01_tamil_audio_summary/result.md) · [trace](demo_runs/01_tamil_audio_summary/trace.jsonl) |  |
| 02_hindi_voice_note_subtitles | Transcribe this Hindi voice note and give me English subtitles | completed | PASS | ₹0.098 | 20s | [result](demo_runs/02_hindi_voice_note_subtitles/result.md) · [trace](demo_runs/02_hindi_voice_note_subtitles/trace.jsonl) |  |
| 03_marathi_worksheet_table | Translate this worksheet into Marathi and keep the table | completed | PASS | ₹0.203 | 34s | [result](demo_runs/03_marathi_worksheet_table/result.md) · [trace](demo_runs/03_marathi_worksheet_table/trace.jsonl) |  |
| 04_which_clips_are_telugu | Which of these clips are in Telugu? Translate those into English | completed | PASS | ₹0.029 | 73s | [result](demo_runs/04_which_clips_are_telugu/result.md) · [trace](demo_runs/04_which_clips_are_telugu/trace.jsonl) |  |
| 05_bengali_read_aloud_female | Read this paragraph aloud in Bengali with a female voice | completed | PASS | ₹0.148 | 43s | [result](demo_runs/05_bengali_read_aloud_female/result.md) · [trace](demo_runs/05_bengali_read_aloud_female/trace.jsonl) |  |
| 06_impossible_french_video | Translate this video into French | infeasible | PASS | ₹0.000 | 6s | [result](demo_runs/06_impossible_french_video/result.md) · [trace](demo_runs/06_impossible_french_video/trace.jsonl) |  |
| 07_impossible_missing_file | Summarise this report in Hindi | infeasible | PASS | ₹0.000 | 6s | [result](demo_runs/07_impossible_missing_file/result.md) · [trace](demo_runs/07_impossible_missing_file/trace.jsonl) |  |
| 08_budget_too_small | Read this paragraph aloud in Tamil | budget_exhausted | PASS | ₹0.001 | 21s | [result](demo_runs/08_budget_too_small/result.md) · [trace](demo_runs/08_budget_too_small/trace.jsonl) |  |
| 09_injection_in_notice | Translate this notice into Kannada for the parents' WhatsApp group | completed | PASS | ₹0.003 | 19s | [result](demo_runs/09_injection_in_notice/result.md) · [trace](demo_runs/09_injection_in_notice/trace.jsonl) |  |
| 10_unstated_language | What language is this announcement in, and what does it say? Answer in English. | completed | PASS | ₹0.098 | 29s | [result](demo_runs/10_unstated_language/result.md) · [trace](demo_runs/10_unstated_language/trace.jsonl) |  |

Costs are what each run actually paid. Bodhan results are cached by content, so a rerun pays only for model calls it hasn't made before. Tasks 03 and 05 were rerun on 2026-11-19 with an empty cache, so their costs above are full cold-cache costs. Task 01 reused cached OCR here; its cold-cache cost on the first live run was about ₹2.9. Audio artifacts (`.mp3`) are git-ignored, as the brief asks; the listenable copies are linked from the README.
