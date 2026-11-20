# Experiments — Bodhan Tool Server & Task Agent

Interactive Jupyter notebooks that benchmark and validate each subsystem of the
recipe, in dependency order. Run them from this folder with the recipe's `.venv`
interpreter (the notebooks resolve paths relative to it).

## Setup

```bash
cd examples/bodhan_tool_agent

# Windows PowerShell
.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate
```

Keys are read from the recipe root `.env` (see `.env.example`): `BODHAN_*` model
keys plus `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` / `LLM_REASONING_EFFORT`.
Paid tool calls (transcribe / translate / speak / OCR) spend real rupees, tracked
by the `CostLedger` and capped per run by `max_cost_inr`.

## Index

| # | Notebook | Focus | Status |
|---|----------|-------|--------|
| 00 | `00_data_profiling_and_analysis.ipynb` | Empirical media corpus profiling: silence troughs, codecs, stdlib-only ingestion reality | executed |
| 01 | `01_audio_engine.ipynb` | Indic sentence-boundary tokenization (dandas), silence-based 30s slicing, ASR benchmarking | executed |
| 02 | `02_document_engine.ipynb` | PDF rasterization with PyMuPDF, 150-DPI sweet spot, Indic-OCR preparation | executed |
| 03 | `03_mcp_server_and_tools.ipynb` | 10-tool FastMCP catalog (8 model-facing), Pydantic schemas, ToolError self-correction semantics | executed |
| 04 | `04_agent_react_core.ipynb` | Live agent runs with tools, ReAct step tracing, report compilation | in progress |
| 05 | `05_security_and_budget.ipynb` | `<untrusted_input_data>` quarantine, pre-flight rupee circuit breaker | not yet run |
| 06 | `06_end_to_end_pipeline.ipynb` | 5-stage pipeline + honest failure on impossible tasks | not yet run |
| 07 | `07_multi_agent_orchestrator.ipynb` | Planner/worker split via `spawn_subtask` | **planned — scaffold only (future work)** |
| 08 | `08_dspy_prompt_optimization.ipynb` | DSPy routing benchmark (87 cases, v3), GEPA prompt optimization, tool-description tournament | **executed — no-spend; catalog v2 (8 model-facing tools), 0 errors** |

Statuses reflect the last verification in this workspace; treat them as hints,
not guarantees.

> Note (2026-11-19): experiment 08's embedded outputs were executed against the pre-v3
> routing benchmark (the embedded `Cases: 70 | train/dev/test: 31 15 24` line predates even
> the v2 rebuild; `sha dd3ecb58…`). The dataset has since been rebuilt as
> **v3**: 87 cases, 37/21/29 splits, `dataset_sha256 = 2c3abeefd189128415a7efd7ff67826d8099e2c98630c39edd83b67ae92a8eb4`,
> provenance audit `v3_multistep`. The lab has not been re-executed against v3.

**DSPy lab design notes:** [`docs/dspy_prompt_optimization.md`](docs/dspy_prompt_optimization.md)
covers the routing benchmark, optimizer choices, the Windows/Jupyter FastMCP deadlock, and the
apply checklist.

## Conventions

- Every run writes its own `outputs/expNN_<name>/` directory containing
  `trace.jsonl` (per-step flush), `report.json`, `result.md`, and `artifacts/`.
- `TraceLogger` **appends** to `trace.jsonl`; re-running a section stacks steps in
  the same trace — delete the run directory for a clean re-run.
- Agent runs use Jupyter top-level `await` to drive the async ReAct loop.

## Planned work

1. **Experiment 04** — hardened reasoning-effort fallback for function-calling
   endpoints, idempotent trace test, trace-on-failure observability.
2. **Experiment 07** — build only if workflows demand it (batch processing,
   long-context tasks). Cheap solutions first: observation compression, then a
   research/search tool. See the scaffold notebook for the full design sketch.
