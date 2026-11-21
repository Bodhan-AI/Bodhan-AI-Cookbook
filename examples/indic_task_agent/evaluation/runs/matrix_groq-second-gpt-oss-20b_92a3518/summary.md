# Suite: 1/2 passed structural checks

Code: `3998fb419690336548ec8182c7d869c50edc7625`. Scored 2026-11-21T15:22:32+05:30. One launch per task (budget checked before each).

| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| h7_budget_too_small | fresh | shared cache | failed | ✅ | 600.0 | 1 | 0.0 | 0 | 0 | single-step missed |  |
| d5_bengali_female_audio | fresh | shared cache | failed | ❌ | 455.23 | 0 | 0.0 | 0 | 0 | single-step missed | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings']; no audio deliverable; no audio deliverable to check the language of; voice of the audio deliverable is not recorded |
