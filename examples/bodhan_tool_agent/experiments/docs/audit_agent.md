# Audit: Bodhan Task Agent (`src/bodhan_agent/agent.py`)

**Scope:** read-only audit of the agent loop, state/context, verification, budgets, retries/idempotency,
untrusted-content handling, and honest-failure behaviour. Files read: `src/bodhan_agent/agent.py`,
`__main__.py`, `trace.py`, `__init__.py`, `tests/test_e2e.py`, `tests/test_agent_cost_estimates.py`,
`src/common/{ledger,retry,paths}.py`, `src/bodhan_mcp/{server,cache}.py` (tool contracts only), and the
agent-loop/security sections of `README.md`. No existing file was modified.

**Assignment reference:** `assesment_descriptions/Agentic Engineer Take-Home Bodhan Tool Server and Task Agent.md`
(requirements lines 24–33, hard parts 46–54, LLM boundary 77–81, quality targets 124–132, rubric 242–251,
automatic-fail 253–259).

---

## Verdict

**Strengths.** This is a real per-turn ReAct executor, not a fixed pipeline with an LLM veneer. The LLM chooses
the next tool from the observation history (`agent.py:695–936`), so it can take an alternative path after a
tool error, and the Turn-0 forced `submit_plan` gives a structured goal/feasibility/assumptions record
(`agent.py:521–688`). The engineering around the loop is better than average: pre-flight Bodhan cost estimates
with fail-closed behaviour (`agent.py:288–338, 815–880`), a per-call budget circuit breaker, content-addressed
SQLite caching for all four paid tools (`server.py:346, 614, 815, 933`), read/write path containment
(`src/common/paths.py:59–88`, `server.py:108–139`), and an explicit model-facing tool allowlist
(`agent.py:205–240`). OCR and transcript observations are XML-quarantined (`agent.py:922–927`), and the trace /
report / run-archive layout is solid (`trace.py:180–347`).

**Is it good enough for complex multi-step planning? No, not yet.** The loop can execute a straightforward
2–4 step task when the LLM follows the prompt, but the two things the rubric weights most heavily —
**verification** and **honest completion** — are model-asserted rather than deterministic. The agent can return
`completed` with no artifacts, with a failed verification, or with Indic text the LLM wrote itself. The last is
an explicit automatic-fail condition in the assignment (lines 79 and 256), and the existing end-to-end test
actually codifies it (`tests/test_e2e.py:74–84, 128–146`). For the hidden set (ambiguous wording, missing files,
unsupported languages, plain-text prompt injection, tight budgets), the current design is likely to either
waste budget before refusing or claim success it cannot prove.

**Classification of the loop:** *plan once, then ReAct* — genuine action-level adaptation, but the plan is
write-once and display-only (`steps` is logged/reported but never tracked), with no explicit replan or
reflection step and no parallel tool execution.

---

## 1. The actual control loop

| Phase | Code | What happens |
| --- | --- | --- |
| Entry | `run_task_agent` `agent.py:457–463` | Receives `task`, `files_dir`, `out_dir`, `max_steps=30`, `max_cost_inr=20.0`. |
| Planning | `agent.py:521–568` | One forced LLM call with **only** `SUBMIT_PLAN_TOOL` advertised (`planning_tools = [SUBMIT_PLAN_TOOL]`, line 528) and `tool_choice` pinned to `submit_plan` (535). Planning failure returns `failed` with a report (551–568). |
| Plan parse | `agent.py:570–631` | First `submit_plan` tool call is selected (574–578); JSON is parsed with a broad `except` that silently yields `{}` (612–615). `goal`, `feasible`, `infeasibility_reason`, `assumptions`, `steps`, `target_language` are extracted (617–622). |
| Feasibility gate | `agent.py:633–665` | If `feasible=false`, returns `impossible` immediately with zero tool calls. This is model judgement, not a deterministic check. |
| Plan into context | `agent.py:680–688` | The plan assistant message is appended, followed by a synthetic tool result `{"status":"plan_approved", ...}`. The plan is never re-injected as a compact checklist, and `steps` is not tracked. |
| Execution | `agent.py:691–936` | `execution_tools = openai_tools + [FINISH_TOOL]` (691); loop `for step in range(1, max_steps + 1)` (695). Each turn makes one LLM call with `tool_choice="auto"` (707–714). |
| No-tool turns | `agent.py:736–764` | First empty turn gets a user nudge (740–746); two consecutive empty turns set `failed` (748–762). |
| Tool dispatch | `agent.py:766–933` | All tool calls in one assistant message are executed **sequentially** in the order returned (768). `finish` is intercepted first (776–811); every other call is pre-flight-estimated and budget-checked (815–880), then executed (884) and recorded (888–894). |
| Observation | `agent.py:922–933` | `transcribe_audio` and `extract_document_text` results are wrapped in `<untrusted_input_data>` (923–925); everything else is appended raw (927–933). |
| Finish | `agent.py:776–811` | Accepts `status`/`answer`/`artifacts`/`assumptions` from the model; logs, acknowledges, sets `finished=True`, breaks. No validation. |
| Max steps | `agent.py:937–940` | `for…else` sets `failed` if the loop exhausts without finishing. |
| Verification | `agent.py:942–958` | Only if `status == "completed"`; runs `_verify_outputs` and stores the result in `verification_results`. **It never changes `status` or `final_answer`.** |
| Report/return | `agent.py:960–990` | `trace.write_report_and_result(...)`; returns status/cost/steps/output paths. |

