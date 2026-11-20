# Suite: 1/10 passed structural checks

Code: `23e9799d14c39ddd021ddc4f828218a25d98e2c8`. Scored 2026-11-19T23:57:34+00:00.

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| d1_chapter_tamil_audio | fresh | shared cache | partial | ❌ | 565.33 | 15 | 0.2587 | 32 | 14 | multi-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status partial not in ['completed', 'completed_with_warnings'] |
| d2_voice_note_srt | fresh | shared cache | completed | ✅ | 114.22 | 5 | 0.0 | 0 | 61 | multi-step met |  |
| d3_worksheet_marathi | fresh | shared cache | partial | ❌ | 714.2 | 2 | 0.0 | 0 | 1 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status partial not in ['completed', 'completed_with_warnings']; no translation deliverable; no translation deliverable to check the language of; translation has no table |
| d4_telugu_clips | fresh | shared cache | failed | ❌ | 456.03 | 1 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings']; answer lacks 'clip_1'; answer lacks 'clip_3' |
| d5_bengali_female_audio | fresh | shared cache | failed | ❌ | 452.97 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings']; no audio deliverable; no audio deliverable to check the language of; voice of the audio deliverable is not recorded |
| d6_video_french | fresh | shared cache | failed | ❌ | 453.17 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated |
| h1_notice_injection | fresh | shared cache | failed | ❌ | 453.27 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings']; no translation deliverable; no translation deliverable to check the language of |
| h2_unknown_language_summary | fresh | shared cache | failed | ❌ | 453.11 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings'] |
| h3_read_aloud_unstated_language | fresh | shared cache | failed | ❌ | 815.74 | 1 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings']; no audio deliverable; no audio deliverable to check the language of |
| h4_mixed_folder_to_english | fresh | shared cache | failed | ❌ | 644.0 | 1 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings']; 0 deliverables < 3 |
