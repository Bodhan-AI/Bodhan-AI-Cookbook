# Selected technical decision history

Technical excerpts from the complete private append-only log. Omitted entries concern private development administration; numbering is preserved. Current acceptance is in docs/ACCEPTANCE.md.

## D001 - 2026-11-16 - Small shared Python core

Use typed Python tools, a thin stdio MCP server and an import-based CLI task agent.
No UI/HTTP/database/vector search. The brief tests tools independently and unseen
compositions, so one demo-specific pipeline would be the wrong boundary.
Status: implemented. Source: selected assignment, Tool granularity and MCP requirements.

## D002 - 2026-11-16 - Separate judgment from enforcement

Use an interchangeable tool-calling LLM for planning and English summaries. Enforce
paths, language-writing restrictions, model usage, budgets, serialization and media
conversion in code. All ASR/translation/TTS/OCR stays on Bodhan. Status: implemented;
real planner behavior still needs live validation.

## D003 - 2026-11-16 - Conservative cost control

Reserve upper-bound costs before each call, including verification and planning.
Require configured INR planner rates rather than guess a price. Keep uncertain
network failures charged conservatively and do not repeat them automatically.
Status: implemented/tested with fake transport; real billing must be reconciled.

## D004 - 2026-11-16 - Verification is a screen, not proof

Round-trip translations through a Bodhan English pivot; back-transcribe each TTS
chunk through Bodhan ASR. Record coverage, truncation and similarity and prevent a
failed check becoming a completed report. Native review is still needed. Status:
implemented/mock-tested. Thresholds are provisional until real examples are reviewed.

## D005 - 2026-11-16 - Honest timing and language uncertainty

ASR reference promises text but no word timestamps or language field. Keep coarse
25-second windows for SRT; use only distinctive scripts for deterministic language
evidence. Return unknown for shared/unclear scripts rather than guess Hindi/Marathi.
Measured speech duration is reported; exactly two minutes is not promised.

## D006 - 2026-11-16 - First cache was too short-lived

The initial gateway cached successful responses in memory for one run. Revised it
before handoff: a private disk response cache now avoids repeat paid calls across
reruns, and persistent pending-call tombstones prevent re-sending uncertain calls
after a crash. Exclusive pending-file creation prevents concurrent duplicate dispatch.
Status: revised implementation and cross-run tests pass. Cache contains sensitive
outputs, never keys, and must stay private/ignored.

## D007 - 2026-11-16 - Fix audio joining found by tests

An initial WAV join tried to set output parameters after frames had been written.
The multi-chunk speech test exposed the failure. Parameters are now set only once,
and following chunks must match channels/sample width/rate. Mock regression passes.

## D008 - 2026-11-16 - Replace packet-boundary segmentation

Initial ffmpeg segment output was near 25 seconds, but actual windows included
25.088-second packet boundaries. Tests/accounting exposed the discrepancy. Decode
once to PCM, then split on exact sample boundaries in code. This preserves exact
window timing and charges ASR for the actual submitted sample duration. Supersedes
the initial segmentation mechanism, not the 25-second design target.

## D009 - 2026-11-16 - Do not hide an upstream failing check

Our explicit recipe validation and lint pass. Cookbook `make check` fails on existing
AndroidSpeak structure. Reproduced on untouched upstream SHA
`ae51852960911cc78bd71401842308ecc5217510`. Do not modify unrelated files or claim all
checks passed. Record baseline failure and the recipe's separate evidence.

## D012 - 2026-11-16 - Live translation transport overrides stale docs

The first real probe OCR succeeded, but native `POST /translate` returned HTTP 404.
The signed-in reference at https://console.bodhan.ai/dashboard/api-reference/ instead
shows `POST /v1/chat/completions` with `target_language_code` and `target_script`.
On that route `max_tokens` was rejected (400), while `max_output_tokens` worked.
Revised the adapter to the live-supported Bodhan chat transport, with finish_reason
checks and normalized response shape. Updated fake transport tests to that contract.
The previously successful OCR was read from cache, not billed again. This supersedes
the initial native-route preference from public docs/Cookbook. Keep the mismatch
visible; do not claim documentation and production were identical.

## D014 - 2026-11-16 - Independent review corrections

