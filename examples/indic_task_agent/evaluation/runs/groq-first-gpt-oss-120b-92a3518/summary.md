# Suite: 3/4 passed structural checks

Code: `92a351876789d6c8e8fc60208842fa2bbae3ce84`. Scored 2026-11-21T00:22:22+00:00.

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| d2_voice_note_srt | reused | shared cache | completed | ✅ | 98.53 | 4 | 0.0 | 0 | 61 | multi-step met |  |
| d3_worksheet_marathi | reused | shared cache | completed | ✅ | 148.44 | 4 | 0.0 | 0 | 17 | multi-step met |  |
| d4_telugu_clips | reused | shared cache | partial | ❌ | 600.02 | 15 | 0.0 | 0 | 10 | multi-step missed | status partial not in ['completed', 'completed_with_warnings']; answer lacks 'clip_1'; answer lacks 'clip_3' |
| d6_video_french | reused | shared cache | unsupported | ✅ | 14.89 | 1 | 0.0 | 0 | 0 | single-step met |  |
