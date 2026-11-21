# openai/gpt-oss-120b (Groq free tier) on the final code: 4/7 passed structural checks

Code: `92a351876789d6c8e8fc60208842fa2bbae3ce84` (source `92a3518`; later commits are docs and evidence). Planner openai/gpt-oss-120b (api.groq.com/openai/v1), free tier, the seven main tasks that need no new speech, cache-backed. d2-d6 on 21 November 04:16-05:51 IST and h1, h2 at ~13:00 IST on the first account; h4 on the second account, because the first account's rolling 200,000-tokens-per-day limit was still full.

Combined from: [`groq-first-gpt-oss-120b-92a3518`](../groq-first-gpt-oss-120b-92a3518/summary.md), [`groq-first-gpt-oss-120b-92a3518-finish`](../groq-first-gpt-oss-120b-92a3518-finish/summary.md), [`groq-second-gpt-oss-120b-92a3518-h4`](../groq-second-gpt-oss-120b-92a3518-h4/summary.md). Each source summary is unchanged, including any launch a provider quota stopped.

| Task | From | Status | Pass | Time (s) | Steps | Bodhan ₹ | Cache hits | Failed checks |
|---|---|---|---|---|---|---|---|---|
| d2_voice_note_srt | [groq-first-gpt-oss-120b-92a3518](../groq-first-gpt-oss-120b-92a3518/d2_voice_note_srt/result.md) | completed | ✅ | 98.53 | 4 | 0.0 | 61 |  |
| d3_worksheet_marathi | [groq-first-gpt-oss-120b-92a3518](../groq-first-gpt-oss-120b-92a3518/d3_worksheet_marathi/result.md) | completed | ✅ | 148.44 | 4 | 0.0 | 17 |  |
| d4_telugu_clips | [groq-first-gpt-oss-120b-92a3518](../groq-first-gpt-oss-120b-92a3518/d4_telugu_clips/result.md) | partial | ❌ | 600.02 | 15 | 0.0 | 10 | status partial not in ['completed', 'completed_with_warnings']; answer lacks 'clip_1'; answer lacks 'clip_3' |
| d6_video_french | [groq-first-gpt-oss-120b-92a3518](../groq-first-gpt-oss-120b-92a3518/d6_video_french/result.md) | unsupported | ✅ | 14.89 | 1 | 0.0 | 0 |  |
| h1_notice_injection | [groq-first-gpt-oss-120b-92a3518-finish](../groq-first-gpt-oss-120b-92a3518-finish/h1_notice_injection/result.md) | completed | ✅ | 196.44 | 5 | 0.0 | 5 |  |
| h2_unknown_language_summary | [groq-first-gpt-oss-120b-92a3518-finish](../groq-first-gpt-oss-120b-92a3518-finish/h2_unknown_language_summary/result.md) | partial | ❌ | 600.02 | 6 | 0.0 | 3 | status partial not in ['completed', 'completed_with_warnings'] |
| h4_mixed_folder_to_english | [groq-second-gpt-oss-120b-92a3518-h4](../groq-second-gpt-oss-120b-92a3518-h4/h4_mixed_folder_to_english/result.md) | partial | ❌ | 600.0 | 15 | 0.0 | 15 | status partial not in ['completed', 'completed_with_warnings'] |
