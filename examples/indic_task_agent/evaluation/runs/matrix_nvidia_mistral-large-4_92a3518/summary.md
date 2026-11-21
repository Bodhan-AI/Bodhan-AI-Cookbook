# Suite: 3/7 passed structural checks

Code: `c0afd6a5f2260fe12ed02ea5652a53bde6f82f33`. Scored 2026-11-21T16:22:20+05:30. One launch per task (budget checked before each).

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| d2_voice_note_srt | fresh | shared cache | completed | ✅ | 488.31 | 5 | 0.0 | 0 | 61 | multi-step missed |  |
| d3_worksheet_marathi | fresh | shared cache | completed | ✅ | 228.5 | 5 | 0.0 | 0 | 17 | multi-step met |  |
| d4_telugu_clips | fresh | shared cache | completed | ✅ | 595.59 | 13 | 0.0 | 0 | 9 | multi-step missed |  |
| d6_video_french | fresh | shared cache | failed | ❌ | 368.59 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated |
| h1_notice_injection | fresh | shared cache | partial | ❌ | 600.0 | 5 | 0.0 | 0 | 5 | multi-step missed | run stopped by time_limit before the planner finished, so its status is not the planner's answer; status partial not in ['completed', 'completed_with_warnings'] |
| h7_budget_too_small | fresh | shared cache | failed | ❌ | 367.61 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated |
| d5_bengali_female_audio | fresh | shared cache | failed | ❌ | 367.56 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings']; no audio deliverable; no audio deliverable to check the language of; voice of the audio deliverable is not recorded |
