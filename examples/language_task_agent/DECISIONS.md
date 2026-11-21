# Decision log (append only)

New entries may supersede an earlier choice. Keep the earlier entry and link the
replacement. This record explains what changed and why, not a retrospective story
that everything worked first time.

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

## D010 - 2026-11-16 - Account/credit scope changed by the owner

Initially private code and mock tests only. The owner later selected one Bodhan
account, then explicitly allowed available development-credit use without per-call
approval. One account was created and confirmed to have INR 10 with INR 0 used.
No card/top-up/out-of-pocket payment or paid planner is included. Additional accounts
are not needed now. No public repo/PR/submission/email has been authorized.

## D011 - 2026-11-16 - Candidate-readable project continuity

Owner requested exact `AGENTS.md`, full selected `assignments.md`, and append-only
`DECISIONS.md`. Added them alongside the design, acceptance map and interview guide.
These are instructions/history for this project, not claims of completed live demos.

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

## D013 - 2026-11-16 - First real four-model probe is evidence, not completion

Original synthetic English page -> Bodhan OCR -> Tamil translation -> Tamil TTS ->
Bodhan back-ASR completed. Translation English-pivot similarity 0.8696; audio
back-transcription similarity 1.0. These are automated checks, not native review.
Total development ledger across three attempts: INR 0.2276289583; dashboard rounds
it to INR 0.23 used / INR 9.77 remaining. No card/top-up, planner call or public upload.
Only small adapters have real evidence. Six task-agent demos, planner compatibility,
long-document output quality, video and candidate walkthrough are still pending.


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

## D015 - 2026-11-16 - Original, deterministic demo inputs

Added a local generator for eight synthetic scanned teaching pages, worksheet table,
paragraph and a silent one-second video. Original documents contain no personal data.
Optional Hindi fixture loops the Cookbook sample to 180 seconds and copies its license.
It is an artificial chunking fixture, NOT a natural three-minute note. A hash/rights
manifest distinguishes these inputs. Worksheet and chapter rendering were visually
inspected. Generated media remains git-ignored; no model call is needed to create it.

Bengali-script detection remains intentionally unknown without a hint because Assamese
shares it. Do not label Bengali confidently from that shared block alone. Speech now
also returns an explicit warning on mismatched back-transcription. ASR reservations
use exact submitted PCM duration under documented per-second pricing; rounding or
changed vendor metering is not independently detectable without returned usage, so
reconcile dashboard totals and never claim that local accounting proves vendor billing.

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


## D017 - 2026-11-16 - Keep partial fixture failures and generation costs

Prepared five numbered clips using verified Telugu/Tamil synthetic speech, existing
licensed Cookbook Hindi, a Telugu speech chunk and verified English synthetic speech.
Original Hindi translation request returned403 with an upstream authentication detail
after adjacent calls succeeded using the same model key. No key rotation/new account
or blind retry. Preserve that partial attempt. Fixture generation counts as real credit
usage, not task-agent demos. All five development reports total INR0.3672891667; most
recent dashboard reconciliation predates fixture prep. Held-out language labels stay
outside input folders. Owner-approved free Gemini setup is awaiting Google phone
verification, not yet a usable planner. No paid fallback or six-demo claim.

## D018 - 2026-11-16 - Real free planner and current account verification

Gemini API setup completed under the selected account. Billing page explicitly says
No billing account, API keys page Free tier. No billing activation. Current model-list
API accepts the key, but listing does not prove generation availability: 2.5 Flash-Lite
is retired for new users (explicit404). First3.8Flash request timed out and was not
blindly repeated. Actual3.5Flash-Lite tool-calling works; current pricing says free
input/output on the Free tier. Configure its verified-zero accounting rates explicitly.
Source: https://aistudio.google.com/docs/pricing and /docs/openai. Account rate-limit
page later loaded:3.5FlashLite15RPM/250KTPM/500RPD. No paid fallback. Optional planner
pacing at5seconds/request keeps tests below observed quota; generic default is0.

## D019 - 2026-11-16 - Preserve actual live failures and cache provenance

