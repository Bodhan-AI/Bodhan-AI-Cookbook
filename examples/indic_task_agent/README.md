# Indic Task Agent

An MCP tool server and command-line task agent for Bodhan's Indic speech, translation, synthesis, and OCR models.

**Bodhan models:** `indic-transcribe`, `indic-translate`, `indic-speak`, `indic-ocr`
**Languages shown:** Any language supported by the relevant Bodhan model
**Format:** MCP stdio server + CLI

## What it does

The agent accepts a plain-language task and input files, plans in an OpenAI-compatible LLM loop, calls Bodhan tools through MCP, and writes a trace, report, summary, and generated artifacts.

## Setup

```bash
cd examples/indic_task_agent
python -m venv .venv
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"
cp .env.example .env
```

Set `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, and the four Bodhan key variables in `.env`.

## Run

```bash
python -m bodhan_agent \
  --task "Transcribe this Hindi voice note and give me English subtitles" \
  --files sample_data \
  --out out \
  --max-steps 30 \
  --max-cost-inr 20
```

Start the MCP server separately:

```bash
python -m bodhan_mcp
```

## How it works

1. The LLM inventories files and plans tool calls.
2. The MCP server runs the applicable Bodhan tool and applies model limits.
3. The agent retries, caches, budgets, traces, and writes the requested outputs.
4. Files are written under `out/`.

## Limitations

Translation uses the confirmed `/v1/chat/completions` route because `/translate` currently returns 404. The speech endpoint currently labels WAV bytes as `audio/mpeg`; the client identifies the actual format from the payload. Transcription and OCR HTTP mappings are implemented from the documented response shapes but still need live smoke verification.
