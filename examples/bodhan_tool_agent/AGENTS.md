> Operational guide for AI coding assistants and humans working in this repo. **Read `SPEC.md` first.** SPEC defines what to build; this file defines how to work on it.

## What this project is

A two-package Python repo (`bodhan_mcp`, `bodhan_agent`) implementing an MCP server that wraps four Bodhan Indic models as tools, plus a CLI task agent that uses those tools via MCP to complete multi-step language tasks. Success is measured by generalization to unseen tasks, not by fitting the demo tasks.

## Golden rules (violating any = stop and reconsider)

1. **Never violate `SPEC.md` §3.** No non-Bodhan STT/MT/TTS/OCR. Ever. Including as a "fallback."
2. **Tools absorb model limits.** Chunking, pagination, timestamp remapping, retries — all live inside the tool. Callers pass a file and get a result.
3. **The LLM emits IDs, English, and plan structure only.** Not paths, not timestamps, not Indic script, not costs. Those come from code or Bodhan tools.
4. **The MCP server never crashes a client and never writes to stdout** outside the protocol stream. Logs → `sys.stderr`.
5. **Trace everything; fabricate nothing.** Every plan, tool call, verification, retry, injection hit, and cost lands in `trace.jsonl`. Impossible tasks end with an explanation and zero artifacts.
6. **Pre-flight before spend.** Estimate cost from file metadata + published prices before any model call. If the estimate exceeds budget, refuse with `status="budget_exhausted", stage="pre_flight"`.
7. **No demo special-casing.** If a code branch exists because of a specific demo task string, delete it.

## File ownership map

Each file owns one concern. Do not merge concerns. When adding behavior, add it to the file that owns it, not to the nearest convenient one.

| File / dir | Owns | May import from | Must NOT do |
|---|---|---|---|
| `bodhan_mcp/config.py` | Env vars, prices, language sets, limits | stdlib | Anything runtime |
| `bodhan_mcp/schemas.py` | Pydantic in/out models for every tool | pydantic | Logic |
| `bodhan_mcp/errors.py` | Error codes + `ToolErrorPayload` | pydantic | Raising through MCP |
| `bodhan_mcp/clients/base.py` | HTTP, retry+backoff, semaphore, cache, cost | config | Model-specific logic |
| `bodhan_mcp/clients/bodhan.py` | Four model wrappers | base, schemas | Chunking / pagination |
| `bodhan_mcp/utils/audio.py` | ffprobe, silence, chunk, remap | config | Calling models |
| `bodhan_mcp/utils/pdf.py` | Page rendering | config | OCR |
| `bodhan_mcp/utils/lang.py` | Supported sets, script detection | config | Translation |
| `bodhan_mcp/tools/*.py` | One tool each: validate, chunk/paginate, call, return | clients, utils, schemas | Agent logic |
| `bodhan_mcp/server.py` | FastMCP registration only (`from mcp.server.fastmcp import FastMCP`) | tools | Business logic; standalone `fastmcp` PyPI pkg |
| `bodhan_mcp/__main__.py` | `mcp.run("stdio")`, stderr logging | server | Anything else |
| `bodhan_agent/__main__.py` | Argparse exactly as SPEC §8.1 | loop | Logic |
| `bodhan_agent/resolver.py` | Inventory + task→file selection | MCP `inspect_media` | Model calls |
| `bodhan_agent/planner.py` | Plan prompt, plan validation | prompts, schemas | Executing |
| `bodhan_agent/budget.py` | Estimate, ledger, ceilings | config | Calling tools |
| `bodhan_agent/executor.py` | MCP client, ID→path, call, summary | mcp, trace | Planning |
| `bodhan_agent/verifier.py` | Back-transcribe, coverage, SRT, table checks | MCP tools, utils | Generating content |
| `bodhan_agent/untrusted.py` | Wrap + scan untrusted text | — | Executing instructions |
| `bodhan_agent/trace.py` | JSONL writer | — | Deciding |
| `bodhan_agent/reporter.py` | `result.md`, `report.json` | schemas | Calling tools |
| `bodhan_agent/loop.py` | Orchestrate plan→act→verify→stop | everything above | Containing tool logic |
| `bodhan_agent/prompts.py` | System prompt + plan schema | — | Runtime state |