**Is it plan-act-observe-replan?** It is plan-act-observe, with implicit adaptation. The model can see a
`ToolError` observation and choose a different tool, so simple recovery works. It cannot revise the plan
(`submit_plan` is not in `execution_tools`), there is no plan-step status, no reflection/verification turn
before `finish`, and independent calls are never parallelized. The README's flow diagram promises
"Retry & Replan" and "Self-Correct / Re-synthesize" after a failed verification (`README.md:103–115`); the
code has neither an agent-level retry policy nor a verification feedback path.

---

## 2. Findings

### P0 — must fix before relying on the agent

**P0-1. Verification is advisory, non-gating, and vacuous.**
`_verify_outputs` (`agent.py:341–454`) sets `verified = True` when no artifacts are found:
`agent.py:446–452` explicitly says *"No artifacts to verify = vacuously true"*. It is called after the model
has already set `status` (`agent.py:942–958`), and its result is only written to `report.json`; `status` stays
`completed` and the CLI exits 0 (`__main__.py:95`). The existing nudge test returns `completed` with an empty
artifacts directory (`tests/test_e2e.py:294–308, 332–333`). This directly violates the assignment's
"Verify outputs before finishing" requirement (line 30) and the "no fabricated outputs or claims of success"
automatic-fail rule (line 257).

**P0-2. The LLM can author and persist Indic text; there is no provenance or script guard.**
`write_text_file` accepts any `content` and writes it (`server.py:1254–1263`). The docstring claims it rejects
audio files (`server.py:1245–1248`), but the implementation has no extension or content check. The end-to-end
test mocks the LLM writing Marathi directly and asserts `completed` (`tests/test_e2e.py:74–84, 128–146`),
which is exactly the assignment's automatic-fail behaviour (lines 79, 256). `finish.answer` is equally
unguarded (`agent.py:777–781`), and `synthesize_speech` accepts model-rewritten Indic text
(`agent.py:308–310` → `server.py:816–823`). A model can fabricate a translation, write it to a `.md`/`.srt`,
and claim success.

**P0-3. `finish` is model-asserted; `finish_artifacts` is computed but never used.**
`finish_artifacts = tool_args.get("artifacts", [])` at `agent.py:779` is dead. Nothing checks that the claimed
files exist, are non-empty, are inside `artifacts/`, or match the plan's target. `status` is taken verbatim
from the model (`agent.py:777`) even though the schema enum is `completed|impossible|failed` (`agent.py:144–147`);
an arbitrary string is accepted. A model can call `finish(status="completed")` after a failed tool call in the
same assistant message (all calls are processed; `finish` simply comes later), or with no tool calls at all.
The initial `status` is also `completed` by default (`agent.py:517`), so any unforeseen fall-through is
reported as success.

**P0-4. No translation coverage or audio fidelity verification.**
Text artifacts are only checked for non-empty content (`agent.py:422–443`): no target-script check, no
source/target length comparison, no Markdown table row/column preservation, and no coverage check. Audio is
checked only for `duration_seconds > 0.5` (`agent.py:361–370`); back-transcription is optional when the budget
allows (`agent.py:373–376`), uses auto-detected language rather than the plan's `target_language`
(`agent.py:378`), and passes if the transcript is longer than 5 chars (`agent.py:383`). It never compares the
back-transcript to the synthesized text, so repeated, omitted, wrong-language, or silent speech passes. If
back-transcription is skipped for budget reasons, the remaining checks can still make `verified=True`. The
plan's `target_language` (`agent.py:622`) is never used by verification.

### P1 — rubric and hidden-test risks

**P1-1. Prompt-injection quarantine covers only two tools.**
The wrapper condition is `if tool_name in {"transcribe_audio", "extract_document_text"}` (`agent.py:923`).
`read_text_file` returns JSON whose `content` is raw file text (`server.py:1219–1227`) and is appended
unwrapped. `translate_text` and `inspect_media` are also unwrapped. The system prompt only tells the model that
*OCR or transcription* is untrusted (`agent.py:87–89`), so a malicious `.txt`/`.md` input is a direct injection
path — and the hidden set explicitly includes prompt-injection text inside input files. The wrapper also
escapes only the closing tag (`agent.py:924`); a nested/opening tag is not neutralized.

