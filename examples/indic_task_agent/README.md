# indic_task_agent

> An MCP server that exposes Bodhan's four Indic models as tools, plus a task agent that plans and runs multi-step language tasks with them.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages shown:** Hindi, Tamil, Marathi, Telugu, Bengali (more via the language list)
**Format:** CLI agent + stdio MCP server

## What it does

Status: **work in progress.** Folder scaffold only; implementation follows.

## Setup

```bash
cd examples/indic_task_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys
```

System dependency: `ffmpeg` (to be documented).

## Run

```bash
# Task agent
python -m bodhan_agent --task "..." --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]

# MCP server on its own (stdio)
python -m bodhan_mcp
```

## How it works

See [architecture](docs/architecture.md), [agent flow](docs/agent_flow.md) and [design notes](docs/design_notes.md).

## Limitations

To be filled in honestly as the build progresses.
