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

### Demonstration evidence

[View execution reports, traces and generated artifacts](https://drive.google.com/file/d/1Gic-Uy80-4bDijzAQhJsO2miunY6BP2v/view?usp=sharing)

Includes evidence from six demonstration scenarios,
with documented partial results and known limitations.

Six examples have been executed through live APIs or, for the impossible MP4 task, rejected before any API calls. **No hidden-set pass rate claimed.** The original 37 offline tests and Cookbook PR validation passed; the expanded unsupported-language regressions must be rechecked in the final submission clone. Repo-wide pytest on native Windows had 30 passes and two failures outside this recipe (the existing AndroidSpeak validation and the Unix `cp` dependency).

| Demo | Outcome | Runtime | Est. Bodhan cost |
| --- | --- | ---: | ---: |
| English → Bengali female narration | Completed, back-ASR 0.975 | 19.39s | ₹0.1071 |
| Hindi audio → English SRT | Completed, short 6.485s sample; coarse cue timings | 121.67s | ₹0.0266 |
| Worksheet image → Marathi table | Autonomous completion; table/math survived, time-token formatting/QA warning | 74.40s | ₹0.2953 |
| 8-page scanned PDF → Tamil MP3 | **Partial:** OCR recovered only 5/8 pages; MP3 2m42s, back-ASR 0.714 | 442.02s | ₹3.6402 |
| Five clips → Telugu detection + English | Correctly selects clip_01/04; English clip_03 reported unknown | 88.34s | ₹0.0411 |
| French MP4 | Appropriate `UNSUPPORTED_LANGUAGE` refusal; 0 artifacts and calls | 0.00s reported | ₹0.00 |

External MCP Inspector discovered nine tools and executed file-inspection, text-reading and OCR successfully. Estimated Bodhan costs exclude LLM-provider usage and are not invoices. Tamil is a **partial** result, not an all-pages pass.

[Detailed live validation](https://github.com/KowshiqKatta/Bodhan-AI-Cookbook/blob/assignment/tool-agent-KowshiqKatta/examples/bodhan_task_agent/docs/LIVE_VALIDATION_RESULTS.md) · [Engineering issue log](https://github.com/KowshiqKatta/Bodhan-AI-Cookbook/blob/assignment/tool-agent-KowshiqKatta/examples/bodhan_task_agent/docs/ISSUES_AND_FIXES.md)

**PENDING:** Public output and trace links for these real runs; original media and `.env` are not committed.

## Demo video

[Watch the recorded project demonstration](https://drive.google.com/file/d/1gPx73aZyV-6eOShBAY60Ebix0tvE3c6M/view?usp=sharing)

The video demonstrates the MCP architecture, agent
orchestration, tool implementations, live execution
artifacts, verification and graceful failure handling.

## Trade-offs and known limitations

Timestamp alignment is at ASR chunk granularity, not word level; complex Markdown and real linguistic fluency require human review; rate limits may prevent long translation tasks; LLM spending is excluded from estimated Bodhan budgets. See [README](https://github.com/KowshiqKatta/Bodhan-AI-Cookbook/blob/assignment/tool-agent-KowshiqKatta/examples/bodhan_task_agent/README.md).

## Cost and runtime

The six run-level cost/runtime figures are in the Results table above. All are estimated Bodhan model charges excluding planning-LLM costs. Tamil exceeded the five-minute multi-step target; Hindi was validated only on a short sample.

## AI assistance

Substantial assistance from OpenAI ChatGPT for design, source, automated tests, and documentation. The applicant performed Windows setup and live integration checks, reviewed artifacts and logs, and should be prepared to explain and modify the code in the technical debrief.