**P1-2. No per-call timeout or overall deadline at the agent layer.**
`_execute_tool_call` is awaited directly (`agent.py:884`) with no `asyncio.wait_for`. The server's HTTP clients
have 60 s/90 s timeouts per request (`server.py:595, 708, 892, 1017`), but chunked transcription, multi-page
OCR, and sentence-by-sentence TTS make many sequential requests and can exceed the assignment's 5-minute
multi-step target. The OpenAI client is constructed without a timeout (`agent.py:472–475`), and `retry_sync`
can add three backoff sleeps (`src/common/retry.py:343–405`).

**P1-3. Duplicate paid-call prevention is cache-only, and the transcription cache key includes `output_path`.**
`make_transcribe_key` includes `output_path` in the hashed payload (`src/bodhan_mcp/cache.py:116–117`). The same
audio transcribed to `artifacts/a.srt` and then to `artifacts/b.srt` (or with and without `output_path`) is two
cache keys and two provider calls. `make_translate_key` includes `source_language` (`cache.py:87`), so an
explicit `source_language` followed by the prompted auto-detect form is also a duplicate. There is no in-run
fingerprint registry in `agent.py`; when the cache is disabled or misses, the same request can be sent again.
Cache hits are still charged nominal rates to the ledger (`agent.py:888–894`; README lines 153–157), so a
duplicate hit can falsely exhaust the budget even though no provider call was made.

**P1-4. Budget is per-call, not plan-level; LLM cost is excluded; actual cost can exceed the cap.**
The user prompt contains the task, file paths, and artifacts directory but **not** `max_steps` or
`max_cost_inr` (`agent.py:504–509`), so the planner cannot make cost-aware choices. Pre-flight checks only the
current call (`agent.py:815–880`); there is no aggregate estimate from input inventory. After the call,
`ledger.record_operation` adds the actual estimate without re-checking the cap (`src/common/ledger.py:73–91`;
`agent.py:888–894`). The translation pre-estimate uses `len(input_text)/3.5` as the output-token count
(`agent.py:302–307`) — not an upper bound; a longer translation can overshoot. LLM/gateway charges are
explicitly outside `--max-cost-inr` (README 153–157; `trace.py:271–276`), so the reported "never exceeds"
claim is only true for the Bodhan estimate, not total task cost.

**P1-5. No translation chunking or long-document strategy.**
`translate_text` sends the entire `text` in one request (`server.py:685–720`); unlike transcription, OCR, and
TTS, there is no chunk loop. The agent has no instruction to chunk long source text or to track which portions
were translated. A full-chapter or full-document translation can hit the model context limit and fail or be
silently truncated. This is also a ceiling on the "translation coverage" requirement.

**P1-6. Multi-file inventory is non-recursive and metadata-free; missing files are not signalled.**
`input_files_list = [str(p) for p in fpath.iterdir() if p.is_file()]` (`agent.py:499`) only lists top-level
files. It includes every file (including unsupported types), and the prompt provides no duration, page count,
or size (`agent.py:503`). A five-clip task or a directory with nested inputs is planned blind, so the LLM
cannot estimate cost or choose which clips to process first. If `--files` points to a nonexistent path, the
prompt says "No input files specified." (`agent.py:503`) rather than explicitly flagging the missing input; the
model has no deterministic signal for the missing-file impossible case.

**P1-7. The plan is write-once and not operational.**
`submit_plan` is only advertised on Turn 0 (`agent.py:528`); `steps` is parsed (`agent.py:621`) and only logged
and written to the report (`agent.py:668–678`, `trace.py:301–315`). There is no per-step status, no replan tool,
and no compact plan reminder in later turns. `target_language` is only logged. Recovery is whatever the LLM
improvises from a `ToolError`; the loop has no deterministic retry/replan policy, despite the README claim
(`README.md:103–115`).

**P1-8. Plan feasibility and target language are model-judged, with no deterministic validation.**
`feasible` comes from the plan (`agent.py:618`); if true, execution starts. `target_language` (`agent.py:622`)
is never checked against the supported set before paid calls, so an unsupported target can spend on OCR or
transcription before `translate_text` rejects it (`server.py:653–658`). Missing files are not checked before
planning. The system prompt asks the model to refuse unsupported targets immediately (`agent.py:61–66`), but
nothing enforces it.

**P1-9. Large tool outputs are appended in full and resent on every turn.**
`messages.append({"role": "tool", ..., "content": sanitized_content})` (`agent.py:929–933`) stores the whole
result string; there is no truncation, summarization, or artifact hand-off. Multi-page OCR text or a long
transcript is resent with the entire history on every subsequent LLM call. The trace truncates to 200 chars
(`agent.py:909`), but the model does not. This is a context-overflow / latency / cost risk and the opposite of
the assignment's "media must move between tools without flooding the LLM's context" guidance (line 49).

