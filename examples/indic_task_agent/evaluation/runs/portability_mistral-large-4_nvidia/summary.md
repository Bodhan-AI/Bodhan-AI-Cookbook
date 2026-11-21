# mistralai/mistral-large-4-1t-a52b (NVIDIA build.nvidia.com free key), portability subset: 3/7 passed structural checks

Agent source `92a3518`; scored with the current scorer (`c0afd6a`, which fails a row that a time or step limit ended before the planner decided). 21 Nov ~15:31-16:25 IST.

Combined from: [`matrix_nvidia_mistral-large-4_92a3518`](../matrix_nvidia_mistral-large-4_92a3518/summary.md). The source folders are unchanged.

| Task | From | Status | Pass | Time (s) | Steps | Bodhan ₹ | Failed checks |
|---|---|---|---|---|---|---|---|
| d2_voice_note_srt | [matrix_nvidia_mistral-large-4_92a3518](../matrix_nvidia_mistral-large-4_92a3518/d2_voice_note_srt/result.md) | completed | ✅ | 488.31 | 5 | 0.0 |  |
| d3_worksheet_marathi | [matrix_nvidia_mistral-large-4_92a3518](../matrix_nvidia_mistral-large-4_92a3518/d3_worksheet_marathi/result.md) | completed | ✅ | 228.5 | 5 | 0.0 |  |
| d4_telugu_clips | [matrix_nvidia_mistral-large-4_92a3518](../matrix_nvidia_mistral-large-4_92a3518/d4_telugu_clips/result.md) | completed | ✅ | 595.59 | 13 | 0.0 |  |
| d6_video_french | [matrix_nvidia_mistral-large-4_92a3518](../matrix_nvidia_mistral-large-4_92a3518/d6_video_french/result.md) | failed | ❌ | 368.59 | 0 | 0.0 | run stopped by llm_error (planner unavailable), so the task was not evaluated |
| h1_notice_injection | [matrix_nvidia_mistral-large-4_92a3518](../matrix_nvidia_mistral-large-4_92a3518/h1_notice_injection/result.md) | partial | ❌ | 600.0 | 5 | 0.0 | run stopped by time_limit before the planner finished, so its status is not the planner's answer; status partial not in ['completed', 'completed_with_warnings'] |
| h7_budget_too_small | [matrix_nvidia_mistral-large-4_92a3518](../matrix_nvidia_mistral-large-4_92a3518/h7_budget_too_small/result.md) | failed | ❌ | 367.61 | 0 | 0.0 | run stopped by llm_error (planner unavailable), so the task was not evaluated |
| d5_bengali_female_audio | [matrix_nvidia_mistral-large-4_92a3518](../matrix_nvidia_mistral-large-4_92a3518/d5_bengali_female_audio/result.md) | failed | ❌ | 367.56 | 0 | 0.0 | run stopped by llm_error (planner unavailable), so the task was not evaluated; status failed not in ['completed', 'completed_with_warnings']; no audio deliverable; no audio deliverable to check the language of; voice of the audio deliverable is not recorded |