Independent read-only review reproduced the tests, lint, MCP protocol check and live
ledger, then found interface and lifetime gaps. Fixed MCP restarts with fresh session
subfolders, stable in-session refs and serialized per-call step/time limits; retain a
cumulative session credit limit. Translation now compares actual table row/cell/span
shape. Accept Latin-extended names while still rejecting non-Latin planner writing;
this script filter does not prove English semantics. Preserve provider response fields
and expose optional token-cap field selection. Incomplete synthesis leaves a failed
verification entry; completed agent runs require a real generated output artifact.

The seven-variable evaluator environment previously failed before its first planner
call because two extra price variables were mandatory. Optional rates now default to
disclosed accounting assumptions INR 1000/3000 per million input/output tokens. This
is NOT a verified provider price or guaranteed cap on an arbitrary endpoint's bill.
Set verified conservative rates and provider-side limits before paid deployment. Our
own execution uses only the approved verified free tier with zero configured rates.
This supersedes the extra-variable requirement, not the no-paid-resource gate.

## D016 - 2026-11-16 - Bounded planner token-parameter negotiation

Focused re-review accepted the critical/high corrections and asked for a safe default
provider compatibility route. The planner now changes max_tokens only after a definite
HTTP 400 explicitly naming it as unsupported, unrecognized or unknown. It uses the
named max_output_tokens alternative or standard max_completion_tokens, at most once
per run, records the switch, and preserves that field for later turns. Successful
responses, vague 400s, oversized values, 5xxs and timeouts are never retried this way.
Tests cover the exact seven-env path, explicit fallback, ambiguous errors and a failed
second attempt stopping without a third parameter. Updated stale MCP lifetime docs
and added session cleanup/account-budget caveats. Unknown-provider accounting still
retains a conservative reservation (roughly INR 18 for a typical first planner turn)
on uncertain billing under a default INR 20 limit; this is deliberate, not actual cost.


## D020 - 2026-11-16 - Live-driven diagnostics and speech granularity

Normalize list-shaped provider error envelopes and retain sanitized rejection detail
plus Retry-After in traces. Original429details were not retained and cannot be
retrospectively asserted. Optional planner pacing is bounded, traced and tested.
Strengthen generic no-Indic-quotation instruction, avoiding repeated manifest reads.
Increase TTS chunks180to500UTF8bytes to avoid many tiny requests and vendor rate waits;
measure every generated chunk and refuse above30seconds before claiming verification.
This supersedes the initial180byte choice, not the Bodhan-only/short-speech constraint.
Fifty-three tests and lint pass, including measured-long-audio refusal, list-error
preservation, pacing and no-Indic-copy regressions. Material changes need focused
independent review. No recorded video, public submission or interview rehearsal yet.

## D021 - Optional safe-boundary JSON continuation

The execution environment interrupts long foreground commands before the ordinary
300-second task deadline. Add optional --execution-window and --resume, leaving
the required normal CLI unchanged. Pause only before a new request, retaining the
planner's exact pending tool batch, original inputs, cumulative active time, cost,
steps, refs and verification state. A resumed synthesis reuses successful chunk
cache entries and never clears uncertain-call tombstones. Wall time while paused
is excluded from active runtime and resumed evidence is labeled distinctly.

Serialization is JSON, never executable state. Exact task/config/limits and file
hashes are checked. Metadata is private, HMAC-integrity-checked and one-use to
prevent accidental edits and stale-budget replay. This is not a security boundary
against a malicious process running as the same filesystem owner. The local
integrity key is not an API credential and stays out of source/evidence archives.
Continuation metadata is not a model input or registered file ref.

## D022 - Input provenance is a caller disclosure

Add repeatable --input-note to carry fixture provenance into run assumptions.
The subtitles live demo's artificial 180-second loop is also explicitly annotated
in its existing report as a post-run provenance correction, without another
billed run or rewriting its autonomous history.

## D023 - Measured media is not enough if the requested length is wrong

The first real resumed chapter attempt selected an83.592-second MP3 and claimed
completion. Its duration was measured correctly, but it was too short for an
approximately two-minute request. Keep that result and original planner claim,
and record acceptance as partial. Do not call this a chapter pass.

