---
license: mit
tags:
- mcp
- tool-use
- evaluation
- synthetic-data
pretty_name: Bodhan Setu MCP Evaluation Suites
configs:
- config_name: default
  data_files:
  - split: single_tool
    path: single_tool.jsonl
  - split: tool_chains
    path: tool_chains.jsonl
  - split: adversarial
    path: adversarial.jsonl
---

# Bodhan Setu MCP Evaluation Suites

Synthetic task records for evaluating Bodhan MCP tool choice, dry-run cost estimates, and multi-step orchestration. Version 0.8.0 packages the referenced synthetic audio, image, PDF, text, and video media with corrected paths, and adds the completed 45-voice reference library to the local MCP setup. Version 0.7.0 added a high-level video translation workflow that matches source-audio DSP features against the full Bodhan voice catalog. Version 0.6.0 added local video audio extraction and replacement tools. Earlier additions cover Indic-Transliterate, dry-run pricing, unsupported-script handling, and translation/transliteration chains.

All records and bundled media are synthetic fixtures. No API keys or private user media are included.

| Split | Records |
|---|---:|
| single_tool | 8 |
| tool_chains | 7 |
| adversarial | 6 |
| Total | 21 |

Records focus on tool selection and orchestration. Synthetic response fixtures are expected values for offline contract evaluation only; they are not Bodhan outputs, live task results, or used by the runtime server. Video cases check extraction and muxing, transcript-to-translation-to-speech tool ordering, full workflow-tool cost-first behavior, complete voice-catalog matching, visual preservation, and duration mismatch handling. Voice matching is approximate DSP similarity, not speaker identification. The six-second synthetic MP4 fixture is included at `media/video/hindi_interview.mp4`.
