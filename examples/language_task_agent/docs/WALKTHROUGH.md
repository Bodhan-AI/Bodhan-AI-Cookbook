# Interview code-review walkthrough

This guide is preparation, not proof that the candidate has already reviewed the code. Explain every line and rehearse a small live change.

Read `runtime.py` first: typed errors, Decimal reservations, opaque refs, containment,
step/clock limits and final reports. Then `media.py`: lossless Unicode splitting,
ffmpeg without a shell, coarse subtitle timings and distinctive-script evidence.

Next read `gateway.py`: one HTTP boundary, env-only secrets, model/content fingerprints,
private cache, durable pending-call tombstones, total-call timeout, rate-limit backoff.
Why is a timeout potentially billed? Why must a retry wait for billing reconciliation?

Then `tools.py`: typed contracts, source references, per-model limits, table-aware
translation, round-trip checks, TTS back-ASR, all-page OCR and SRT serialization.
Which parts are arithmetic/conversion, and which need LLM judgment? What does each
verification score fail to prove? What happens if verification cannot fit the budget?

Finally read the MCP wrappers and agent runner. Show that a third-party MCP client
can use the server without the agent. Explain provider-neutral tool messages,
English-only planner writing, restricted capabilities and final artifact validation.

Run every test. Explain why a fake HTTP transport proves orchestration but not live
quality, actual pricing, provider compatibility or native-language correctness.
Show one hostile reference, a billing-uncertain timeout, a preserved table and an
impossible task. Never present a mock fixture as a successful real demo.

## Decisions to defend

- Model-level tools plus deterministic subtitle helper, not one demo-specific pipeline.
- Shared code imports internally, independent MCP externally, no needless extra process.
- No alternate ASR/OCR/translation/TTS fallback, even during vendor outages.
- Conservative reservations and no blind retries after uncertain billing.
- Round-trip scores are screening signals, not proof of semantic accuracy.
- Telugu script is useful evidence; Devanagari cannot distinguish Hindi and Marathi.
- Prompt-injection containment has capability limits, but prompts alone are no guarantee.
- Unknown provider tokenization/pricing prevents an unconditional all-in cost guarantee.

## Small live-change rehearsals

1. Add deterministic `inspect_audio`: duration/channels, typed schema, no model call,
   bad-format/reference tests, same helper exposed through MCP and agent.
2. Add male/female preference mapped only to the fixed voice library; retain defaults.
3. Change the audio-window constant and corresponding timestamp/limit tests without
   touching task strings. Explain precision versus call-count/cost tradeoffs.

Do not memorize prose. Deliberately break an invariant, observe the failing test,
repair it, and explain the diff. Simplify any code you cannot explain.

## Continuation, in plain terms

The optional execution window is an infrastructure accommodation, not a longer
budget. Before dispatch, Gateway asks whether the current process has room for a
whole call. A short window yields before a new charge. The runner retains the
assistant's chosen call and every answered tool-call ID; on resume it executes only
unanswered calls before consulting the planner again. A synthesis may restart its
local loop, but successful chunks are served from the exact request cache. A timed
out chunk is blocked by its persistent pending marker. That is intentional: the
client cannot tell whether the server already charged it.

JSON stores messages and known state, not Python objects or functions. The task,
provider configuration, limits, file paths and file bytes must match. A local HMAC
detects edits and metadata is single-use. Explain its threat model accurately: it
is useful integrity hygiene, not protection from a malicious process that can also
read the local integrity key. Do not distribute private checkpoint text or keys.

`elapsed_before + monotonic() - started` enforces cumulative active time. Waiting
between invocations does not count as active runtime. Do not claim a resumed run
was one uninterrupted five-minute process, even if its active time is below 300s.
The report records both resume_count and execution_kind.

## A ten-minute rehearsal you can actually do

1. Read the original assignment brief (kept privately) and explain the rule that every OCR, translation, ASR and
   TTS operation uses Bodhan. English planning is different from translation.
2. Trace one `synthesize` call through schema validation, text splitting, price
   reservation, HTTP/cache, audio inspection, back-transcription and final check.
   Inspect the real chapter report rather than relying on this outline.
3. Run the tests, then temporarily make `Run.resolve` skip containment. Identify
   the failing safety test and restore the original line. Do this locally, never
   in the submitted version.
4. Draw the request state machine: not dispatched -> reserved -> successful and
   settled, explicit rejected and released, or uncertain with reservation held.
5. Explain why round-trip lexical similarity cannot prove Tamil meaning, natural
   pronunciation or faithfulness. Native review remains separate work.
6. Explain what you would change with another day: natural recordings and native
   evaluation, more unseen task compositions, other Docker architectures, and stronger
   incremental processing if larger tasks require it. Do not call these done.

## Likely reviewer questions

**Why no broad auto-retry?** A timeout is not a rejection. Retrying could charge the
same synthesis twice. Only explicit retriable refusals use bounded backoff.

**Why not detect Telugu with an LLM?** The planner must not transcribe audio or
invent Indian-language text. We use Bodhan ASR and permitted script evidence;
ambiguous shared scripts remain unknown instead of falsely precise labels.

**Why a separate MCP service if the runner calls shared Python code?** The service
is independently usable by any stdio MCP client. In-process shared code avoids an
unnecessary subprocess for our runner without weakening that external interface.

**Is the budget a payment guarantee?** Bodhan bounds follow the assignment's
published prices. Unknown planner prices are explicit assumptions unless verified
rates are configured. Vendor misreporting or changing prices is not a client-side
payment guarantee. Actual and uncertain reserved amounts are distinguished.

## Reading preserved reports without confusing history and acceptance

The original chapter06 report was written before correction-lineage labeling was
fixed. It says `execution_kind: single_process` and `lineage: null`, and its final
explanation says to resume a paused window. Do not follow that old instruction.
`closeout_review.json` supersedes it: do not resume, no next carry, partial outcome.
`carry_provenance_review.json` records the later identical-context check and states
that it was not an original preflight check. D024 explains the code fix. The old
report/checkpoint are retained unchanged as history, not current instructions or
acceptance. Similarly chapter05's planner-written success claim is superseded by
its separate acceptance_review.json because83.592seconds did not meet the target.
