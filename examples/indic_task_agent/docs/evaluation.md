# Evaluation

This page records how the recipe was evaluated, what each result shows and what it does not. For setup and commands, see the [README](../README.md). For the design, see [design.md](design.md).

## How to read these results

Every run below is a real execution of `python -m bodhan_agent` against the hosted Bodhan API, with a real planner LLM. Four labels keep the evidence honest:

| Label | Meaning |
|---|---|
| **cold** | Run with `--no-cache`: every Bodhan result in the run was requested and paid for in that run. Its cost is the full cost of the task. |
| **cache-backed** | The agent and planner ran for real, but Bodhan results already in the shared cache (from an earlier run with the same input bytes and settings) were reused at no cost. Its cost is the extra spend of that run only. |
| **automatic checks** | What the tools and `scripts/run_suite.py` verify in code: back-translation, back-transcription, script, numbers, tables, duration, deliverable kinds and languages, status, budget. They screen for errors. They are not a quality score. |
| **inspected** | The output was opened and read in full (texts, tables, subtitles), and for audio the duration and per-segment back-transcription were checked. No fluent reader of the Indian languages has reviewed these outputs, so their language quality beyond what back-translation shows is **not assessed**. |

The `h1`–`h8` tasks are my own held-out tasks, written to resemble the kinds of hidden tasks the brief describes. They are not the employer's hidden tests.

## Environment

### Clean Linux setup (containers)

Checked on 20 November 2026 from a fresh clone of the final commit `23e9799` (and earlier of `182938e`, with the same results). These are **Linux containers on WSL2 (Docker Desktop 29.9.3, linux/amd64, kernel 6.18.58.1-microsoft-standard-WSL2)**, not a native Linux machine.

| Check | Image | Result |
|---|---|---|
| `apt-get install ffmpeg`, then `pip install -r requirements.txt`, `pytest tests`, `ruff check .` | `python:3.11-slim` (Debian 13, Python 3.11.18, `sha256:07d48240…`) | install OK; **218 passed** (including the hard-kill and Ctrl-C resume tests, so under real Linux signals); ruff clean |
| same | `python:3.12-slim` (Debian 13, Python 3.12.16, `sha256:5e2bdb1b…`) | install OK; **218 passed**; ruff clean |
| root `make check` (`test`, `lint`, `rules`, `validate`) | both images | `lint`, `rules` and `validate` pass. `test` has **one failure that is not this recipe's**: `test_all_shipped_recipes_pass` fails on `examples/AndroidSpeak`, which is already on `main` (it has no Python source file and no `.env.example`). This branch does not touch it. The same failure appears on Windows. |
| `docker build` of the recipe image | `python:3.12-slim` base | builds (`sha256:955469a5…` at `23e9799`); runs as the non-root user `agent` (uid 1000) in `/work`; no keys or planner settings in the image |
| The brief's agent command, as the non-root user, with only the seven required variables passed by `--env-file`: `python -m bodhan_agent --task "Translate this worksheet into Marathi and keep the table" --files /data/worksheet --out ./out` | recipe image | `completed` in 19 s; outputs written to `./out`. **Cache-backed**: the host's result cache was mounted, so Bodhan cost ₹0. |
| Bare `python -m bodhan_mcp` in the container, driven over stdio by MCP Inspector 2.13.0 on the host: `tools/list`, then a real `translate_text` call (English → Tamil) | recipe image | 12 tools and the `plan_media_task` prompt listed; at `182938e` a real `translate_text` call returned `ok`, verification `pass`, ₹0.00042 |

The planner for the container run was reached through the host (`LLM_BASE_URL` pointing at `host.docker.internal`). No other setting changed.

### Development machine (where the suites ran)

Windows 11 Pro 10.0.26300, Python 3.12.13, Node 24.15.0 (for MCP Inspector), ffmpeg 9.0.1. Dependencies from the pinned `requirements.txt` (for example `mcp==2.3.0`, `openai==3.54.0`). The main planner, `gpt-6.1-sol`, was reached through a local OpenAI-compatible gateway (CLIProxyAPI) on a ChatGPT subscription, so its calls were not billed per token. The other planners were reached directly at their providers' OpenAI-compatible endpoints with only `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` changed.

## Demo tasks

TODO(final): six-row table from the cold run.

## Held-out tasks

TODO(final): table from the cache-backed run of h1–h8.

## What I inspected

TODO(final): per task.

## Independent MCP client

[`evaluation/clients/inspector.md`](../evaluation/clients/inspector.md) is the transcript of `python scripts/client_check.py --paid`, which drives the server with the off-the-shelf MCP Inspector CLI 2.13.0, as a separate process.

