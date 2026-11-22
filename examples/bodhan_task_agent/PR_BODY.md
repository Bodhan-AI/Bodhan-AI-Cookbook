## Summary

Agentic language-task engine exposing four Bodhan Indic APIs through a standalone MCP stdio server, plus a CLI agent that plans and executes unfamiliar tasks with auditable files, model-cost ceilings, structured error reporting, and QA.

## How to run

From `examples/bodhan_task_agent`: install Python 3.10+ and ffmpeg, `pip install -e .`, configure the private `.env` based on `.env.example`, then:

```bash
python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" --files path/to/inputs/ --out ./out --max-steps 30 --max-cost-inr 20
python -m bodhan_mcp
```

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the agent's OpenAI-compatible LLM selects typed tools, all model calls are routed by `ToolHub` to Bodhan, model outputs are cached and verified, and the MCP server offers the same tool definitions to independent clients.

## Agent flow

See the plan→act→verify→retry/stop flowchart in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Untrusted document instructions are not executable. Failure reports never invent artifacts.

## Tool design

`inspect_files`, `read_text`, `ocr_document`, `transcribe_audio`, `translate_text`, `translate_segments`, `speak_text`, `create_subtitles`, `save_english_text`. Each tool validates paths/types and returns typed JSON errors. Long audio is chunked at ≤25 seconds; TTS sentences are chunked and joined; scanned PDFs are rasterized before OCR. Full design in [`docs/DESIGN_NOTES.md`](docs/DESIGN_NOTES.md).

## Where the LLM is used, and why

Planning, choosing tools, writing English summaries, interpreting verification evidence, and stopping honestly on impossible requests. Indian-language text is never generated, translated, or rewritten by the general LLM. Bodhan handles all speech/OCR/translation.

## Bodhan models used

Indic-Transcribe, Indic-Translate, Indic-Speak, Indic-OCR using the hosted Bodhan HTTP API, each with its own key and independently cached exact inputs. No non-Bodhan equivalents.

## Results

**PENDING LIVE VALIDATION:** Locally isolated simulated tests: 22 passed. Real Bodhan demo artifacts, their public links, costs, quality metrics and independent MCP Inspector results must be added after a paid/credited API run. Do not submit this PR description unchanged as a finished project.

## Demo video

**PENDING:** Public, sign-in-free recording ≤5 minutes, with real model outputs and traces.

## Trade-offs and known limitations

Coarse ASR chunk timestamps rather than word alignment; heuristic language detection when ASR metadata is absent; structure-preservation fallback affects translation fluency; external LLM pricing excluded from budget estimates; no native-speaker accuracy claim without live testing. See README for full limitations.

## Cost and runtime

**PENDING REAL MEASUREMENTS:** `report.json` records model-cost reservations and time. Live demo run values must be reported per task.

## AI assistance

Substantial AI coding assistance from OpenAI ChatGPT for design, implementation, tests and documentation. Applicant reviews code and should be able to make live modifications in the debrief.
