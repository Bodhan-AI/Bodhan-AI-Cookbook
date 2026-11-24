# Offline evals

A small golden set (`golden.json`, 12 tasks) that runs the **real agent** (real LLM, real MCP server, real harness) and scores what it did.
Results go to the console, to `outputs/evals/<run>.json` and, when the Langfuse keys are set, to Langfuse as a dataset run.

## Two modes

| Mode | LLM | Bodhan tools | Bodhan cost | What it tells you |
|---|---|---|---|---|
| `stub` (default) | real (Groq `openai/gpt-oss-120b`) | the 5 paid tools return canned results with the real names, descriptions and schemas; the free tools stay real | Rs 0 | planner decisions: tools, arguments, order, refusals, injection handling, budgets |
| `live` | real | real | small, capped per case | that stubs and reality agree; real output quality |

Stubs say nothing about Bodhan's output quality. They only test the agent around the models.

## Run

```bash
pip install -e ".[dev,eval]"
python -m evals.run_evals                          # all stub cases, Langfuse on if keys are set
python -m evals.run_evals --only injection_in_text_file,translate_one_line_to_hindi
python -m evals.run_evals --judge                  # + DeepEval LLM metrics (extra Groq calls, no Bodhan cost)
python -m evals.run_evals --mode live              # live cases only; checks the credit ledger first
pytest evals -m stub                               # the same cases as pytest (EVAL_JUDGE=1 adds DeepEval)
EVAL_LIVE=1 pytest evals -m live
```

Live mode only runs cases marked `"live": true` whose keys are set (`BODHAN_TRANSCRIBE_KEY`, `BODHAN_OCR_KEY`, ...), refuses to run when
less than Rs 2 of credit is left, and appends every paid run to `outputs/credit_ledger.json`.

## What is scored

Deterministic checks (no judge, no cost): `status`, `tool_sequence`, `tool_counts`, `tools_forbidden`, `args`, `args_absent`,
`no_duplicate_calls`, `no_artifacts`, `artifact_suffixes_any`, `verified_audio`, `verified_translation`, `injection_flagged`,
`answer_contains`/`answer_excludes`, `max_cost_inr`, `max_llm_turns`, and `no_llm_indic_text` (every Indian-language word the LLM wrote
must already exist in a tool result or file: the assignment's hard rule). A case passes (`case_passed`) only when all its checks pass.

DeepEval (`--judge`, judged by the same Groq model through `evals/judge.py`): `de_tool_correctness` (deterministic),
`de_argument_correctness`, `de_task_completion`.

## Langfuse

Each run syncs the cases to the dataset `bodhan-agent/golden` (item id `golden::<case id>`), then runs experiment `bodhan-agent-evals`
with run name `<mode>-<model>-<timestamp>`. Every case is one trace (with the LangGraph LLM calls nested inside) carrying one score per
check, plus run-level `pass_rate` and `total_tool_cost_inr`. Compare runs in Langfuse under Datasets.

## Adding a case

Add an entry to `golden.json`: `task`, `inputs` (text, copy of a sample file, generated PDF, fake video), optional `stubs`, and `expect`.
Keep `live: false` unless the case costs a few paise at most.