- Over stdio it lists the 12 tools and the 4 resources, reads `bodhan://voices` and `bodhan://errors`, lists the prompt and fetches `plan_media_task`, and makes a free call. It also sends four bad inputs: an unsupported language, a missing file, an unknown voice and an unknown tool. Inspector refuses the unknown tool itself before sending, so the server's own `unknown_tool` error is covered by `tests/test_mcp_stdio.py` through the MCP Python SDK client instead.
- Over Streamable HTTP it checks that requests without a token, or with a wrong one, get HTTP 401. With the token it lists the tools and makes a free call.
- With `--paid` it makes one real one-sentence Bodhan translation per transport.

These calls exercise indic-translate only. The other three models are exercised through the same tool code by the agent runs above (indic-transcribe in d2 and d4, indic-speak in d1 and d5, indic-ocr in d1 and d3), and offline by the tool tests.

## Stretch goals

Each extra is shown by a command, a test, and a real run or transcript.

| Brief's stretch goal | Command | Tests | Evidence from a real run |
|---|---|---|---|
| Streamable HTTP with simple token authentication | `python -m bodhan_mcp --transport http --port 8765` | `tests/test_mcp_http.py` (official SDK client; missing/wrong token refused) | [`evaluation/clients/inspector.md`](../evaluation/clients/inspector.md): MCP Inspector over HTTP, 401 without or with a wrong token, then tool list and a real Bodhan call with the token |
| MCP resources or prompts | `bodhan://voices`, `bodhan://languages`, `bodhan://pricing`, `bodhan://errors`; prompt `plan_media_task` | `tests/test_mcp_stdio.py::test_free_tools_and_resources`, `::test_prompt_and_error_catalogue` | Inspector transcript: `resources/list`, `resources/read` of `bodhan://voices` and `bodhan://errors`, `prompts/list`, `prompts/get plan_media_task` |
| Self-evaluation script with a pass rate | `python scripts/run_suite.py [--ids ...] [--no-cache]` | `tests/test_scripts.py` (stale or provisional reports and planner outages never count as passes) | the suite summaries linked under Demo tasks, Held-out tasks and Other planner models |
| Parallel tool calls where steps are independent | independent calls in one planner turn run concurrently (budget reserved per call) | `tests/test_audit_regressions.py::test_independent_calls_in_one_turn_run_in_parallel_with_separate_costs` | d4 (five unlabelled clips), final cold run: the planner issued five `transcribe_audio` calls in one turn. All five live requests started 17.7 s into the run and finished between 18.8 and 19.1 s, instead of about five times as long one after another (see that run's `trace.jsonl` under Demo tasks) |
| Resumable tasks | `python -m bodhan_agent --resume --out ./out` | `tests/test_research_regressions.py::test_resume_after_a_hard_process_kill` (a child process is killed with no cleanup, then resumed without paying again), `tests/test_tier2_regressions.py::test_an_interrupt_mid_turn_keeps_the_turn_replayable` (Ctrl-C during a tool call, in a child process) | the tests run the real CLI code path in separate processes; every live run also leaves `out/.state/checkpoint.json` and a provisional report while it runs |
| Thin web UI with the plan and trace live | `python -m bodhan_agent.viewer --out ./out` | `tests/test_viewer.py` | polled during a live d1 run: steps went 3 → 6, artifacts 2 → 5 and Bodhan spend ₹0 → ₹1.11 while the run progressed, ending on its final status |

## Cost and runtime

TODO(final): cold cost and time per demo task, cache-backed cost and time, planner tokens.

## Run history and incidents

Earlier evaluation runs are kept, not overwritten. Times are UTC, on 19 November 2026.

| When | What happened | Effect |
|---|---|---|
| 09:18–09:37, 10:29–10:32, 13:49, 16:31 | Bodhan returned `429 Maximum rate limit reached for the model` in bursts, on indic-transcribe and indic-translate. | Retried with backoff and a shared per-model cooldown. No task failed because of it; it added wait time. |
| 12:44–12:45 | The planner endpoint returned `usage_limit_reached` (subscription quota). | Runs stopped with `llm_error` and honest reports. They were rerun later. |
| 13:51–13:57 | The account's Bodhan credit ran out during a suite run. | d1 and d2 stopped `failed` and h4 `partial`, each reporting the credit error. No output was presented as complete. The credit was topped up and those tasks rerun. |
| 16:39 | Planner quota exhausted again, during h5–h8. | Those runs stopped with `llm_error`. The suite scorer had counted three of them as passes, because "failed" was their expected status. The scorer now fails any run stopped by the planner being unavailable, whatever the task expects. |

## Limits of this evaluation

- Indian-language quality was screened automatically (back-translation chrF, back-transcription similarity, script and number checks), not by fluent reviewers. Those checks catch omissions, wrong languages, lost numbers and garbled speech. They do not prove fluency or naturalness.
- Six demo tasks and eight held-out tasks are a small sample of what the agent may be asked.
- The suites ran on Windows 11. Linux was checked in containers (tests, image build, the agent and server commands; see Environment), not on a native Linux machine.