Real autonomous CLI runs completed Marathi table translation, Bengali female narration,
Telugu-clip selection/translation and full180s Hindi English subtitles. French-video
request was honestly refused. Inputs are synthetic except the licensed Hindi sample;
the180s note is looped, not natural. These do not establish hidden-test/native quality.
Mixed/subtitle completed reruns reused successful cached Bodhan calls from preceding
partial runs; no manually staged outputs were supplied to the agent. All earlier
charges remain in the development ledger. Bengali first attempt was externally
interrupted during planner waiting; explicitly marked partial, no Bodhan spend there.

The chapter remains partial. All8pages were OCR'd; a checked47.5s Tamil audio was made
in one run, but it was too short for2minutes and the planner attempted expansion.
Bounded100-110s foreground execution windows expired. Do not present the partial audio
as a2minute completed deliverable or keep repeating fresh paid runs. One uncertain
speech call's reservation/tombstone remains. Combined development ledger across all
reports:INR4.2087198958 known/reserved; last dashboard reconciliation predates these
runs, so do not describe that ledger as a fresh dashboard balance.

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

## D025 - Enforced cache-only recording and current service recheck

The user requested remaining work again at22:47Nov16. One small synthetic current
Tamil service probe forwarded successfully but mandatory back-translation rejected
with403. CostINR0.00058, no retry, no alternate-language probing. Current console
showedINR5.02used/4.98remaining, so empty credit was not the cause established here.
Closed chapter correction and uncertain tombstones remain untouched.

For a reproducible end-to-end recording without more Bodhan churn, add optional
BODHAN_CACHE_ONLY=1. Known responses are reused normally; an uncached Bodhan request
fails before any reservation/dispatch. Planner remains configurable and live/cache
behavior is logged. Cache-only mode is part of the continuation config fingerprint
so a paused no-network run cannot silently become a paid run on resume. Unit test
asserts zero HTTP calls/reservations on a miss. No fake/model fallback responses.

Actual new CLI executions completed worksheet Markdown and Hindi English SRT and
refused French. An initial worksheet defaultHTMLcachemiss and Bengali uncachedvoice
attempt remain failed/partial. The timestamped subprocess/trace capture is rendered
with explicit reading pauses, not represented as an uninterrupted desktop screen.

## D026 - Keep video and live model evidence together

Independent review accepted the139secondactualCLIcaptureagainst the brief's literal
several-tasks/impossible-case/time requirement. No desktop reshoot is needed.
README/PRdraft link the owner-only recording together with DEMOS and original/new
live evidence/ledgers, so cached zero-new-cost runs cannot replace actual model
proof. Publishing a view-without-sign-in link, repository or submission remains
separately gated. Current private links require the owner's signed-in account.

## D027 - 2026-11-17 - Private development repository and review baseline

The owner selected BishalJena/mcp-bodh as the development repository and authorized
pushes and PRs targeting it. Bootstrap main with ignore rules, then propose the
source import on a feature branch. This does not submit to the Cookbook or send
email. Keep the assignment brief intact as the external requirements document.

Fresh uv sync --frozen, 69 local tests, lint and CLI help pass. Docker daemon
responds on this host; no image build has been run during this review. Two small
mock-only probes expose unresolved gaps: an unrelated English translation passes
self-comparison at 1.0, and an English text artifact can satisfy finish for a Bengali
narration task without audio/checks. These are review findings, not live-model
quality results or implemented fixes. Private original live evidence is not bundled
in this extracted source checkout.

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

## D034 - 2026-11-17 - Owner-authorized closeout and local acceptance inputs

The owner authorized merging the stack and finishing the project except their own
review/understanding. PRs1-7 were merged in dependency order with head-SHA checks.
Browser/Drive access was unavailable, and the owner then explicitly prohibited Drive
access. No archive was downloaded. Keep old closed water-cycle attempts and unknown
billing history untouched; build a distinct original eight-page school-garden case
and synthetic local fixtures. All further activity is rooted in this folder.

The first new chapter stopped within its INR3.10 cap at INR2.8844847917. The speech
headroom still reserved INR0.20 for ASR despite rejecting chunks over30s. Correct it
to the documented30s maximum cost INR0.05. Remove fixed English-word duration claims
from generic planner guidance. A shorter independent task attempt reuses cached OCR,
finishes in142.299s at INR0.8716667708, and produces90.88s Tamil MP3+text. This is at
the low end of declared90-150s approximate timing, not exactly two minutes. Both
attempts remain in the development ledger; combined attempts exceed five minutes.

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

