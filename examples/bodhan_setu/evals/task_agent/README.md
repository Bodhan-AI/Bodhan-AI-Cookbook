# Task-agent evaluations

This suite evaluates full agent runs, separately from `tests/` (MCP tool contracts) and the existing MCP tool-catalog evaluation suite.

## Run

Activate the recipe virtual environment and configure the OpenAI-compatible LLM and Bodhan API keys. The runner fetches and SHA-256 checks the synthetic inputs from the public Hugging Face dataset if needed. Then run:

```bash
python evals/task_agent/run.py --out ./eval-results --jobs 1
```

To run the original 16 MCP design tasks through this task agent instead of Copilot, use `--suite design_tasks`:

```bash
python evals/task_agent/run.py --suite design_tasks --out ./eval-results --jobs 1 --max-parallel-tools 1
```

Select cases with repeated `--case CASE_ID` flags. `--jobs` bounds independent task runs; `--max-parallel-tools` bounds concurrent MCP requests inside each run. Cases make real model/API calls and can incur costs. Each case has an explicit `max_cost_inr`, a fresh output directory, isolated artifact stores, and an in-memory cache. The runner retains stdout/stderr, `result.md`, `report.json`, `trace.jsonl`, and a `summary.json` with **pass rate** and **execution coverage** reported separately.

For a no-Bodhan-spend end-to-end run, add `--mock-bodhan`:

```bash
python evals/task_agent/run.py --suite design_tasks --out ./eval-results/mock --jobs 2 --mock-bodhan
```

This replaces only Bodhan HTTP responses with deterministic fixtures. The configured local/OpenAI-compatible planner, MCP client/server, tool schemas, dry-run gating, caches, file artifacts, chunking, and video processing still execute normally. Mock paid calls report ₹0; dry runs continue to show the configured price estimates. This mode is for planner/integration behavior, not for judging real model quality. Set no Bodhan credentials to use it; it cannot contact Bodhan while enabled.

`--cache-only` reads the shared Redis model cache and refuses Bodhan requests on a miss. It is a useful no-spend check for previously cached inputs; uncached outputs are reported as blocked. This differs from `--mock-bodhan`, which returns deterministic fixtures for cache misses.

## Case contract

`cases.jsonl` defines the task-agent starter suite. The `design_tasks` suite reads the original `evals/design_tasks.jsonl` prompts and media, with task-agent tool requirements in `run.py`. Coverage counts successful expected-tool units; case execution coverage is reported separately. The deterministic verifier checks tool coverage, forbidden calls, terminal status, and selected honest-failure language. It does not execute every semantic verifier in `evals/verifiers.jsonl` or judge translation correctness and speech naturalness; those require separate verification or human review.

The starter cases cover unknown-language transcription, multipage OCR, OCR plus translation with adversarial text, scanned-document narration, multi-file clip selection, unsupported French video translation, long-audio translation, and missing-file honesty. The 16-case design suite adds translation/transliteration chains, cache parameter sensitivity, unsupported scripts, and video transcription/dubbing. Deterministic pass rate measures tool choice and artifact structure; it does not establish translation accuracy or speech quality.
