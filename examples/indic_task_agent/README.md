# Indic task agent

> An MCP server that exposes Bodhan's four Indic models as tools, plus a command-line agent that plans and runs multi-step language tasks with them.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages shown:** Hindi, Tamil, Marathi, Telugu, Bengali
**Format:** CLI + MCP server

*Work in progress, built in phases.*

## Setup

```bash
cd examples/indic_task_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your keys from https://console.bodhan.ai
```

System dependency: `ffmpeg` (for `ffprobe`).

## Run

Phase 0 API probe (one tiny call per model, about Rs 0.33; cached runs cost nothing):

```bash
python scripts/probe.py
```