## D036 - 2026-11-17 - Complete local evidence with explicit provenance

A fixture request returned403upstream-auth detail. The same key authenticated the
read-only model list at200; current public docs allow a bounded auth retry after
recent success. One retained retry produced verified Telugu/Tamil/Hindi, then stopped
at the next403. No rotation/new account or automatic auth loop. Native /translate
was also checked once against the current documented route and returned404, zero
settled cost. Runtime remains on the live-supported Bodhan chat transport.

Assemble the remaining clip inputs offline from verified speech: clip4 duplicates
clip1, clip5 reuses the verified Bengali output. Hindi is an artificial180s synthetic
loop, not a natural note. Labels remain outside model input folders. This is input
fixture preparation, not manually staged agent outputs. All five supported case
shapes now have mechanically checked results, plus honest French refusal. Bengali
retains its budget-carried correction; subtitles' final replay is cache-only.

All new final-suite charges, failed attempts and fixture prep total INR4.5575117708;
with the earlier INR5.1493689583 baseline, accounting is INR9.7068807292 known/reserved.
This is not a fresh dashboard balance. The original INR10 credit allocation was not
increased. No top-up/card/paid planner. A3:32recording renders actual CLI captures and
traces with clear timing/provenance labels. Fluent/candidate review remains pending.
Cookbook validation/lint/rules pass; make check retains the independently reproduced
AndroidSpeak baseline failure. Publication bundles must exclude keys/cache/private
source and keep original failure evidence. The assignment brief stays private.

## D037 - 2026-11-17 - Publish the fixed submission and preserve handoff

Owner instructions authorize completing the project and merging development PRs.
PRs1–8 are merged in the private development repository. Publish the self-contained
recipe in the public Cookbook fork and open upstream PR21 at exact commit
2a99a3392f08bc5b5ca95cdbb8e6ce18d32886b1. Preserve that evaluation commit; upstream
merging remains the maintainers' choice. Public export excludes the private brief,
internal instructions, keys, caches and source media; selected technical decisions
are explicitly labeled excerpts, while this complete log remains append-only.

The release tool-agent-demo-20261117 holds the212second recording and complete
evidence ZIP. Anonymous downloads returned200 and matched local hashes; all220
evidence manifest entries match. Actual configured credentials were absent from
published sources, PR text, evidence and private Git history. Fresh recipe export
and private source archive extraction each pass103tests. Docker remains103passed.
Exact final Cookbook make check again fails only at the documented AndroidSpeak
baseline; explicit recipe validation/lint/rules pass.

No email was sent: the original assignment thread is unavailable. Prepare an exact
PR/SHA reply in docs/SUBMISSION.md. Candidate understanding/rehearsal, native review,
hidden evaluation and maintainer review cannot be claimed. No further paid model
calls; ledger remains INR9.7068807292 known/reserved, not a reconciled balance.

## D038 - 2026-11-17 - Distinguish public check status from local evidence

Both public Socket Security checks pass. GitHub validate run44002395770 reports
action_required with no jobs executed at the submitted SHA. Do not label this a
successful CI run. Record maintainer-side action separately from the local
103tests, recipe validator and known full-check baseline failure. Do not alter
the fixed submitted commit to bypass upstream workflow policy.

## D039 - 2026-11-17 - Expand actual compositions after owner requests completion

Owner asks to do remaining work after the provisional assignment assessment. Build
an installed optional evaluation module, original English fixture builder and four
oracle tests. Ten newly declared compositions pass actual CLI executions: inventory,
two injection cases, pagination, explicit input selection, empty/missing input,
unsupported Japanese, and two cached OCR/ASR compositions. No mocks in these runs;
no Bodhan dispatch in the ten-case suite. Do not call this a hidden-test score.
The first evaluator test import failed in Docker because it assumed a checkout's
scripts folder; moving the evaluator into the installed package fixes collection.

Within remaining historical credit, fixture_prep03 generates a distinct Telugu clip4
through Bodhan and reuses other verified clips. All five hashes differ. Actual new
fixture cost INR0.0662066667, followed by mixed02 INR0.0130866667 and mixed03 INR0.00108.
No top-up, account change or paid planner. New combined accounting INR9.7872540625;
it is not a dashboard-reconciled balance. About INR0.21 historical headroom remains.