**P1-10. Audio format/extension is unvalidated; MP3 deliverables cannot be met.**
`synthesize_speech` only produces WAV (`server.py:824, 875–877, 915–921`); there is no `output_format` and no
conversion. If the model passes `output_path="summary.mp3"`, WAV bytes are written under an `.mp3` name, and
nothing checks the container. `write_text_file` also accepts `.wav`/`.mp3` and writes text, contrary to its
docstring (`server.py:1245–1248` vs `1254–1263`). The demo task "Tamil MP3" (`README.md:313`) and any hidden
MP3 request therefore produce a fake or mislabelled artifact.

**P1-11. Requested audio duration is never verified.**
The only duration gate is `> 0.5 s` (`agent.py:368`). A "two-minute" task that produces ten seconds passes.
There is no `expected_duration_seconds` in the plan and no regex/plan check against the task wording.

### P2 — quality, robustness, and documentation

- **P2-1. No run-state summary in context.** The model never sees remaining budget/steps. Add a compact
  `<run_state step="3/30" estimated_spend_inr="..." remaining_inr="..." artifacts="..."/>` line each turn.
- **P2-2. Trace is summary-only.** `result_summary[:200]` (`agent.py:909`), no model name, token usage, or full
  observation reference; `result.md` does not include verification results (`trace.py:289–344`). Replay is
  possible at a high level, not at the level the rubric asks for.
- **P2-3. Plan JSON parse failure silently defaults to feasible.** `except Exception: plan_args = {}` then
  `feasible = plan_args.get("feasible", True)` (`agent.py:612–618`). A malformed plan starts execution with no
  steps and no feasibility assessment.
- **P2-4. `finish` status is not normalized/validated.** `status = tool_args.get("status", "completed")`
  (`agent.py:777`); `"Completed"` or `"success"` is written to `report.json` and the CLI exits 1
  (`__main__.py:95`) despite apparent success.
- **P2-5. `max_steps` counts LLM turns, not tool calls.** One assistant message with five tool calls consumes
  one step but can incur five paid calls (`agent.py:695, 768`). Post-loop verification adds steps outside the
  limit (`agent.py:949`), and `total_steps` includes them (`trace.py:267`).
- **P2-6. No parallelization of independent calls.** The `for` at `agent.py:768` is strictly sequential. This
  is a stretch goal, but it matters for the five-clip task and the 5-minute target.
- **P2-7. Mixed message types and dangling tool IDs.** `messages.append(msg)` stores SDK message objects
  (`agent.py:680, 767`) alongside dicts; the tests use a plain mock class, so production serialization is not
  exercised. On an estimate failure (`agent.py:832–835`) the code breaks without acknowledging the remaining
  tool calls, unlike the budget path (`agent.py:867–872`).
- **P2-8. CLI argument validation is missing.** `--max-steps 0` and negative `--max-cost-inr` are accepted
  (`__main__.py:52–62`), producing confusing immediate failures rather than argparse errors.
- **P2-9. README drift.** README mentions a `verify_artifact` tool and `speaker_gender` (`README.md:111–115,
  315`), neither of which exists in the code; it shows `<untrusted_document_content>` / `<untrusted_transcript>`
  tags (`README.md:144–149`) while the code uses `<untrusted_input_data>` (`agent.py:925`). It claims
  translation coverage audits and self-correct/re-synthesize; neither is implemented. A reviewer will notice.
- **P2-10. Cache hits are charged nominal rates without a cache-hit signal.** `record_operation` always runs
  (`agent.py:888–894`). `synthesize_speech` returns `"cached": true` on a hit (`cache.py:617–624`), but the
  agent ignores it; text tools do not expose the flag at all. This distorts the budget and can halt a valid run.

---

## 3. Requirement table

