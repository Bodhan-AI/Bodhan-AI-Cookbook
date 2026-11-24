# Bodhan Setu MCP Tool Design Benchmark

This benchmark measures which MCP tool catalog helps an agent complete language tasks reliably. The experimental variable is the MCP interface: tool names, descriptions, parameter schemas, and granularity. Task prompts and input files stay fixed between catalog versions so changes in results can be attributed to the interface.

## Benchmark suites

- `design_tasks.jsonl`: task prompts with linked inputs, difficulty, limits, and verifier IDs, including transliteration-only and mixed translation/transliteration tasks.
- `tool_catalogs.jsonl`: alternative MCP tool designs to compare (per-model tools, fine-grained primitives, and workflow-level tools).
- `media_manifest.jsonl`: input-file paths, SHA-256 checksums, provenance, and ground truth. Synthetic media is hosted in the linked public Hugging Face dataset and fetched into the ignored local `media/` folder by `python prepare_media.py` or the task-agent eval runner.
- `verifiers.jsonl`: machine-checkable final-state requirements.
- `huggingface/`: versioned Dataset Viewer export published at [1rsh/bodhan-setu-mcp-evals](https://huggingface.co/datasets/1rsh/bodhan-setu-mcp-evals).

Each benchmark case is a task × catalog pair. A harness should present one catalog to an agent, run the user task in an isolated workspace, record the tool trace, and execute the same verifier. Do not bake a prescribed tool sequence into the agent-facing task prompt; store expected behavior privately in verifier metadata.

## What the dataset evaluates

Measure task pass rate, correct tool selection, invalid arguments, unnecessary calls, repeated calls, artifact correctness, recovery, tokens, latency, and estimated Bodhan cost. Report results by difficulty and task type, and compare catalog variants on the same cases. Use deterministic verifiers for artifacts and trace properties; reserve model judging for language-quality checks with a fixed rubric and blind catalog labels.

## Media

Media exists to probe real MCP file-reference behavior and model-tool workflows. Regenerate the macOS synthetic fixtures with `swift generate_document_fixtures.swift "$PWD"`, `python generate_audio_fixtures.py`, then `python refresh_media_manifest.py` from this directory. The generators use local CoreText, built-in speech voices, and FFmpeg; they make no network or Bodhan API calls. Every asset must have a known task purpose, ground truth, checksum, provenance URL, and reuse license. Never attach unrelated assets just to inflate row count. Keep source media records distinct from task rows so the same media can support controlled variants without duplication. The Cookbook repository omits these binaries; `prepare_media.py` downloads and verifies them from Hugging Face.

## Hugging Face export

The export uses JSONL with a dataset-card YAML config mapping files to named splits for the Dataset Viewer. The Hub repository is public. All records and included media are synthetic evaluation fixtures; do not add credentials, real user recordings, or private data.

## Current status

The Hugging Face export is version 0.8.0 with 21 records across single-tool, tool-chain, and adversarial splits. It packages the synthetic media fixtures referenced by the records. The tasks cover Indic-Transliterate choice, both script directions, dry-run pricing, unsupported-script honesty, multi-step audio/PDF tasks that distinguish translation from transliteration, and video translation workflows with explicit local extraction and audio-track replacement tools, plus a high-level translation tool that selects a voice from source-audio DSP features. Records evaluate tool selection and orchestration; they do not claim to benchmark Bodhan model output quality.