If a change requires editing three or more files, write one line in the PR description explaining why the boundary is right.

## Workflow — build vertical slices

Each PR/commit should be the smallest change that makes one thing work end-to-end and adds a test. Suggested slices (do in this order, see SPEC §17):

1. Scaffold + `pip install -e .` succeeds.
2. One real transcribe call via `clients/` returns text + cost.
3. `transcribe_audio` tool works on a real clip, chunked and timestamp-remapped.
4. MCP Inspector connects over stdio and calls tools; bad inputs return structured errors.
5. All four tools + all helper tools pass their unit tests.
6. `resolver` handles single/named/ambiguous/plural with recorded rationale.
7. `budget` refuses a too-small budget with zero calls.
8. One single-step task end-to-end through `loop.py` writes all four outputs.
9. Verifier passes on generated audio and validated SRT.
10. `test_e2e_multi_step` passes with mocked models.
11. Six demo tasks incl. impossible; README + diagrams; 5-min video.

Do not start the agent before the MCP server is independently gradeable.

## Testing protocol

- **Unit tests mock all Bodhan HTTP calls.** Never hit the API from CI or `pytest` by default.
- **Every new limit-absorbing behavior needs a test**: chunking, pagination, remap, page loop, retry, cache hit.
- **Every new error path needs a test** in `test_errors.py`.
- **One E2E test with mocked models** must stay green: multi-step OCR→translate→TTS→verify.
- `pytest -q` must pass locally before any commit. If a test is flaky, fix it — don't skip it.
- Fixtures live in `conftest.py`: a short WAV, a small PDF, an image, a fake MCP transport, a fake LLM.

## Anti-patterns — reject on sight

- `print(...)` anywhere under `src/bodhan_mcp/`. Use `logging` to stderr.
- The LLM emitting a path, timestamp, cost, or Indic string.
- Any file-listing logic inside a tool (that belongs in `resolver.py`).
- Chunking or pagination in `planner.py` (that belongs in tools).
- Try/except that swallows an error and returns `{}` or `None`.
- `asyncio.run` inside a tool.
- Cache keys that include timestamps or file mtimes.
- Tests that hit the real Bodhan API.
- Special-casing a task phrase.
- Any import of whisper, tesseract, indic-trans, indic-translate (IndicTrans2), or cloud media APIs.
- Using the standalone `fastmcp` PyPI package (2.0); use SDK-embedded `from mcp.server.fastmcp import FastMCP` provided by `mcp>=1.2`.

## Definition of done (per change)

- Code + test in the same commit.
- `pytest -q` green.
- Traces include enough detail to replay *why* a decision was made.
- No new public function without a docstring stating purpose, inputs, and failure modes.
- SPEC §3 still holds.
- If you touched a tool's schema or description, re-run the MCP Inspector check.

## Debugging protocol

1. Reproduce with `--max-steps 1` or a single tool call via MCP Inspector before tracing the loop.
2. Read the last 10 lines of `trace.jsonl` — not the code — first.
3. If a tool result is wrong, isolate the tool with a fixture; do not "fix it in the agent."
4. If the planner chose wrong, check the prompt and the inventory it saw, not the executor.
5. If a test hangs, it is almost certainly an unbounded retry loop; cap it before debugging further.

## When stuck

- Ambiguous requirement: pick the option that generalizes to more hidden tasks, implement it, and record the decision in `result.md`'s "assumptions" and the PR's "Trade-offs."
- Ambiguous boundary between code and LLM: default to **code**. The rubric rewards control, not cleverness.
- Cost of verification too high: verify cheaper (character similarity, coverage counts) rather than skipping.

## AI assistant policy

Using AI assistants is expected. When you (the agent) use one, append a one-line note to the PR's **AI assistance** section: what tool, what it produced, and which lines you had to fix. Never commit AI output you cannot explain line by line.

## Commit / PR conventions

- Commit prefixes: `scaffold:`, `tool:`, `agent:`, `test:`, `docs:`, `fix:`, `chore:`.
- PR body uses the assignment's template verbatim (Summary / How to run / Architecture / Agent flow / Tool design / Where the LLM is used / Bodhan models used / Results / Demo video / Trade-offs / Cost / AI assistance).
- Never commit `.env`, `out/`, media files, or traces larger than 100 KB. Link them from the README instead.
