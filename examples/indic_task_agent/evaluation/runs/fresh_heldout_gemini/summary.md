# Suite: 4/6 passed structural checks

Code: `e2c1302d6c1d26aa1b5633fcaed950489f09feb1`. Scored 2026-11-20T18:18:29+00:00.

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| h9_write_out_recording | fresh | shared cache | completed | ✅ | 12.23 | 4 | 0.0 | 0 | 1 | single-step met |  |
| h10_notice_to_tamil_voice | fresh | shared cache | completed_with_warnings | ✅ | 44.86 | 5 | 0.4205 | 14 | 1 | multi-step met |  |
| h11_voice_note_question | fresh | shared cache | completed_with_warnings | ✅ | 27.0 | 8 | 0.0 | 0 | 11 | multi-step met |  |
| h12_gujarati_voiceover_conflict | fresh | shared cache | failed | ❌ | 20.58 | 10 | 0.0 | 0 | 9 | multi-step met | status failed not in ['completed', 'completed_with_warnings']; no audio deliverable; no audio deliverable to check the language of; voice of the audio deliverable is not recorded |
| h13_bhojpuri_partly_unsupported | fresh | shared cache | partial | ✅ | 38.3 | 7 | 0.0028 | 2 | 4 | multi-step met |  |
| h14_folder_weekday_filter | fresh | shared cache | completed | ❌ | 32.62 | 13 | 0.003 | 2 | 7 | multi-step met | 1 deliverables < 2 |
