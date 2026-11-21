# Suggested PR title

`[Agentic Eng Assignment] Tool server and agent — Ishvinder`

## Summary

Bodhan Setu is a self-contained Bodhan MCP server and task agent for Indian-language audio, images, documents, and video. The agent plans with an OpenAI-compatible LLM, performs model operations only through Bodhan tools, and writes a result, structured report, and trace for each run.

## How to run

From a clean clone:

```bash
cd examples/bodhan_setu
./setup.sh
# Add Bodhan model keys and LLM_BASE_URL, LLM_MODEL, LLM_API_KEY to .env
source .venv/bin/activate
```

Run the MCP server over stdio (the default expected by MCP clients):

```bash
python -m bodhan_mcp
```

Run a task:

```bash
python -m bodhan_agent \
  --task "Transcribe the recording and translate it into Marathi" \
  --files ./recording.wav \
  --out ./outputs \
  --max-steps 30 \
  --max-cost-inr 20
```

The agent launches its local stdio MCP server itself. Install FFmpeg and `ffprobe` for audio/video conversion. Setup and usage details are in the [recipe README](README.md).

## Architecture

The [architecture diagram and component notes](agent/README.md#proposed-architecture) show the agent, MCP server, Bodhan API, cache, and artifact flow.

- **Task agent:** interprets the request, selects tools, and manages each isolated run.
- **MCP server:** validates typed arguments, applies model limits, estimates cost, caches model requests, and returns structured outputs and file references.
- **Bodhan API:** hosted models perform transcription, OCR, translation, transliteration, and speech synthesis.
- **Local support:** Redis caches request results; bounded artifact storage and `setu:=<file_id>` references pass media between steps without adding binary content to model context.

## Agent flow

The [agent flow chart](agent/README.md#agent-turn-flow) covers planning, validation, cost reservation, execution, verification, recovery, and stopping. In short: validate the inputs and task budget; ask the LLM for the next tool action; validate and deduplicate calls; obtain dry-run estimates before paid calls; execute through MCP; verify outputs; retry or re-plan recoverable failures; and write `result.md`, `report.json`, and `trace.jsonl` on completion or an honest stop.

## Tool design

The core operations remain separately callable so MCP clients can control multi-step workflows and reuse intermediate results: `transcribe_audio`, `translate_text`, `transliterate_text`, `synthesize_speech`, and `ocr_document`. Model limits are handled inside the tools: audio is normalized and chunked, long translation and speech inputs are split and reassembled, and PDFs are rendered and OCRed page by page.

Higher-level `translate_audio` and `translate_video` tools handle VAD, transcription, translation, speech synthesis, timing, and audio/video assembly for callers asking for a dubbed deliverable. Focused helpers include `transcribe_video`, `extract_video_audio`, `replace_video_audio_track`, `convert_pdf_to_images`, `chunk_audio`, and `get_file`. Tools expose typed output schemas; file IDs let the agent chain binary or long-text outputs without flooding the LLM context. The rationale and per-tool behavior are documented in [tool granularity and contracts](agent/README.md#tool-granularity-and-contracts) and the [README tool notes](README.md#tools).

## Where the LLM is used, and why

The configured LLM interprets the task, chooses and sequences tools, records assumptions for unstated details, produces English explanations, and provides advisory checks of results. It does not perform OCR, transcription, Indian-language translation, transliteration, or speech synthesis. Deterministic Python owns schema and file checks, cost and step limits, deduplication, bounded parallel calls, retries, checkpoints, artifact handling, and output verification. Transient planner errors are retried within limits; when planning cannot continue, the run records a failed or blocked result instead of inventing work or outputs. Advisory quality checks are not a substitute for native-speaker review.

## Bodhan models used

The demos use Bodhan's hosted API at `https://api.bodhan.ai` for Indic-Transcribe, Indic-OCR, Indic-Translate, Indic-Transliterate, and Indic-Speak. Per-model credentials are loaded from local environment variables and are never tool arguments. Tool parameters follow the API's supported languages, scripts, voices, and duration limits; no self-hosted Bodhan model was used for the live demos.

## Results

The [demo index](demos/README.md) links each result, report, and trace. These seven runs use the existing synthetic Hugging Face fixtures. Six tasks completed; the unsupported-language task correctly stopped without a Bodhan model call.

| Demo | Outcome | Evidence |
| --- | --- | --- |
| Unknown-language voice note transcription | Completed; language argument omitted. The API returned no detected-language label. | [result](demos/runs/01-unknown-language-transcription/20261121-220706-transcribe-this-voice-note-the-language-is-unkno-01ee2d/result.md) · [report](demos/runs/01-unknown-language-transcription/20261121-220706-transcribe-this-voice-note-the-language-is-unkno-01ee2d/report.json) · [trace](demos/runs/01-unknown-language-transcription/20261121-220706-transcribe-this-voice-note-the-language-is-unkno-01ee2d/trace.jsonl) |
| Two-page scanned notice OCR | Completed; both pages OCRed (cache hit). | [result](demos/runs/02-multipage-notice-ocr/20261121-220738-read-the-scanned-notice-and-provide-its-text-838363/result.md) · [report](demos/runs/02-multipage-notice-ocr/20261121-220738-read-the-scanned-notice-and-provide-its-text-838363/report.json) · [trace](demos/runs/02-multipage-notice-ocr/20261121-220738-read-the-scanned-notice-and-provide-its-text-838363/trace.jsonl) |
| Photo OCR and English translation | Completed; the instruction-like text inside the image remained treated as document content. | [result](demos/runs/03-photo-ocr-translation/20261121-220817-translate-this-photographed-notice-into-english--80a175/result.md) · [report](demos/runs/03-photo-ocr-translation/20261121-220817-translate-this-photographed-notice-into-english--80a175/report.json) · [trace](demos/runs/03-photo-ocr-translation/20261121-220817-translate-this-photographed-notice-into-english--80a175/trace.jsonl) |
| 67-second audio transcription and Bengali translation | Completed with internal audio chunking. | [result](demos/runs/04-long-audio-bengali/20261121-220909-transcribe-this-long-recording-and-translate-the-d448cd/result.md) · [report](demos/runs/04-long-audio-bengali/20261121-220909-transcribe-this-long-recording-and-translate-the-d448cd/report.json) · [trace](demos/runs/04-long-audio-bengali/20261121-220909-transcribe-this-long-recording-and-translate-the-d448cd/trace.jsonl) |
| English notice translated to Hindi | Completed inline (cache hit). | [result](demos/runs/09-notice-translation-inline/20261121-221436-translate-this-english-community-library-notice--f99975/result.md) · [report](demos/runs/09-notice-translation-inline/20261121-221436-translate-this-english-community-library-notice--f99975/report.json) · [trace](demos/runs/09-notice-translation-inline/20261121-221436-translate-this-english-community-library-notice--f99975/trace.jsonl) |
| Hindi translation and generated speech | Completed; Hindi WAV generated and back-transcribed for an advisory check. | [result](demos/runs/11-notice-hindi-speech-repaired/20261121-221744-translate-this-english-community-library-notice--f1f0a6/result.md) · [report](demos/runs/11-notice-hindi-speech-repaired/20261121-221744-translate-this-english-community-library-notice--f1f0a6/report.json) · [trace](demos/runs/11-notice-hindi-speech-repaired/20261121-221744-translate-this-english-community-library-notice--f1f0a6/trace.jsonl) |
| Impossible request: translate to French | Correctly stopped as unsupported; zero Bodhan calls. | [result](demos/runs/08-impossible-unsupported-french/20261121-221356-translate-this-notice-into-french-96fe91/result.md) · [report](demos/runs/08-impossible-unsupported-french/20261121-221356-translate-this-notice-into-french-96fe91/report.json) · [trace](demos/runs/08-impossible-unsupported-french/20261121-221356-translate-this-notice-into-french-96fe91/trace.jsonl) |

The web UI sample translates the [synthetic Hindi video](https://huggingface.co/datasets/1rsh/bodhan-setu-mcp-evals/resolve/main/media/video/hindi_interview.mp4) into an Assamese audio draft. The 6.485-second picture is preserved; the reported Bodhan cost is ₹0.066940. Its advisory translation-quality check flagged the wording, so the result is partial and should be reviewed by an Assamese speaker. The recording shows this run; the six task-agent run reports and traces are linked in [demos/README.md](demos/README.md).

## Demo video

**Assignment demo (1:27):** [watch the public multi-task MP4](https://huggingface.co/datasets/1rsh/bodhan-setu-mcp-evals/resolve/main/demos/2026-11-21/bodhan-setu-multitask-demo.mp4). It was rendered in a headless browser from six existing synthetic task-agent run reports plus the live web UI video run. It demonstrates unknown-language transcription, multipage PDF OCR/cache reuse, untrusted OCR content, long-audio chunking and translation, Hindi speech synthesis, an unsupported French request stopping before Bodhan, and the partial Assamese video result with its review caveat. No additional model calls were made to create the recording. The source reports and traces are linked in [demos/README.md](demos/README.md).

**20-second Brag preview:** [watch the public MP4](https://huggingface.co/datasets/1rsh/bodhan-setu-mcp-evals/resolve/main/demos/2026-11-21/bodhan-setu-brag.mp4). It focuses on the real web UI video-translation workflow.

## Trade-offs and known limitations

- The transcription API accepts an omitted language for auto-detection but does not return the detected language or confidence, so the agent cannot report those as verified facts.
- Translation and speech checks are advisory; they do not establish native-speaker quality.
- The recorded live demos do not cover every requested workflow, and the video-translation output has no lip-sync adjustment.
- Audio/video conversion requires local FFmpeg. Artifact file IDs are local to the running service and are bounded by the per-user file count.
- The LLM provider cost is not included in Bodhan rupee budgets. The demos used local Ollama, so no hosted planner charge was incurred.
- The full test suite was not rerun after the latest implementation edits. Earlier, the recipe suite reported 70 passing tests; repository `make check` reported 31 passing and one existing AndroidSpeak recipe validation failure. These earlier results should not be treated as validation of the pushed revision.

## Cost and runtime

Costs are Bodhan-reported values from each demo's `report.json`; the two marked cache hits made no new Bodhan charge. Runtime is end-to-end wall time for the task-agent run. The planner was local `lfm2.5:8b`; local planner cost is not included.

| Demo | Bodhan cost | Runtime |
| --- | ---: | ---: |
| Unknown-language voice note transcription | ₹0.004500 | 15.566 s |
| Two-page scanned notice OCR | ₹0.000000 (cache hit) | 23.244 s |
| Photo OCR and English translation | ₹0.200860 | 16.515 s |
| 67-second audio transcription and Bengali translation | ₹0.114187 | 27.856 s |
| English notice translated to Hindi | ₹0.000000 (cache hit) | 15.568 s |
| Hindi translation and generated speech | ₹0.112880 | 46.120 s |
| Unsupported French request | ₹0.000000 | 0.353 s |
| Web UI Hindi video → Assamese audio draft (separate run) | ₹0.066940 | 65.316 s |
| **Combined reported Bodhan total** | **₹0.499367** | — |

The web UI run cost and runtime are separate from the seven task-agent CLI demo runs. Both figures exclude LLM provider cost; the local Ollama planner's cost was not metered.

## AI assistance

Codex assisted with implementation, debugging, documentation, and visual QA. Copilot CLI was used to exercise the MCP integration during evaluation. The live task-agent demos used local Ollama `lfm2.5:8b` as the planner; Bodhan handled the Indian-language model operations.
