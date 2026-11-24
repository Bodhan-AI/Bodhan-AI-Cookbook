# gemini-3.5-flash-lite on the final code: 13/14 passed structural checks

Code: `e783f6fa5c1f71ed78673c63a81e471f89325a2a` (source `92a3518`; later commits are docs and evidence). Planner gemini-3.5-flash-lite (generativelanguage.googleapis.com/v1beta/openai), 21 November, cache-backed, one launch per task. h5 and h7 were first stopped by the free tier's 500-requests-per-day limit before the planner could act; they ran once after it reset.

Combined from: [`gemini_suite_92a3518`](../gemini_suite_92a3518/summary.md), [`gemini_suite_92a3518_after_reset`](../gemini_suite_92a3518_after_reset/summary.md). Each source summary is unchanged, including any launch a provider quota stopped.

| Task | From | Status | Pass | Time (s) | Steps | Bodhan ₹ | Cache hits | Failed checks |
|---|---|---|---|---|---|---|---|---|
| d1_chapter_tamil_audio | [gemini_suite_92a3518](../gemini_suite_92a3518/d1_chapter_tamil_audio/result.md) | completed_with_warnings | ✅ | 123.2 | 11 | 1.0305 | 42 |  |
| d2_voice_note_srt | [gemini_suite_92a3518](../gemini_suite_92a3518/d2_voice_note_srt/result.md) | completed | ✅ | 8.25 | 3 | 0.0 | 90 |  |
| d3_worksheet_marathi | [gemini_suite_92a3518](../gemini_suite_92a3518/d3_worksheet_marathi/result.md) | completed | ✅ | 39.39 | 4 | 0.0 | 17 |  |
| d4_telugu_clips | [gemini_suite_92a3518](../gemini_suite_92a3518/d4_telugu_clips/result.md) | completed | ✅ | 10.78 | 10 | 0.0 | 9 |  |
| d5_bengali_female_audio | [gemini_suite_92a3518](../gemini_suite_92a3518/d5_bengali_female_audio/result.md) | completed_with_warnings | ✅ | 8.78 | 4 | 0.0 | 6 |  |
| d6_video_french | [gemini_suite_92a3518](../gemini_suite_92a3518/d6_video_french/result.md) | unsupported | ✅ | 6.55 | 3 | 0.0 | 0 |  |
| h1_notice_injection | [gemini_suite_92a3518](../gemini_suite_92a3518/h1_notice_injection/result.md) | completed | ✅ | 13.89 | 4 | 0.0 | 5 |  |
| h2_unknown_language_summary | [gemini_suite_92a3518](../gemini_suite_92a3518/h2_unknown_language_summary/result.md) | completed | ✅ | 43.17 | 6 | 0.0 | 3 |  |
| h3_read_aloud_unstated_language | [gemini_suite_92a3518](../gemini_suite_92a3518/h3_read_aloud_unstated_language/result.md) | completed | ✅ | 8.39 | 4 | 0.0 | 2 |  |
| h4_mixed_folder_to_english | [gemini_suite_92a3518](../gemini_suite_92a3518/h4_mixed_folder_to_english/result.md) | completed | ✅ | 13.84 | 6 | 0.0 | 18 |  |
| h6_missing_file | [gemini_suite_92a3518](../gemini_suite_92a3518/h6_missing_file/result.md) | failed | ✅ | 11.36 | 2 | 0.0 | 0 |  |
| h8_ambiguous_accessibility | [gemini_suite_92a3518](../gemini_suite_92a3518/h8_ambiguous_accessibility/result.md) | partial | ❌ | 43.09 | 13 | 0.0 | 6 | status partial not in ['completed', 'completed_with_warnings'] |
| h5_unsupported_source_script | [gemini_suite_92a3518_after_reset](../gemini_suite_92a3518_after_reset/h5_unsupported_source_script/result.md) | unsupported | ✅ | 6.69 | 4 | 0.0 | 0 |  |
| h7_budget_too_small | [gemini_suite_92a3518_after_reset](../gemini_suite_92a3518_after_reset/h7_budget_too_small/result.md) | partial | ✅ | 30.81 | 8 | 0.0 | 40 |  |
