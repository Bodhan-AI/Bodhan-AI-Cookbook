# Suite: 4/6 passed structural checks

Code: `fe83c6248db20f25883b34f1945903f32e3eee01`. Scored 2026-11-20T18:06:00+00:00.

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| h9_write_out_recording | fresh | shared cache | completed | ✅ | 23.08 | 3 | 0.0 | 0 | 1 | single-step met |  |
| h10_notice_to_tamil_voice | fresh | shared cache | completed_with_warnings | ✅ | 107.47 | 8 | 0.239 | 6 | 5 | multi-step met |  |
| h11_voice_note_question | fresh | shared cache | completed_with_warnings | ✅ | 41.31 | 6 | 0.0212 | 4 | 7 | multi-step met |  |
| h12_gujarati_voiceover_conflict | fresh | shared cache | unsupported | ❌ | 32.05 | 5 | 0.0 | 0 | 3 | multi-step met | status unsupported not in ['completed', 'completed_with_warnings']; no audio deliverable; no audio deliverable to check the language of; voice of the audio deliverable is not recorded |
| h13_bhojpuri_partly_unsupported | fresh | shared cache | unsupported | ❌ | 22.55 | 3 | 0.0 | 0 | 0 | single-step met | status unsupported not in ['completed_with_warnings', 'partial']; no audio deliverable; no audio deliverable to check the language of |
| h14_folder_weekday_filter | fresh | shared cache | completed | ✅ | 58.39 | 12 | 0.0076 | 10 | 15 | multi-step met |  |
