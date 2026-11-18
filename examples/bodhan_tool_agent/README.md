# Bodhan Tool Server & Task Agent

A standalone stdio MCP server (`bodhan_mcp`) and an unattended CLI task agent (`bodhan_agent`) for Bodhan AI's Indic language models.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages supported:** 25 languages (Devanagari, Tamil, Telugu, Bengali, etc.)

## Setup

```bash
cd examples/bodhan_tool_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys from https://console.bodhan.ai
```

## Run

```bash
# Start the MCP server (stdio mode)
python -m bodhan_mcp

# Run the CLI task agent
python -m bodhan_agent --task "..." --files ... --out ./out
```

## How it works

1. `bodhan_mcp` — Standalone MCP server that wraps Bodhan API calls as tools
2. `bodhan_agent` — CLI agent that orchestrates tools via an LLM planner

## Limitations

- Requires ffmpeg and ffprobe in PATH
- Audio files processed in chunks ≤ 25 seconds
- TTS requests limited to one sentence or two per call