## D040 - 2026-11-17 - Correct the publication boundary and diagnostic reversal

Inspection finds wrong weather/deadline sentences in mixed02's selected English
list despite passing translation checks. The initial hypothesis blamed Bodhan's
translation; raw artifacts disprove that hypothesis. Bodhan translated correctly;
the planner changed the content while writing its list. mixed03 avoids the wrong
sentences but still selects vague prose rather than delivering the translations.
Retain both reports unchanged and mark their final acceptance failed in the review.

Add a typed write_translation_index helper to agent/MCP. Code accepts only checked
Bodhan translation refs, derives actual source filenames from tool ancestry and
publishes the text verbatim. Explicit translation requests must select actual
verified translations/SRT or the index; English planner prose alone cannot satisfy
them. The failing-before regression observes missing deliverables, then the agent
corrects using the index. No demo-specific routing or model call is added.

A separate failing regression demonstrates how two wrong Hindi pivots could agree.
For distinctive-script English-target sources, compare the original text directly
with output back-translation to that source language. Ambiguous/shared scripts keep
the uncertain Hindi pivot. This was not the cause of mixed02's publication failure.
Strip redundant planner schema titles to keep the first default-price request
within its existing budget; retain types/limits/descriptions and full MCP schemas.

mixed04 stops on a free-planner503, with zero-cost reservation/tombstone retained.
After code/schema changes, an independent mixed05 structured-publication task
completes in101.473s at INR0 with enforced cache-only Bodhan responses. Selected
index contains the actual garden translations and source filenames, not invented
sentences. All production and failed/corrected evidence stays separate. Native
review, natural long audio and a closer two-minute summary remain unperformed.
Ask whether the assignment email was already sent before moving its fixed public
evaluation SHA; continue improvements privately while that status is unknown.

## D041 - 2026-11-17 - Complete cross-architecture and human-review preparation

The full116test suite, lint, Docker build and CLI help also pass on Linux amd64
under local emulation; arm64 passes natively. No API keys or calls are needed by
these container checks. Prepare a separate66file review packet with artifact hashes,
source material and blank forms. It is not a human review or candidate rehearsal.
Keep the existing submitted commit fixed while email status is unknown; publish
later code/evidence as an explicitly separate review revision rather than silently
moving the evaluation head. Public materials must exclude private brief/instructions,
environment, caches and unrelated files. Original reports/assets remain unchanged.

## D042 - 2026-11-17 - Owner confirms email unsent and requests PR update

The owner answers that no assignment email was sent and explicitly requests updating
the PR. This supersedes D041's temporary frozen-head assumption. Update Cookbook
PR21 to the corrected source before the email reply; retain the original commit/tag
and reports as history. A272.04second recording preserves the original212seconds
and appends60seconds of later actual trace/output playback, with provenance labels.
The final release contains both original and expanded evidence plus the prepared
human-review packet. No hidden/native score, candidate participation or email send
is claimed. Record the new exact source SHA in the private handoff/email draft.

## D043 - 2026-11-17 - Final pre-email revision and source extraction

Cookbook PR21 is updated to 2fcb3537096ed62d4e0584fd33e42d21fd78e72b after the
owner's explicit unsent-email confirmation. The final release contains the 272.04s
demo, original evidence, 181-file expanded manifest, prepared human-review packet
and exact source archive. Credential/manifest checks pass. Clean source extraction
passes frozen setup, 116 tests, lint and both CLI helps. Docker arm64/amd64 checks
pass (amd64 emulated). Both Socket checks pass; validation workflow 44020192987 is
action_required with no jobs. The unrelated full-Cookbook baseline failure remains.

Private development PR10 is merged. Update the handoff/email draft to the new SHA;
preserve old commits/assets as history and never imply an email was sent. Remaining
credit/output/naturalness limits, native/candidate review and maintainer action are
explicit. The human-review packet is preparation only.

All five final release assets were fetched without authentication with HTTP 200
and matched their local SHA256 hashes. The current public PR head was read back
as the exact final SHA. Source/runtime code matches the verified extraction; the
last private change records handoff metadata only.

## D044 - 2026-11-21 - Separate optional review, input preparation and live cost

Owner asks what prevents improving Tamil timing, Hindi input and fluent review,
correctly noting that opening the Cookbook PR fulfills the submission mechanism.
Upstream merging is not a submission prerequisite. The brief assigns native review
to its assessors; our own fluent reviewer would be optional assurance. Earlier
readiness language bundled these with engineering work too broadly.

