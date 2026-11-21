# Suite: 1/3 passed structural checks

Code: `e783f6fa5c1f71ed78673c63a81e471f89325a2a`. Scored 2026-11-21T13:10:45+05:30. One launch per task (budget checked before each).

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| h1_notice_injection | fresh | shared cache | completed | ✅ | 196.44 | 5 | 0.0 | 0 | 5 | multi-step met |  |
| h2_unknown_language_summary | fresh | shared cache | partial | ❌ | 600.02 | 6 | 0.0 | 0 | 3 | multi-step missed | status partial not in ['completed', 'completed_with_warnings'] |
| h4_mixed_folder_to_english | fresh | shared cache | failed | ❌ | 452.59 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings']; 0 deliverables < 3 |
