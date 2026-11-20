# Suite: 2/5 passed structural checks

Code: `480b6103f1f454ef9db1d391310833bb70a5fdd5`. Scored 2026-11-20T09:35:37+00:00.

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| d1_chapter_tamil_audio | reused | shared cache | partial | ❌ | 600.01 | 13 | 0.0 | 0 | 8 | single-step missed | status partial not in ['completed', 'completed_with_warnings']; no audio deliverable; no translation deliverable; no audio deliverable to check the language of; no translation deliverable to check the language of |
| d2_voice_note_srt | reused | shared cache | completed | ✅ | 117.61 | 4 | 0.0 | 0 | 61 | multi-step met |  |
| d3_worksheet_marathi | reused | shared cache | completed | ✅ | 139.49 | 4 | 0.0 | 0 | 17 | multi-step met |  |
| d4_telugu_clips | reused | shared cache | partial | ❌ | 600.02 | 15 | 0.0 | 0 | 9 | multi-step missed | status partial not in ['completed', 'completed_with_warnings']; answer lacks 'clip_1'; answer lacks 'clip_3' |
| d5_bengali_female_audio | reused | shared cache | failed | ❌ | 452.49 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings']; no audio deliverable; no audio deliverable to check the language of; voice of the audio deliverable is not recorded |