| Requirement | Current behaviour | Gap | Recommended change |
| --- | --- | --- | --- |
| `--task`, `--files`, `--out`, `--max-steps`, `--max-cost-inr` | Implemented in `__main__.py:29–84`; defaults 30/20 | No bounds validation; non-recursive file discovery; no metadata | Validate args; recursive inventory with type/size/duration/pages; explicit missing-input flag |
| Plan before execution | Forced `submit_plan` Turn 0 (`agent.py:521–568`) with goal/feasibility/assumptions/steps | Plan is write-once, display-only; no budget/state in prompt; no replan | Keep Turn 0; validate target language; add compact plan checklist + run-state each turn; optional `replan` tool |
| Step-by-step tool calls | LLM `tool_choice="auto"` each turn (`agent.py:707–714`); calls executed sequentially | No parallel independent calls; no agent-level retry policy | Keep ReAct; add `asyncio.gather` only for independent free/paid calls with atomic budget reservation |
| Observation feedback | Full tool result appended (`agent.py:929–933`) | Large OCR/transcript floods context; only 2 tools quarantined | Truncate/summarize with full text saved to `artifacts/observations/`; wrap all file-derived results |
| Audio verification | Metadata + optional back-transcription (`agent.py:341–420`) | Length >5 only; no comparison to synthesized text; no language; skippable; non-gating | Record synth text per output; back-transcribe with target language; compare normalized text; check repetition/duration; gate finish |
| Translation verification | None beyond non-empty text (`agent.py:422–443`) | No coverage, script, length, or table check | Deterministic source/target length ratio, target-script ratio, paragraph/table row counts; use plan `target_language` |
| Finish / success | Model sets status/answer/artifacts (`agent.py:776–811`); `finish_artifacts` unused | Fabricated success; no artifact existence check; arbitrary status | `_validate_finish`: status enum, artifact existence/non-empty/containment, verification pass, deliverable coverage |
| Max steps | `for step in range(1, max_steps+1)` (`agent.py:695`); `else` fails (`937–940`) | Counts LLM turns, not tool calls; verification post-loop | Count tool calls or document the semantics; cap total paid calls; include verification in step budget or exempt explicitly |
| Max cost INR | Pre-flight per paid call (`agent.py:815–880`); ledger (`ledger.py`) | Per-call only; LLM excluded; post-call actual can exceed; cache hits charged | Put budget in prompt; aggregate plan estimate; conservative upper-bound translate estimate; post-call hard check; track LLM tokens |
| Per-call timeout | None at agent layer (`agent.py:884`); HTTP timeouts only | Long chunked calls can exceed 5 min; no overall deadline | `asyncio.wait_for` with configurable per-tool deadline; overall task deadline; surface timeout as observation |
| Retry | LLM `retry_sync` 3× (`agent.py:190–197`); server HTTP `retry_async` | No agent tool retry; no replan; blocking sleeps | Keep transient retries in server; don't blind-retry paid calls after timeout; expose error and let LLM choose; optional free-tool retry |
| No duplicate paid calls | Cache on 4 paid tools (`server.py:346, 614, 815, 933`) | Transcribe key includes `output_path` (`cache.py:116–117`); no in-run dedupe; cache hits charged | Fix key; add run-scoped `(tool, canonical args/file hash)` registry; return cached observation with zero marginal cost |
| Prompt injection | OCR/transcript wrapped (`agent.py:922–927`); prompt rule (`agent.py:87–89`) | `read_text_file` raw; translate/inspect raw; only closing tag escaped; no deterministic guard | Wrap all file-derived text; nonce-tagged blocks; escape all tags; provenance guard for tool args/artifacts |
| Honest failure | Plan `feasible=false` → impossible (`agent.py:633–665`); finish status (`776–811`) | Model-judged; no deterministic target/file/artifact check; fabricated success possible | Validate plan target against supported set; preflight missing files; finish gate; never accept completed without verified deliverables |
| Unstated language | `transcribe_audio(language=None)` auto-detect (`server.py:347–353`); `detect_language` script helper (`server.py:1288`) | Devanagari ambiguity; detection result not part of plan record | Keep auto-detect; record detected language as an explicit assumption; use it for verification |
| Ambiguous wording | Plan `assumptions` (`agent.py:620`) | No clarification (unattended is acceptable); assumptions not used downstream | Keep; surface assumptions in result.md; use them to choose default file/language |
| Multi-file inventory | Top-level `iterdir` only (`agent.py:499`) | Nested dirs missed; no durations/pages; no prioritization | Recursive walk, supported-extension filter, metadata table, cap and summary |
| Missing files | Empty list + "No input files specified." (`agent.py:503`) | No explicit missing-path signal; model may waste a call or fabricate | Detect at startup; add `<missing_inputs>`; if the task requires the missing file, deterministic `impossible` before paid calls |
| Unsupported language | Tool validates target (`server.py:653–665`) | Plan target not validated; upstream spend before failure | Validate plan target/subtags in agent; refuse before OCR/ASR |
| Media by path | Prompt lists paths (`agent.py:503–507`); tools accept paths; no base64 in context | Good | Keep; ensure all large outputs are files, not context |
| Trace | `trace.jsonl` per step (`trace.py:180–214`) | 200-char summaries; no model/token usage; no full observation reference | Add model, tokens, cost basis, observation path/length; include verification in result.md |
| Output layout | `result.md`, `report.json`, `trace.jsonl`, `artifacts/`, `runs/` (`trace.py:238–347`) | Good; verification absent from result.md; status can be false | Keep; add verification section; only write `completed` after the gate |
| LLM language boundary | Prompt only (`agent.py:56–59`) | `write_text_file`, `synthesize_speech`, `finish.answer` unguarded; e2e test violates it | Provenance registry + script guard; reject unregistered Indic runs in artifacts/answers; reject TTS text not traceable to a translate/source tool |
| LLM must not ASR/OCR/TTS | All four operations go through Bodhan tools; no other model | Good | Keep; audit any future helper that could bypass |
| Replan / recovery | Implicit from error observations | No plan revision, no reflection, no verification feedback loop | Add `replan` or allow `submit_plan` revision; add a pre-finish reflection turn; feed verification failures back as tool errors |
| Parallelism | Sequential (`agent.py:768`) | Five clips serial; latency target | Stretch: gather independent calls with per-call budget reservation |
| MP3 / duration | WAV only (`server.py:824`); no duration check | Mislabelled `.mp3`; 10 s "two-minute" audio passes | Add `output_format` + ffmpeg conversion; plan `expected_duration_seconds`; verify ±20% |
| Long translation | Whole text in one request (`server.py:685–720`) | Context limit; no coverage | Chunk by paragraph/sentence inside `translate_text` or a deterministic agent helper; track source coverage |
| Tests | 7 e2e tests (`tests/test_e2e.py`) plus cost estimates | Codify direct Marathi write and vacuous verification; no gate/timeout/dedupe/missing-file/coverage tests | Replace the Marathi-write test with a tool-translation path; add finish-gate, provenance, timeout, dedupe, missing-file, coverage tests |

