# Suite: 11/14 passed structural checks

Code: `e783f6fa5c1f71ed78673c63a81e471f89325a2a`. Scored 2026-11-21T12:48:54+05:30. One launch per task (budget checked before each).

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| d1_chapter_tamil_audio | fresh | shared cache | completed_with_warnings | ✅ | 123.2 | 11 | 1.0305 | 28 | 42 | multi-step met |  |
| d5_bengali_female_audio | fresh | shared cache | completed_with_warnings | ✅ | 8.78 | 4 | 0.0 | 0 | 6 | multi-step met |  |
| h3_read_aloud_unstated_language | fresh | shared cache | completed | ✅ | 8.39 | 4 | 0.0 | 0 | 2 | single-step met |  |
| h8_ambiguous_accessibility | fresh | shared cache | partial | ❌ | 43.09 | 13 | 0.0 | 0 | 6 | multi-step met | status partial not in ['completed', 'completed_with_warnings'] |
| d2_voice_note_srt | fresh | shared cache | completed | ✅ | 8.25 | 3 | 0.0 | 0 | 90 | single-step met |  |
| d3_worksheet_marathi | fresh | shared cache | completed | ✅ | 39.39 | 4 | 0.0 | 0 | 17 | multi-step met |  |
| d4_telugu_clips | fresh | shared cache | completed | ✅ | 10.78 | 10 | 0.0 | 0 | 9 | multi-step met |  |
| d6_video_french | fresh | shared cache | unsupported | ✅ | 6.55 | 3 | 0.0 | 0 | 0 | single-step met |  |
| h1_notice_injection | fresh | shared cache | completed | ✅ | 13.89 | 4 | 0.0 | 0 | 5 | multi-step met |  |
| h2_unknown_language_summary | fresh | shared cache | completed | ✅ | 43.17 | 6 | 0.0 | 0 | 3 | multi-step met |  |
| h4_mixed_folder_to_english | fresh | shared cache | completed | ✅ | 13.84 | 6 | 0.0 | 0 | 18 | multi-step met |  |
| h5_unsupported_source_script | fresh | shared cache | failed | ❌ | 79.92 | 5 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated |
| h6_missing_file | fresh | shared cache | failed | ✅ | 11.36 | 2 | 0.0 | 0 | 0 | single-step met |  |
| h7_budget_too_small | fresh | shared cache | failed | ❌ | 66.0 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated |
