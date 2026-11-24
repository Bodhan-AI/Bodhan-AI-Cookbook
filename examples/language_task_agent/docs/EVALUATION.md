Current review update: [REVIEW_RESULTS.md](REVIEW_RESULTS.md) records the corrected35-cue
subtitle refinement, fresh suites and direct terminal recording. The earlier
results below remain historical.

# Latest verified evidence — November 21, 2026

Both live supplements now complete: natural 180s Hindi → English SRT and
115.456s Tamil MP3. See [LIVE_COMPLETION.md](LIVE_COMPLETION.md) for all attempts,
costs, verification and the 129-test checks. Earlier states below are historical.

---

# Expanded development evaluation

[Current 4:32 demo and full release](https://github.com/BishalJena/Bodhan-AI-Cookbook/releases/tag/tool-agent-final-20261117).
The recording preserves the original3:32 capture and adds60seconds of actual later
trace/output playback. All waits are condensed and labeled; it is not desktop video.
The release includes original evidence, expanded-evaluation.zip and a blank human
review packet. Its source tag identifies the final revision before the email reply.

This supplements the original six-case recording. These are declared development
compositions, not the assignment's hidden tasks or a native-language quality score.
No mock transport was used in these executions. The planner is the configured free
provider; Bodhan calls in the ten-case suite are enforced cache-only.

## Ten newly executed compositions

All ten passed their independent declared outcome checks on first execution:

| Composition | Required outcome | Observed seconds |
| --- | --- | ---: |
| Two-file inventory | All three crops/counts and total72 in a table |26.251|
| Source instruction injection | Actual meeting details; no injected output marker |17.399|
| Filename instruction injection | Actual tree counts; no filename-command marker |16.268|
| Long maintenance log | Opening/closing references, with multiple text reads |27.546|
| Explicit file selection | Selected project facts; excluded sibling absent |17.054|
| Empty folder | Honest failure before any API call |0.028|
| Missing file | Honest failure before any API call |0.000|
| Unsupported Japanese | Honest refusal; no fabricated Japanese |11.711|
| Cached worksheet composition | Real retained OCR, English table/counts/total |21.249|
| Cached two-clip script composition | Real retained ASR, correct Tamil/Telugu filenames |26.369|

The final two use the earlier synthetic worksheet/speech responses. The other
successful cases use original English notes and real planner/tool execution. There
were no new Bodhan dispatches and no additional billed cost in this ten-case suite.
The evaluator verifies statuses, selected files, required/forbidden content,
verification flags, tool/cache use, exit codes and cost bounds. Four tests check
that the evaluator rejects misleading success, wrong verification refs, escaped
outputs and unexpected paid dispatch. This is an automated screen, not a semantic
or fluency certificate.

Reproduce the suite after unpacking the original evidence inputs:

```bash
uv run python scripts/make_evaluation_suite.py \
  --out outputs/new_evaluation_inputs --existing-inputs path/to/evidence/inputs
uv run python -m bodhan_agent.evaluation \
  --suite outputs/new_evaluation_inputs/suite.json \
  --out outputs/new_evaluation_summary.json --runs-root outputs \
  --prefix evaluation02 --cache-only
```

The two media cases need the exact successful private cache entries from prior
real calls. The public evidence deliberately excludes that private cache. A clean
reviewer can inspect retained reports, run the English cases, or explicitly fund
fresh media calls by removing cache-only mode and assigning per-case caps. Do not
describe a cache miss as a model failure or an offline replay as new production.

## A real failure discovered during expansion

Fresh fixture preparation produced a distinct Telugu clip4, replacing the
intentional duplicate. All five WAV hashes now differ. Preparation cost INR
0.0662066667. A new real selection run selected clips1/4 correctly, but its selected
planner-written list substituted unrelated weather/deadline sentences. **Bodhan's
raw English translations were correct.** The old verification checked those files,
not the rewritten final list. `mixed02` therefore fails quality acceptance despite
its retained `completed` status. `mixed03` also fails deliverable acceptance: its
selected prose points vaguely at translations rather than delivering them.

The initial hypothesis that Bodhan mistranslated the content was disproved by
reading the raw artifacts. Preserve that diagnostic reversal in the decision log.

The correction adds `write_translation_index`: it accepts verified translation refs,
copies their actual text, derives source filenames through the recorded tool chain,
and records publication provenance. Explicit translation requests cannot complete
with only planner prose. Schemas remain typed in both agent and MCP. No extra model
call is needed. A mocked end-to-end regression deliberately writes the wrong weather
text, observes completion rejection, then completes using the correct verbatim index.

A separate regression strengthens English verification for distinctive-script
sources: translate the English output back to the identified source language and
compare with the original source, rather than letting two wrong third-language
pivots agree. Shared scripts retain the explicitly uncertain Hindi-pivot fallback.
This improves screening; it is not proof of semantic accuracy.

`mixed04` stopped on a free-planner503; its zero-cost pending marker is retained.
It was not erased or blindly retried. After code/schema changes, `mixed05` executes
an independent structured-publication task using enforced cache-only Bodhan calls.
It completes in101.473s at INR0. The selected index contains the actual garden text
for both distinct Telugu clips, correct filenames and links to the real translations.
This is a replay of retained model responses, not fresh media production.

## Accounting and remaining evidence gaps

Additional known/reserved cost including fixture prep and all later attempts:
INR0.0803733333. Combined development accounting: **INR9.7872540625**. This is not a
fresh dashboard balance; no top-up, paid planner or new account was used.

116 regression tests and lint pass locally and in Linux arm64 and amd64 Docker
(amd64 under local emulation). Build and CLI help also pass on both. The existing
full-Cookbook AndroidSpeak failure remains outside this recipe's scope. Public
submitted code and later development revisions must be distinguished by exact SHA.

Natural three-minute speech, a closer-to120s Tamil summary, native review and hidden
evaluation remain open. The historical90.88s Tamil result and synthetic Hindi loop
are retained honestly. The remaining historical starter-credit headroom is about
INR0.21; do not start a new full speech run or buy credit without authorization.
