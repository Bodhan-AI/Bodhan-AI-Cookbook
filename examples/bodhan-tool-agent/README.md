# Bodhan Tool Server and Task Agent

> MCP tool server and autonomous task agent for Bodhan Indic AI models.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr  
**Languages shown:** Indic languages (Hindi, Tamil, Bengali, Marathi, etc.)  
**Format:** CLI Agent and Stdio MCP Server  

## What it does

An MCP server exposing Bodhan's Indic AI models as structured tools, paired with an autonomous agent that plans, executes, verifies, and retries complex multi-step language tasks. The agent accepts plain-language prompts and input files, manages strict budgets and disk caching, and verifies outputs using back-translation.

## Setup

```bash
cd examples/bodhan-tool-agent
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Install dependencies
pip install -r requirements.txt
pip install -e .
# Copy environment file and add your keys
cp .env.example .env
```

## Run

### 1. Task Agent CLI
Run autonomous tasks over input files:
```powershell
python -m bodhan_agent --task "Transcribe this Hindi voice note and give me English subtitles" --files samples/ --out ./out --max-steps 30 --max-cost-inr 20
```

### 2. Standalone MCP Server
Launch the MCP server over stdio for third-party MCP clients:
```powershell
python -m bodhan_mcp
```

## Architecture

- **`bodhan_mcp`**: Stdio MCP server exposing 5 tools (`transcribe_audio`, `translate_text`, `synthesize_speech`, `extract_document_text`, `inspect_media`) with automatic chunking and limit handling.
- **`bodhan_agent`**: Autonomous agent loop (plan, act, verify, retry, stop) with live budget tracking, disk caching, and step tracing.

## How it works

1. **Plan**: LLM decomposes the task and checks for unsupported languages or impossible requirements.
2. **Act**: Plain code enforces disk cache, budget caps, and calls Bodhan tools with file paths.
3. **Verify**: Plain code validates file formats and audio lengths, and uses `indic-translate` for English back-translation so the LLM judges quality strictly in English.
4. **Trace**: All actions, latencies, and costs are emitted to `out/trace.jsonl` and `out/report.json`.

## Limitations

- Audio requests are chunked to $\le 30$ seconds.
- TTS inputs are split into short sentences ($\le 30$ seconds speech).
- Multi-page PDFs are rendered and OCR-processed per page.
- Indian-language generation is delegated exclusively to Bodhan models; the LLM operates only in English.
