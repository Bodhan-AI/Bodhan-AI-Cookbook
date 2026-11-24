# Suite: 3/5 passed structural checks

Code: `7a5f8ce2b080fc9cd63d658060b210fe9d6ce47a`. Scored 2026-11-20T19:36:30+00:00.

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| H1P1_h10_voice_message | fresh | shared cache | partial | ❌ | 65.53 | 9 | 0.0 | 0 | 15 | multi-step met | status partial not in ['completed', 'completed_with_warnings'] |
| H1P2_text_message | fresh | shared cache | completed_with_warnings | ✅ | 34.42 | 6 | 0.0034 | 6 | 1 | multi-step met |  |
| H1C_verbatim_translation | fresh | shared cache | completed | ✅ | 14.14 | 4 | 0.0 | 0 | 7 | multi-step met |  |
| H2P_h14_weekday_filter | fresh | shared cache | completed | ✅ | 20.8 | 10 | 0.0016 | 2 | 25 | multi-step met |  |
| H2C_zero_matches | fresh | shared cache | completed | ❌ | 21.78 | 12 | 0.0 | 0 | 15 | multi-step met | impossible task left files in artifacts/ |