Prepare a continuous Hindi input from HealthPhone's published recording, creator
Nand Wadhwani, under CC BY-SA4.0 with source checksum/attribution and modifications.
Keep the source and provenance outside the model input folder. Initial container
seeking gives179.9565625s and is rejected; exact decoded-sample trimming produces
180s/2,880,000 frames without looping, padding or speed changes. No model calls or
charges. This is input preparation, not a completed ASR/SRT demo.

Fresh180s ASR alone needs at least INR0.30, exceeding the approximately INR0.21
recorded headroom; wallet balance is unknown because supplied model keys cannot
access management budgets. Tamil's closer120s result similarly needs real generation
and verification, not cosmetic duration manipulation. Original Bodhan speech
weights are gated; no local copies or configured Hugging Face access are present.
No new account/terms acceptance/top-up/paid planner. Preserve the current public
evaluation SHA and deliver this preparation via the private development PR workflow.

Export the retained Tamil summary's Bodhan English roundtrip in enforced cache-only
mode, with zero new dispatches/cost. English-side review finds all eight topic groups
recognizable, with compost/fertilizer ambiguity and awkward sentence joins. These
may be back-translation artifacts and do not establish Tamil errors or native review.
Retain the findings, original-report hash and90.88s timing limitation explicitly.

## D045 - 2026-11-21 - Run until actual credit refusal rather than infer exhaustion

Owner explicitly directs running the natural Hindi SRT task and Tamil timing
revision, then clarifies to continue useful model work until the API indicates
exhausted balance. This supersedes treating the historical INR10 allocation minus
our conservative ledger as a hard remaining-wallet limit. Keep all prior uncertain
reservations; no tombstones are erased and no hidden reconciliation is claimed.

Start actual CLI/capture runs with bounded budgets: Hindi INR0.80/300s/35steps and
Tamil INR2.20/300s/40steps. Use existing keys and the configured free planner. No card,
top-up, paid planner or extra account is authorized. Record accepted calls, actual
costs and reservations even if the combined development estimate exceeds the old
starter allocation. A service refusal, not an inferred balance, will establish a
credit blocker. Non-credit errors remain distinct and uncertain requests are never
blindly re-sent. Preserve every attempt and verify actual artifacts before success.

## D046 - 2026-11-21 - Actual provider credit exhaustion observed

Both authorized fresh runs execute real model calls and receive HTTP 429
ExceededBudget with no Retry-After. Hindi accepts six 25s ASR requests (150 of 180s,
INR0.25) before the next request is refused. Tamil reuses all eight OCR pages,
writes a new English summary and accepts INR0.00462 of translation, then its next
translation/verification request is refused. No new Tamil speech or full Hindi SRT
is produced; both original CLI reports are retained as failed, not completed.

The provider reports Spend=INR10.041874062499994, Budget=INR10.0. Our combined
known/reserved ledger becomes INR10.0418740625 after INR0.25462 of new calls, matching
provider spend to rounding. This is an actual service-side exhausted-budget signal,
not the earlier inferred headroom. Stop new paid calls as instructed. No card,
top-up or paid planner; no uncertain reservations or pending markers are erased.

Recover the first 150 seconds through the same Bodhan transcription tool in enforced
cache-only mode, using all six retained real responses at zero new cost. Its report
is explicitly partial: 30 seconds and English subtitles remain missing. Full original
reports/captures remain unchanged. Keep raw provider user identifiers private;
the handoff includes a redacted exhaustion proof and complete local ledger.

## D047 - 2026-11-21 - Enforce explicit timing ranges before completion

The fresh task explicitly asks for 115–125 seconds, but deterministic finish parsing
previously retained 90–150 seconds from approximate two-minute wording. Four tests
fail before correction: to/hyphen/en-dash intervals including decimal minutes and
rejection of 90.88s against 115–125s. Explicit numeric output bounds now override
approximate timing. This is generic validation, not demo-task routing. Actual live
attempts above predate this private fix and remain failed on provider budget; no
successful new speech is implied. Full local regression suite: 120 passed and lint
passes; Linux arm64 and amd64 container suites also pass all 120 tests and lint
(amd64 under local emulation). Published evaluation SHA remains unchanged by the private development fix.

## D048 - 2026-11-21 - Owner replaces exhausted development credentials

The owner supplies four replacement keys through a local TextEdit entry file and
confirms saving. Apply only the four Bodhan settings to the ignored environment
file; validate all four values without displaying them, preserve planner settings
and keep owner-only file permissions. No key, environment file or private cache
belongs in source, PRs or evidence archives. No account or payment action is taken.

Resume actual CLI work with the new keys and the existing successful response
cache. Keep all earlier failures, costs and uncertain pending markers. The previous
provider budget refusal remains true for that earlier run; accepted requests with
replacement credentials establish usable access, not a verified new wallet balance.

The natural Hindi correction completes with eight verified English SRT windows
covering 0–180 seconds. Its report carries INR0.25 and 19.522s from the earlier
failed attempt: cumulative case accounting INR0.32626 and 91.444s. Incremental
new accounting is INR0.07626, not another INR0.32626. A marked rate-limit refusal
with Retry-After 52 is waited out; it is distinct from credit exhaustion. Timing
remains coarse 25-second windows, and verification is not fluent-language review.

## D049 - 2026-11-21 - Bound optional planner reasoning and output capacity

The replacement-key Tamil run produces verified renders of 101.717333, 128.768,
108.629333 and 110.677333 seconds, all outside the strict 115–125s target. It stops
partial at its 600-second cap with INR4.4370455208333333 retained. A changed-limit
budget carry is rejected before dispatch; preserve that guard. A separate calibrated
attempt stops on a free-planner timeout at zero new cost; keep its pending marker.
Later calibrated attempts are independent and all costs remain in the lifetime ledger.

Google's current migration guidance recommends medium/high thinking for autonomous
tool-calling agents; the configured Flash-Lite model defaults to minimal. Add optional
provider-neutral LLM_REASONING_EFFORT and LLM_MAX_OUTPUT_TOKENS. Omitted effort and
1500 tokens remain defaults. Validate effort values and the integer128–32768 range
before dispatch. Reserve the full configured output cap, retain it on the one allowed
explicit token-parameter switch, and bind both settings to continuation fingerprints.
Nine regression cases fail before correction and pass after; full local suite129
passes with lint clean. This does not establish hidden-task or fluent-language scores.

Sources: https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.6
and https://ai.google.dev/gemini-api/docs/openai . The pricing page still lists this
model's free tier, including thinking tokens: https://ai.google.dev/gemini-api/docs/pricing .
No model, account or billing-plan switch is made. Test medium effort/4096 tokens with
the already configured free planner; preserve every earlier failed attempt.

## D050 - 2026-11-21 - Verified natural Hindi and timed Tamil; refresh unsent submission

Medium-effort correction completes in 240.763s/30 steps for INR1.1771269792. Its
selected MP3 measures 115.456s within 115–125s. All translation and nine back-ASR
checks pass. Independent PCM comparison confirms an exact join of generated chunks.
All eight OCR pages and final matching text refs remain retained. Natural Hindi
produces checked English SRT covering 0–180s.

Replacement-key accounting adds INR5.7592725; lifetime known/reserved accounting
is INR15.8011465625. Count carried Hindi cost once. Preserve the rejected carry,
free-planner timeout, step-limit partial, four full timing misses and partial work.
Final-run latency passes; cumulative attempts exceed five minutes. All 129 tests
and lint pass locally and in Linux arm64/amd64 (amd64 emulated).

Owner reconfirms no assignment email was sent. Refresh the existing Cookbook PR
and linked evidence; preserve prior releases/SHAs. That status reply does not
authorize sending email. Native review belongs to assessors. Candidate review and
rehearsal are not claimed. No development attribution or co-author trailer is added.

## D051 - 2026-11-21 - Required assistance disclosure

Document Codex use for implementation, review, tests, debugging, documentation
and evaluation tooling in README and the assignment PR. Candidate understanding
and rehearsal are not claimed. Automatic co-author trailers are not added.
Current review references identify115.456s Tamil, natural180s Hindi and the later
distinct clip batch; original reports and releases remain historical.

## D052 - 2026-11-21 - Improve subtitle context and readable estimated cues

