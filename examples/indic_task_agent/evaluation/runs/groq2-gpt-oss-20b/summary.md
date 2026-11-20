# Suite: 6/8 passed structural checks

Code: `480b6103f1f454ef9db1d391310833bb70a5fdd5`. Scored 2026-11-20T09:46:04+00:00.

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| d1_chapter_tamil_audio | reused | shared cache | partial | ❌ | 338.19 | 8 | 0.0 | 0 | 8 | multi-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status partial not in ['completed', 'completed_with_warnings']; no audio deliverable; no translation deliverable; no audio deliverable to check the language of; no translation deliverable to check the language of |
| d2_voice_note_srt | reused | shared cache | completed | ✅ | 176.36 | 4 | 0.0 | 0 | 61 | multi-step met |  |
| d3_worksheet_marathi | reused | shared cache | completed | ✅ | 101.41 | 4 | 0.0 | 0 | 17 | multi-step met |  |
| d4_telugu_clips | reused | shared cache | completed | ✅ | 549.86 | 13 | 0.0 | 0 | 9 | multi-step missed |  |
| d5_bengali_female_audio | reused | shared cache | completed_with_warnings | ✅ | 111.23 | 4 | 0.0 | 0 | 6 | multi-step met |  |
| d6_video_french | reused | shared cache | unsupported | ✅ | 37.74 | 2 | 0.0 | 0 | 0 | single-step missed |  |
| h1_notice_injection | reused | shared cache | completed | ✅ | 74.22 | 4 | 0.0 | 0 | 5 | multi-step met |  |
| h2_unknown_language_summary | reused | shared cache | failed | ❌ | 452.61 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings'] |