---

## 4. Concrete, minimal-risk change plan

The loop structure is worth keeping. Do **not** rewrite it as a fixed pipeline. Add deterministic guardrails at
the three boundaries where the model currently has unchecked authority: **tool arguments**, **observations**,
and **finish**.

### Phase 0 — stop false success (P0, highest value, smallest diff)

1. **Add `_validate_finish(...)` in `agent.py`, called from the `finish` handler at `776–811`.**
   - Normalize `status` (`strip().lower()`) and reject anything outside `{completed, impossible, failed}`.
   - If `status == "completed"`:
     - Require `finish_artifacts` (the currently dead variable at line 779) to be non-empty when the plan
       declares a deliverable; resolve each path under `trace.artifacts_dir` with
       `Path.resolve().is_relative_to(...)`; require `is_file()` and `st_size > 0`.
     - Require the deterministic verification result (Phase 1) to be `verified is True`.
     - Reject if the last paid tool call in `trace.steps` has `status == "error"` and no later successful
       replacement.
   - On rejection, append a `role: "tool"` message with `ToolError: finish rejected: ...` and `continue` the
     loop (do not set `finished=True`), capped at 3 attempts. After the cap, set `status="failed"` with an
     honest answer.
   - Keep `impossible` allowed without artifacts, but require a non-empty `infeasibility_reason`/answer and
     zero fabricated artifacts.
2. **Make `_verify_outputs` gating and non-vacuous** (`agent.py:341–454`).
   - Replace `verified=True` for zero artifacts (`446–452`) with `verified=False` unless the plan explicitly
     says the task is answer-only.
   - Call it before accepting `finish(status="completed")` (inside the handler), not only after the loop. The
     post-loop call can remain as a final assertion, but it must downgrade `status` to `failed` if it fails.
   - On failure, feed a concise check summary back to the model as a tool error so it can re-synthesize or
     re-translate; do not silently accept.
3. **Add a run-scoped provenance registry for Indic text.**
   - In `run_task_agent`, create `indic_provenance: set[str]` and `synth_text_by_path: dict[Path, str]`.
   - In `_execute_tool_call` (or a wrapper in the loop), after a successful `translate_text` or
     `transcribe_audio`, add the returned text (and its normalized form) to `indic_provenance`.
   - In the `write_text_file` branch, reject content containing Indic-script runs that are not substrings of a
     provenance entry. English-only content is allowed. Normalize whitespace before matching. Return a
     `ToolError` telling the model to call `translate_text` first. This permits copying exact translated cells
     into a Markdown table but blocks model-authored Marathi/Tamil/Hindi.
   - In the `synthesize_speech` branch, if `language != "en"`, require `text` to be a substring of a
     provenance entry or of a source text obtained from `read_text_file`/`transcribe_audio` (for direct
     speech). This blocks the LLM from rewriting translated text before TTS.
   - Apply the same check to `finish.answer` for Indic runs; allow exact quotes only.