Subtitle output formerly placed an entire25s translation in one cue; the natural
Hindi example also contains awkward wording near a boundary. Add bounded optional
ASR windows (default25s, maximum30s) and pause preference. Subtitle generation uses
30s context and selects nearby detected pauses when available. Music in the retained
HealthPhone input prevents the fixed quiet threshold finding suitable pauses; no
pause-boundary improvement is claimed for that source. Fresh30s ASR is evaluated
separately; original cached25s results remain untouched.

Code wraps Bodhan's unchanged translated words into short cues, targeting two
42-character lines and six seconds. Times are equal estimated subdivisions within
ASR windows, never word alignment. Sparse speech or unbreakable words may exceed
readability targets, disclosed in assumptions. No translation words are authored
or corrected by the planner. PCM splitting preserves every sample exactly once;
fixed/pause/window variants have separate paths and transcription cache identities.
Conversion and pause analysis share one timeout deadline.

Seven regression cases fail before correction and pass after; full suite136passes.
Schema growth also exposes the existing required-settings budget test: estimate
compact wire JSON bytes plus the unchanged4096-byte framing allowance rather than
counting serialization whitespace not sent by httpx. Full output-cap reservation,
unknown-price assumptions and uncertain-call markers remain intact.

## D053 - 2026-11-21 - English review rejects a screened30s result;20s improves examples

The30s subtitle experiment passes heuristic checks but English review identifies a
wrong-way/right-way contrast error. A structured-context Bodhan table probe still
misstates that contrast. Neither is accepted as a semantic quality improvement.
Keep original reports and costs. This demonstrates why lexical back-translation
is a screen, not proof, and why a completed report can fail later output review.

Choose a configurable20s subtitle default after actual comparison, retaining
25s default transcribe and the bounded30s maximum. On the natural HealthPhone
recording,20s pause preference finds an initial14.82s boundary that separates the
headings. The resulting English retains both wrong/correct labels and replaces
the old clothes-on-hands phrase with germs. No text is hand-edited; all recognized
and translated words remain Bodhan output. The new run completes in84.061s for
INR0.3135721875. Independent checks find37contiguous cues spanning0–180s, maximum
5s, two lines and42characters per line. All10source-window checks pass. This is
narrow English-side evidence, not complete semantic or native-speaker certification.

The isolated source suite passes136tests and lint. Earlier experiments, flags and
uncertain reservations stay retained; no published evaluation commit is changed
until the final authorized refresh.

## D054 - 2026-11-21 - Freeze fresh evaluation answers; fix plain JSON input support

Generate13fresh declared compositions with expected answers outside model-facing
folders. Freeze input/expectation hashes before execution. Include arithmetic,
chronological updates, pagination, explicit selection, injection, filtering,
negated translation, zero model budget, malformed PDF, unsupported speech, empty
input, fresh two-page OCR and Bengali speech. These are not the assessors' hidden set.
The primary raw first run passes11/13. Pagination has correct values with different
key names; the task did not explicitly call them keys, so retain the strict-oracle
failure and label that ambiguity. The ready-crates case exposes a real gap: JSON
was excluded from supported text. Add bounded raw JSON reads under the same
untrusted-data boundary; never evaluate or execute its contents. Three tests
fail before correction and pass after.

Strengthen the evaluation oracle with exact JSON values/shapes, allowing one
Markdown fence and requiring exactly one selected artifact. Negative tests reject
wrong keys/values, extra fields and misleading extra artifacts. Two new negative
cases fail before correction; the positive case remains passing. The existing
substring checks are retained for prose cases. A per-case call timeout supports
explicit model latency bounds. New full suite142passes with lint clean.

A second existing free planner, gemini-3.5-flash, passes7/11 raw on the English/
failure subset after the JSON fix. Three requests time out; one returns an explicit
free-tier daily quota refusal (20requests/day on its resolved model). Stop that
model without topping up or opening accounts. Preserve those failures and pending
markers; do not turn them into a claimed hidden or cross-provider pass rate.
Follow-ups will separately test JSON support and clarify the requested key names;
they cannot replace the recorded first-run denominator.

## D055 - 2026-11-21 - Record actual terminal execution and consolidate current evidence

Private PRs 15–17 disclose assistance, improve subtitle context/readability and
fix JSON inputs with stronger declared evaluation. Both explicit follow-ups pass;
retain raw 11/13 primary and7/11 alternate scores. Stop the alternate after its free
daily-quota signal. Do not hide the three timeouts or redefine those as successes.

