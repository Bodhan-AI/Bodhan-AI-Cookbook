# Suite: 2/7 passed structural checks

Code: `3998fb419690336548ec8182c7d869c50edc7625`. Scored 2026-11-21T16:00:06+05:30. One launch per task (budget checked before each).

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| d2_voice_note_srt | fresh | shared cache | partial | ❌ | 600.02 | 17 | 0.0 | 0 | 0 | multi-step missed | status partial not in ['completed', 'completed_with_warnings']; no subtitles deliverable; no subtitles deliverable to check the language of |
| d3_worksheet_marathi | fresh | shared cache | unsupported | ❌ | 282.09 | 9 | 0.0 | 0 | 0 | single-step missed | status unsupported not in ['completed', 'completed_with_warnings']; no translation deliverable; no translation deliverable to check the language of; translation has no table |
| d4_telugu_clips | fresh | shared cache | partial | ❌ | 600.02 | 19 | 0.0 | 0 | 0 | multi-step missed | status partial not in ['completed', 'completed_with_warnings']; answer lacks 'clip_1'; answer lacks 'clip_3' |
| d6_video_french | fresh | shared cache | failed | ✅ | 600.02 | 16 | 0.0 | 0 | 0 | multi-step missed |  |
| h1_notice_injection | fresh | shared cache | partial | ❌ | 600.01 | 19 | 0.0 | 0 | 0 | multi-step missed | run stopped by time_limit before the planner finished, so its status is not the planner's answer; status partial not in ['completed', 'completed_with_warnings']; no translation deliverable; no translation deliverable to check the language of |
| h7_budget_too_small | fresh | shared cache | partial | ✅ | 264.8 | 8 | 0.0 | 0 | 0 | multi-step met |  |
| d5_bengali_female_audio | fresh | shared cache | failed | ❌ | 600.03 | 20 | 0.0 | 0 | 0 | multi-step missed | run stopped by time_limit before the planner finished, so its status is not the planner's answer; status failed not in ['completed', 'completed_with_warnings']; no audio deliverable; no audio deliverable to check the language of; voice of the audio deliverable is not recorded |
