# openai/gpt-oss-120b (Groq free tier), portability subset: 6/7 passed structural checks

Agent source `92a3518`; scored with the current scorer (`c0afd6a`, which fails a row that a time or step limit ended before the planner decided). d2-d6 on the first account (04:16-05:51 IST), h1 on the first account (~13:00), h7 and d5 on the second account (~15:00).

Combined from: [`groq-first-gpt-oss-120b-92a3518`](../groq-first-gpt-oss-120b-92a3518/summary.md), [`groq-first-gpt-oss-120b-92a3518-finish`](../groq-first-gpt-oss-120b-92a3518-finish/summary.md), [`matrix_groq-second-gpt-oss-120b_92a3518`](../matrix_groq-second-gpt-oss-120b_92a3518/summary.md). The source folders are unchanged.

| Task | From | Status | Pass | Time (s) | Steps | Bodhan ₹ | Failed checks |
|---|---|---|---|---|---|---|---|
| d2_voice_note_srt | [groq-first-gpt-oss-120b-92a3518](../groq-first-gpt-oss-120b-92a3518/d2_voice_note_srt/result.md) | completed | ✅ | 98.53 | 4 | 0.0 |  |
| d3_worksheet_marathi | [groq-first-gpt-oss-120b-92a3518](../groq-first-gpt-oss-120b-92a3518/d3_worksheet_marathi/result.md) | completed | ✅ | 148.44 | 4 | 0.0 |  |
| d4_telugu_clips | [groq-first-gpt-oss-120b-92a3518](../groq-first-gpt-oss-120b-92a3518/d4_telugu_clips/result.md) | partial | ❌ | 600.02 | 15 | 0.0 | run stopped by time_limit before the planner finished, so its status is not the planner's answer; status partial not in ['completed', 'completed_with_warnings']; answer lacks 'clip_1'; answer lacks 'clip_3' |
| d6_video_french | [groq-first-gpt-oss-120b-92a3518](../groq-first-gpt-oss-120b-92a3518/d6_video_french/result.md) | unsupported | ✅ | 14.89 | 1 | 0.0 |  |
| h1_notice_injection | [groq-first-gpt-oss-120b-92a3518-finish](../groq-first-gpt-oss-120b-92a3518-finish/h1_notice_injection/result.md) | completed | ✅ | 196.44 | 5 | 0.0 |  |
| h7_budget_too_small | [matrix_groq-second-gpt-oss-120b_92a3518](../matrix_groq-second-gpt-oss-120b_92a3518/h7_budget_too_small/result.md) | unsupported | ✅ | 2.81 | 1 | 0.0 |  |
| d5_bengali_female_audio | [matrix_groq-second-gpt-oss-120b_92a3518](../matrix_groq-second-gpt-oss-120b_92a3518/d5_bengali_female_audio/result.md) | completed_with_warnings | ✅ | 169.42 | 4 | 0.0 |  |