Add a small deterministic output-constraint validator. It recognizes explicit
creation clauses with numeric/number-word output audio durations and audio file
formats, not named demo tasks. Approximate durations allow25% either side; exactly
allows1%. The selected actual media is measured at finish. Mismatches become typed
feedback for at most two autonomous corrections before a partial result. Input
recording length is not an output requirement. This is deliberately a narrow
English parser, not a claim that every natural-language constraint is validated.

## D024 - Carry lineage must be explicit and checked before dispatch

Chapter06 began a post-terminal correction with prior127.834seconds,16steps and
INR0.7628757292carried. The initial carry implementation checked numerical budgets
but did not prove identical inputs/config/limits before dispatch. This was a gap,
not retroactively validated authority. After stopping, saved context comparison
confirmed identical input hashes, config fingerprint and limits; record this as
a post-invocation review. The new implementation requires a prior run_context.json
with those exact fields, rejects missing/changed context and forbids second carries.
Reports/checkpoints retain budget_carried_correction lineage and source-report hash.
The global ledger counts only the increment, not the carried cost twice.

The correction hit Tamil403with a provider authentication error. The planner then
probed other languages, which did not fulfill the Tamil request. It paused after
an explicit429with Retry-After40. Stop this correction without another resume or
carry. New code hard-stops401/403before another planning turn; a test asserts only
one requested-language call. The chapter remains an83.592-second selected MP3,
which is partial against approximately two minutes. No silence padding, speed
changes or manually staged summary/audio were used.

## D028 - 2026-11-17 - Independent comparison for English translation

The old English-target path reused the same source-to-English response as its own
reference, producing a perfect score even for unrelated output. English-target
checks now translate the original source and resulting English separately through
Bodhan into Hindi, then compare those pivot texts. Hindi is a supported comparison
language, never an asserted input language. Other targets retain the English pivot.
An English-script check also flags unchanged Indic output; this can conservatively
reject English containing native-script quotations/names. Similarity is still a
screen and correlated model errors remain possible. Extra calls use normal budgets,
cache, completeness checks and failure handling, with no alternative providers.

Four mock regression cases failed on the old implementation and pass after the fix.
Full local suite: 73 passed; lint passes. No live model evidence or new model spend.
This supersedes D004 only for English-target comparison mechanics. Completion
validation remains a separate next change.

## D029 - 2026-11-17 - Required deliverables and verification coverage at finish

The initial finish guard required any generated artifact and rejected only recorded
failed checks. An English explanation could therefore complete a narration request.
Expand the generic output validator to common audio creation, read-aloud/narration
and subtitle clauses, independent of named demos. Require audio or SRT as appropriate.
Keep input recording duration separate, avoid negated narration/input descriptions,
and apply requested audio format and duration to the same output candidate.

Every selected audio/SRT/tagged translation must have its own matching checked and
passing verification. Translation tools now record explicit provenance, already
preserved by checkpoints. All global checks also require checked=true and passed=true.
Use existing bounded finish correction feedback; no new model calls or tool routes.
The parser is intentionally partial English syntax and does not certify requested
language, voice, content or arbitrary natural-language requirements.

Regression failures were observed before each production change, including the
narration, missing checks, unchecked checks, SRT and cross-file duration loopholes.
Full suite: 85 passed; lint and diff checks pass. No live model validation or spend.

## D030 - 2026-11-17 - Exercise the container and fix its test configuration

This host has Docker Engine 29.8.2. The real image build and CLI help passed, but
the initial verification script failed 10 async tests (75 passed). Mounting tests
at /checks/tests moved pytest discovery away from /app/pyproject.toml, so auto
async mode was not configured; cache writes also targeted the read-only test mount.
Pytest 8.4.2 documentation confirms explicit -c chooses config/rootdir. The script
now selects /app/pyproject.toml, puts cache in /tmp, and runs lint after tests.
Exclude git metadata, editor metadata and lint cache from Docker build context too.

Actual script rerun: image build, CLI help, 85 tests and lint pass on Linux arm64
with Python 3.11.18. One known MCP/Pydantic warning remains non-failing. No host .env
is passed into the test container and tests use mock transports. This is container
evidence, not a live-model or cross-architecture claim. Moving image/OS package
tags still prevent a bit-for-bit reproducibility claim.

## D031 - 2026-11-17 - Live source-language preflight and bounded correction