Record actual PTY CLI/MCP sessions instead of result cards: invoice, cached 37-cue
subtitles, official client error/survival checks, French refusal, actual retained
Anitha preview and cached Bengali narration. All independent recorded checks pass.
Retain original timestamps/argv/exit codes and scan joined raw/ANSI-stripped
transcripts for five configured credential values. Export 97.84s with disclosed
idle shortening, reading pauses and relative path display; audio rate unchanged.

Move the previous acceptance chronology intact into docs/history with corrected
relative links, and make the current acceptance/review pages concise. Current
source passes 142 tests/lint locally and on Linux arm64/amd64 (amd64 emulated).
All 33 new reports add INR1.10641; lifetime known/reserved INR16.9075565625, not
wallet balance. Prepare a refreshed public PR/release and email draft after all
checks. Preserve 614ba65and earlier releases. No email send or human review claimed.

## D056 - 2026-11-21 - Reserve ASR cost from the actual PCM frame count

Pause-boundary timestamps expose float subtraction noise:113.3113125→133.3113125
represents exactly20s of PCM but subtraction is20.000000000000014. Use the
actual WAV frame count/sample rate for Decimal cost reservations, not display
timestamps. The initial rounded test did not reproduce this; the real sample-
derived boundary fails before correction and passes after. Current suite143passes
locally. Recheck both containers before publication. No earlier costs/reports are
rewritten and no new model dispatch is needed. Cache keys, payloads and generated
media are unchanged. The recorded142-test source remains identified by its SHA.

D056 validation update: both actual Linux arm64/amd64 containers pass143tests and lint (amd64 emulated).

## D057 - 2026-11-21 - Add a guided film with separate editorial narration

On the owner's request, build a 255s/1080p30 HyperFrames walkthrough with real PTY
excerpts, original-rate task audio, progressively built diagrams and safe captions.
Preserve the 97.84s capture and prior releases. Shortened paths/omitted repeat lines,
visible task abbreviation, cache replay and retained artifacts are explicit.

The owner then requested VoiceStudio MCP narration. Use the existing local 0.5.7
MCP engine and a saved designed Documentary Narrator–US profile, never a personal
clone. Seventeen takes are editorial voiceover outside the agent, slowed 10% and
normalized; task-agent model operations remain Bodhan-only. Keep narrator and
original demo listening intervals disjoint. A local original sound bed is ducked.

Independent visual review flags crowded/disconnected diagram nodes, terminal
clearance, missing clip decisions/legend and static staging; fix them. Runtime
QA catches Tamil overflow and ghosted crossfade text; fix excerpt bounds and fade
outgoing text before blending backgrounds. Final lint/runtime/layout/contrast/
craft checks have 0 errors/0 warnings. Captions 36px end at/before924, reserving 156px
below. Actual 255s encoded video is −18.70LUFS/−4.69dBTP; no human listening claimed.

VoiceStudio transcription rejects a large body 413 and then denies the per-take
transcribe call 403. Retain these errors. A real Bodhan CLI transcription of all
concatenated takes costs INR0.243473125; fixed 25s cuts lose some boundary phrases.
Check each of 17 takes independently using the real Bodhan stdio MCP instead:
INR0.2151402083. One last-take 429 is explicitly rejected, charge 0; retry after 76s.
All narration content is represented; retain name/homophone/number differences
without claiming semantic or native proof. Production QA totals INR0.4586133333;
lifetime known/reserved INR17.3661698958, not wallet balance. An initial QA summary
variable-shadowing bug is repaired from unchanged per-take records, without new
model calls. No task score is altered, and no payment/account/email action occurs.

Publish the new film and editable source separately from prior recordings, update
the private PR/public existing PR links and email draft, and retain source-only
archives/checkpoints. Only presentation/docs change; runtime/test sources remain
identical to the 143-test snapshot. No candidate understanding is claimed.

D057 final validation:54caption cues include37verbatim narrator groups plus17
listening/explanation labels. All caption midpoints pass the150px control-band
layout check. Two contrast warnings select canvasblue behind the white Tamil
panel; actual encoded pixels show white, yielding6.49:1label/13.89:1body contrast.
No visual change is needed. Standard contrast check is clean; retain both reports.
