## Summary

A Python MCP stdio server exposing Bodhan Indic-OCR, Indic-Transcribe, Indic-Translate, and Indic-Speak, plus an OpenAI-compatible tool-calling task agent that plans, verifies results, enforces estimated rupee budgets, and emits reproducible artifacts and traces. All OCR/ASR/translation/TTS are routed to Bodhan, while the general-purpose LLM is used for planning and English reasoning only.

## How to run

From `examples/bodhan_task_agent`, install Python 3.10+, ffmpeg/ffprobe, `pip install -e .`, and configure `.env` privately from `.env.example`.

```bash
python -m bodhan_agent --task "Read this paragraph aloud in Bengali with a female voice" --files ./sample_data/paragraph.txt --out ./outputs/demo --max-steps 30 --max-cost-inr 20
python -m bodhan_mcp
```

## Architecture and agent flow

[Architecture diagram and plan→act→verify→recover/stop flowchart](https://github.com/KowshiqKatta/Bodhan-AI-Cookbook/blob/assignment/tool-agent-KowshiqKatta/examples/bodhan_task_agent/docs/ARCHITECTURE.md)

[Tool boundaries, failure handling, budgets and QA design notes](https://github.com/KowshiqKatta/Bodhan-AI-Cookbook/blob/assignment/tool-agent-KowshiqKatta/examples/bodhan_task_agent/docs/DESIGN_NOTES.md)

## Tool design

Nine tools: `inspect_files`, `read_text`, `ocr_document`, `transcribe_audio`, `translate_text`, `translate_segments`, `speak_text`, `create_subtitles`, `save_english_text`. External MCP Inspector discovered all nine tools and successfully invoked `inspect_files`, `read_text` and `ocr_document`.

## Where the LLM is used, and why

The LLM interprets task intent, plans tool calls, writes English summaries, and decides whether to retry or stop. Indian-language content must originate from Indic-Translate, not the LLM. OCR, transcription and TTS use Bodhan only.

## Bodhan models used

Hosted `indic-ocr`, `indic-transcribe`, `indic-translate`, `indic-speak`. Translation uses the documented Bodhan chat-completions fallback when `/translate` returns HTTP 404.

## Results

- **37 offline tests passed** in the Windows development copy; Cookbook's recipe validator reported zero blocking issues on the submission clone. Full `make check` still needs a confirmed run.
- **Bengali text→female speech:** completed, 19.39 s; ~12.032 s speech; back-ASR character similarity 0.975; estimated ₹0.1071.
- **Hindi speech→English SRT:** completed, 121.67 s; 6.485 s audio/one subtitle cue; estimated ₹0.0266.
- **OCR through MCP Inspector:** 1 page succeeded, 0 failed; Markdown table and math extracted; review warning for empty header/footer blocks.
- **Marathi worksheet:** direct tool created translated Markdown; offline audit found numbers/table/LaTeX preserved, but *the full agent run failed to translate* and an earlier QA check disagreed. Do **not** count as a completed autonomous task.
- **Unsupported French:** correctly refused, 0 artifacts, ₹0 estimated, 1.14 s.
- **Not yet demonstrated:** Tamil scanned-chapter audio summary and Telugu clip identification. No hidden-test pass rate is claimed.

[Live results and limitations](https://github.com/KowshiqKatta/Bodhan-AI-Cookbook/blob/assignment/tool-agent-KowshiqKatta/examples/bodhan_task_agent/docs/LIVE_VALIDATION_RESULTS.md) · [Engineering issue log](https://github.com/KowshiqKatta/Bodhan-AI-Cookbook/blob/assignment/tool-agent-KowshiqKatta/examples/bodhan_task_agent/docs/ISSUES_AND_FIXES.md)

## Demo video

**PENDING — public video URL required before final submission.** The video must be ≤5 minutes and watchable without sign-in. Do not treat this PR draft as complete until replaced with a real URL.

## Trade-offs and known limitations

Timestamp alignment is at ASR chunk granularity, not word level; complex Markdown and real linguistic fluency require human review; rate limits may prevent long translation tasks; LLM spending is excluded from estimated Bodhan budgets. See [README](https://github.com/KowshiqKatta/Bodhan-AI-Cookbook/blob/assignment/tool-agent-KowshiqKatta/examples/bodhan_task_agent/README.md).

## Cost and runtime

Bengali: estimated ₹0.1071 and 19.39 s; Hindi SRT: estimated ₹0.0266 and 121.67 s. Values are from run reports, exclude LLM provider costs, and are not bills. See [live results](https://github.com/KowshiqKatta/Bodhan-AI-Cookbook/blob/assignment/tool-agent-KowshiqKatta/examples/bodhan_task_agent/docs/LIVE_VALIDATION_RESULTS.md).

## AI assistance

Substantial assistance from OpenAI ChatGPT for design, source, automated tests, and documentation. The applicant performed Windows setup and live integration checks, reviewed artifacts and logs, and should be prepared to explain and modify the code in the technical debrief.
