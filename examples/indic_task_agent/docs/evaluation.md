# Evaluation

This page records how the recipe was evaluated, what each result shows and what it does not. For setup and commands, see the [README](../README.md). For the design, see [design.md](design.md).

## How to read these results

Every run below is a real execution of `python -m bodhan_agent` against the hosted Bodhan API, with a real planner LLM. Four labels keep the evidence honest:

| Label | Meaning |
|---|---|
| **cold** | Run with `--no-cache`: every Bodhan result in the run was requested and paid for in that run. Its cost is the full cost of the task. |
| **cache-backed** | The agent and planner ran for real, but Bodhan results already in the shared cache (from an earlier run with the same input bytes and settings) were reused at no cost. Its cost is the extra spend of that run only. |
| **automatic checks** | What the tools and `scripts/run_suite.py` verify in code: back-translation, back-transcription, script, numbers, tables, duration, deliverable kinds and languages, status, budget. They screen for errors. They are not a quality score. |
| **inspected** | I read the output (and listened to the audio) myself. I am not a fluent reader of every language involved, so Indian-language quality beyond what back-translation shows is **not assessed** unless a row says otherwise. |

The `h1`–`h8` tasks are my own held-out tasks, written to resemble the kinds of hidden tasks the brief describes. They are not the employer's hidden tests.

## Environment

TODO(final): commit, OS, Python, Node, ffmpeg, dependency lock, planner model and endpoint type, date.

## Demo tasks

TODO(final): six-row table from the cold run.

## Held-out tasks

TODO(final): table from the cache-backed run of h1–h8.

## What I inspected

TODO(final): per task.

## Independent MCP client

[`evaluation/clients/inspector.md`](../evaluation/clients/inspector.md) is the transcript of `python scripts/client_check.py --paid`, which drives the server with the off-the-shelf MCP Inspector CLI 2.13.0, as a separate process.

- Over stdio it lists the 12 tools and the 3 resources, reads `bodhan://voices`, and makes a free call. It also sends four bad inputs: an unsupported language, a missing file, an unknown voice and an unknown tool. Inspector refuses the unknown tool itself before sending, so the server's own `unknown_tool` error is covered by `tests/test_mcp_stdio.py` through the MCP Python SDK client instead.
- Over Streamable HTTP it checks that requests without a token, or with a wrong one, get HTTP 401. With the token it lists the tools and makes a free call.
- With `--paid` it makes one real one-sentence Bodhan translation per transport.

These calls exercise indic-translate only. The other three models are exercised through the same tool code by the agent runs above (indic-transcribe in d2 and d4, indic-speak in d1 and d5, indic-ocr in d1 and d3), and offline by the tool tests.

## Stretch goals

TODO(final): one row per bullet, with command, test and run evidence.

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
- Runs were made on Windows 11. Linux was not tested locally (see the README).
