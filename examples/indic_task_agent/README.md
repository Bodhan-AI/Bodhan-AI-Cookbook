# Indic task agent

> An MCP server that exposes Bodhan's four Indic models as tools, plus a command-line agent that plans and runs multi-step language tasks with them.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages shown:** Hindi, Tamil, Marathi, Telugu, Bengali
**Format:** CLI + MCP server

*Work in progress, built in phases.*

## Setup

```bash
cd examples/indic_task_agent
python3.11 -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -r requirements.txt
cp .env.example .env   # then paste your keys from https://console.bodhan.ai
```

System dependency: `ffmpeg`.

## Run

Run commands from this folder.

```bash
pytest -q   # unit tests, no network, no spend
```

Bodhan responses are cached in `~/.cache/bodhan_agent/`, so the same input is never paid for twice.