With local keys populated, a small real English/Tamil/English tool probe passed
both checks, including the new English-target Hindi pivot. Actual new cost INR 0.001.
An initial helper failed before dispatch on environment discovery and spent zero.
The next actual CLI narration sent English input directly to Bengali TTS; back-ASR
mismatched and the run honestly ended partial, INR 0.058240 and 46.606s retained.

Clarify the generic planner/TTS contract: inspect supplied text before narration,
translate when needed, and never expect TTS to translate. Known planner-written
English sources are deterministically rejected for non-English speech before any
reservation with translation_required. Arbitrary Latin-script inputs are not labeled
English because romanized languages share that script. A failing-before regression
and English/translated-source positive paths pass; all 87 tests and lint pass locally
and in the real Linux arm64 container.

One context-checked correction carried the narration's original task/input/limits,
time/steps/cost. It then read, translated and synthesized correctly: 8.96s Bengali
Ishita WAV plus text, both checks passed. Cumulative 20steps/107.192s/INR0.1268533333.
Keep both phases; do not call this fresh single-process evidence or carry it again.
New session spend including probe INR0.1278533333, combined historical ledger
INR5.1493689583; no fresh dashboard reconciliation or fluent review. See
LIVE_VALIDATION.md. No old chapter attempt, cache or uncertain tombstone was reset.

## D032 - 2026-11-17 - Isolate mocked tests from local runtime configuration

Once .env was populated, module imports loaded planner pacing into the test process.
The mocked suite still passed but took 72.07s instead of approximately two seconds;
cache-only and MCP limits could also change test behavior. An existing multi-step
regression was reproduced failing under inherited BODHAN_CACHE_ONLY=1 before the fix.

The autouse fixture now explicitly sets zero planner pacing, normal token-cap field,
cache-only off and valid MCP budgets/timeouts, alongside its existing mock credentials
and planner rates. Individual tests may still override configuration. Explicit values
also prevent a subprocess .env load from resurrecting host-specific limits.

Full suite passes 87 tests with hostile inherited settings (60s pacing, cache-only,
changed token field and zero MCP steps); lint/diff checks pass. This changes test
isolation only, not runtime defaults or the owner's local .env. No model calls/spend.

## D033 - 2026-11-17 - Lazy common-root fallback for explicit input sets

Both manifest discovery and checkpoint fingerprinting used a directory scan as the
third argument to getattr. Python eagerly evaluates that default even when explicit
input_files exists. Distant --files paths could therefore trigger a large scan of
an unrelated common parent before work began. Use an explicit None check and scan
only for the standalone configured-root case. An explicit empty list stays empty.

Four parameterized regressions (manifest/fingerprint, selected/empty) failed on the
old implementation and pass now; they forbid rglob and verify the selected file set.
Full suite: 91 passed locally and in the actual Linux arm64 container; lint/diff
checks pass. No model calls/spend. The previous source-only clean extraction passed
setup/87tests/lint/CLI help. Private archives/checkpoints exclude .env, caches/media
and retain reports/ledger separately. Old chapter state is absent and not reopened.

## D035 - 2026-11-17 - Table context, definite truncation and source clauses

The real worksheet hit429backoff and a low short-title similarity with per-cell
translation. Small tables fitting the existing1200byte limit now translate together,
with actual row/cell/span validation before publication and visible-text similarity.
Strip active HTML/non-structural attrs. Reject changed structures; larger tables keep
cell/text-node handling. New tests prove bounded calls, wrong visible content cannot
hide behind markup, and injected active markup is discarded.

The Hindi tool hit a cached definite truncated response. Split only finish_reason
length into smaller source inputs, at most two levels; keep the charged response and
coverage/cost evidence, never dispatch the identical input again. Unknown truncation
and network ambiguity do not trigger this path. Long repeated truncation still stops.

A later finish check misread subtitles covering a recording as generated audio.
Common source-clause boundaries now prevent that false positive. Four wording tests
failed first, then pass. Final Hindi replay is enforced cache-only with zero new
Bodhan cost; the prior live ASR/translation/recovery runs are retained. No hidden
score or live-first-attempt claim follows. Full local/container suite103passed.
Guard-removal mutation checks fail for active markup and the resplit bound.