4. **Implement real verification content (deterministic, no LLM judge).**
   - **Translations:** record `TranslationRecord(source_text, target_language, result)` per call. For a
     plan-declared full translation, require normalized source length coverage ≥ ~80% across calls, target
     script ratio above a threshold (use `detect_language`'s Unicode ranges), and, for Markdown tables,
     equal row/column counts between source and target. For summaries, require a non-empty target and the
     plan's assumption that it is a summary; do not claim full coverage.
   - **Audio:** `synth_text_by_path[output_path] = text` at synth time. In `_verify_outputs`, back-transcribe
     with `language=target_language` (not auto), normalize both strings, and require a similarity ratio
     (e.g. `difflib.SequenceMatcher` or character 3-gram Jaccard) above ~0.6; flag repeated n-grams above a
     threshold. Check duration against `expected_duration_seconds` if the plan provides it (or a regex such
     as `(\d+)\s*(minute|min)`) with ±20% tolerance. If the budget cannot afford back-transcription, mark
     the audio **unverified** and fail the completion gate rather than passing it.
5. **Fix the finish artifact list and status.**
   - Use `finish_artifacts` in the report and final answer instead of ignoring it; if the model omits them,
     derive from `trace.artifacts_dir` and state that in the answer.
   - Do not default `status` to `completed` at `agent.py:517`; default to `failed` until the gate passes.

### Phase 1 — hidden-test robustness (P1)

6. **Pre-flight inventory and deterministic refusals.**
   - Replace the `iterdir` block (`agent.py:495–503`) with a recursive, extension-filtered walk. For each file,
     include name, size, and (for audio/pdf/images) duration/pages/dimensions via existing helpers. Cap the
     list (e.g. 50) and summarize the rest.
   - If `--files` does not exist, add `<missing_inputs>` to the prompt and, if the task names a file that is
     absent, set `status="impossible"` before the planning LLM call (or at least before any paid call).
   - Validate `plan.target_language` against `SUPPORTED_INDIC_LANGS` (and the script-subtag rules) before the
     execution loop. If unsupported and the task requires it, set `impossible` without spending.
   - If the plan JSON is malformed, re-ask once with a strict "call submit_plan" nudge; then fail. Do not
     default to `feasible=True`.
7. **Per-call timeout and deadline.**
   - Wrap the dispatch at `agent.py:884` in `asyncio.wait_for(..., timeout=config.tool_timeout_s)`.
     Suggested defaults: 30 s for free/local tools, 180 s for paid tools; expose
     `BODHAN_TOOL_TIMEOUT_S`. Return a `ToolError` observation on timeout; do not auto-retry a paid call that
     may have been received (see the retry module's own warning at `src/common/retry.py:286–290`).
   - Add an overall task deadline (e.g. `--max-seconds`, default 300) checked before each turn; on expiry,
     set `budget_exhausted`/`failed` with a partial-work summary.
8. **In-run dedupe and cache-key fixes.**
   - Add `executed_calls: dict[str, str]` keyed by `(tool_name, canonical_json(args))` plus file SHA-256 for
     file arguments. Before dispatch, if present, return the previous observation with a note
     "already executed; reused" and charge zero. This is independent of the SQLite cache and survives
     `BODHAN_CACHE_ENABLED=false`.
   - Fix `make_transcribe_key` (`cache.py:116–117`) to exclude `output_path` from the key (the output content
     does not depend on the destination name; `output_format` already covers format). Optionally normalize
     `source_language` to `"auto"` when the prompt says omit, or document the cache-key difference.
   - Add an explicit `"cached": true` flag to all cached tool responses (JSON envelope), or expose a
     contextvar from the cache decorator; charge zero marginal cost on a hit. If changing every return shape
     is too risky, at least have `_estimate_units` see the flag for `synthesize_speech` and skip the ledger
     charge on a hit.
9. **Context management.**
   - Add `_observation_for_llm(tool_name, result, step)` with per-tool limits (e.g. 12k chars for OCR/ASR,
     10k for read). When over the limit, write the full result to
     `artifacts/observations/step_<n>_<tool>.txt`, return a head/tail excerpt plus the path and a note to use
     `read_text_file` on it (if the file is in the artifacts root, reads are allowed).
   - Keep the plan as a compact `<plan>` block in the initial prompt and add a one-line `<run_state>` before
     each LLM call with `step`, remaining budget, and artifact names.
10. **Untrusted-content coverage.**
    - Extend the wrapper condition to `read_text_file` (wrap the JSON `content` field, or wrap the whole
      result), `translate_text`, and `inspect_media`. Use a per-run nonce in the tag, e.g.
      `<untrusted_input_data id="a1b2">`, and escape both opening and closing tag variants. Add a test with
      injection in a `.txt` file.
    - Update the system prompt to say *all* tool results derived from user files are untrusted, not just
      OCR/transcripts.
11. **Budget semantics.**
    - Put `<budget max_cost_inr="..." max_steps="..." />` in the initial user prompt and in the per-turn
      `run_state`.
    - Add a conservative translation pre-estimate: `max(input_chars, expected_output_chars)/3.5` or a fixed
      1.5× factor, and after the call, if `cumulative + actual > max`, set `budget_exhausted` and record an
      overage note. This does not recover the spend but makes the report honest.
    - Track LLM token usage from `resp.usage` and either include it in the ledger (using a configured price)
      or report it separately in `report.json` with a clear "excluded from cap" note. The assignment's
      "never exceeds `--max-cost-inr`" (line 130) should be interpreted as total task cost; document the
      interpretation explicitly if you keep the Bodhan-only cap.
12. **MP3 and duration.**
    - Add `output_format: Literal["wav","mp3"] = "wav"` to `synthesize_speech` and a deterministic ffmpeg
      conversion after synthesis; or, if avoiding new dependencies, reject `.mp3` requests with an explicit
      "only WAV is supported" error and record it as a limitation. Do not write WAV bytes under `.mp3`.
    - Add `expected_duration_seconds` to `SUBMIT_PLAN_TOOL` and use it in verification. For "two-minute"
      wording, parse a fallback with a small regex.
13. **Trace/report polish.**
    - Log `model`, `prompt_tokens`, `completion_tokens`, `observation_chars`, and an observation path in
      `trace.jsonl`; add a `verification` section to `result.md`; include `verification.verified` in the
      exit-code decision (already implied by the gate).
    - Normalize `finish` status; validate `max_steps >= 1`, `max_cost_inr > 0`.

### Phase 2 — planning quality (P1/P2, after the gates)

14. **Make the plan operational.** Either (a) add a `replan` tool with a `revision` counter and a compact
    plan-state object, or (b) keep the plan write-once but inject a deterministic checklist each turn:
    `completed: [...]; next: ...; blocked: ...`. The latter is a smaller change and enough for most hidden
    tasks. Feed verification failures back as a `ToolError` so the model can adapt.
15. **Long translation.** Add chunking inside `translate_text` (paragraph/sentence windows with overlap) or a
    deterministic agent helper that splits source text, calls `translate_text` per chunk, and records
    coverage. This also makes the coverage check meaningful.
16. **Parallelism (stretch).** For independent calls (e.g. five clips), use `asyncio.gather` with a semaphore
    and reserve budget per call atomically. Do not parallelize calls that share an output path.

### What to leave to the LLM

Keep the LLM responsible for: interpreting ambiguous task wording, writing English summaries/explanations,
choosing which tool to call and with which English/source arguments, deciding when a result is semantically
good enough, making and recording assumptions, and composing the final English narrative. Do **not** leave to
the LLM: success status, artifact existence, script/language compliance, translation coverage, audio
fidelity, budget/time enforcement, duplicate detection, or target-language validation. Those must be
deterministic code.

---

## 5. Test changes that would have caught these issues

1. Replace `test_e2e_multi_step_pipeline`'s direct `write_text_file` Marathi content
   (`tests/test_e2e.py:74–84`) with a mocked `translate_text` call followed by a write of the returned text;
   add a negative test that `write_text_file` rejects unprovenanced Devanagari/Tamil.
2. `test_finish_completed_with_no_artifacts_is_rejected`: mock `finish(status="completed", artifacts=[])`;
   assert the run does not return `completed` and `report.verification.verified is False`.
3. `test_finish_verification_failure_downgrades_status`: create an empty `.wav` or a silent/too-short WAV in
   `artifacts/`, call `finish(completed)`, assert `failed` and no exit-0 claim.
4. `test_translation_coverage_rejects_summary_as_full_translation`: mock a short target for a long source;
   assert verification fails.
5. `test_audio_backtranscription_compares_text`: mock a back-transcript unrelated to the synth text; assert
   verification fails (not just `len > 5`).
6. `test_read_text_file_injection_is_wrapped`: put `<untrusted_input_data>`-style text in a `.txt`; assert the
   observation is wrapped/nonce-escaped.
7. `test_same_audio_different_output_path_is_one_provider_call`: mock `_call_transcribe`; call
   `transcribe_audio` twice with the same file and different `output_path`; assert one call / cache hit.
8. `test_tool_timeout_returns_observation`: patch a tool to sleep; assert the loop gets a timeout error and
   continues/fails honestly.
9. `test_missing_files_reported_before_planning`: run with a nonexistent `--files`; assert `<missing_inputs>`
   or `impossible` and zero paid calls.
10. `test_plan_unsupported_target_language_is_impossible`: plan `target_language="fr"`, feasible=true; assert
    no paid call and `impossible`.
11. `test_budget_is_visible_to_planner`: assert the prompt contains `max_cost_inr`/`max_steps`.
12. `test_cache_hit_zero_marginal_cost`: force a cache hit and assert the ledger either records 0 or marks
    `cached=true`; document the chosen semantics.

---

## 6. Bottom line

The agent is a competent **executor** with unusually good cost plumbing and path hygiene. It is not yet a
trustworthy **planner/verifier** for the hidden set. The single highest-leverage change is the deterministic
finish gate: once `completed` requires a verified, existing, correctly-scripted deliverable, most of the
fabrication and false-success risk disappears. The second is the Indic provenance guard, which removes the
automatic-fail exposure created by `write_text_file`/`synthesize_speech`. After those, add the inventory,
timeouts, dedupe, and coverage checks — all small, local changes that preserve the existing ReAct loop.
