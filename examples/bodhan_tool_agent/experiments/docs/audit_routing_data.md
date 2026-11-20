# Audit: Routing benchmark v2 (in-session rerun, 2026-11-19)

> **Note (2026-11-19).** The body of this audit describes the **v2 benchmark (76 cases)** as it
> existed at audit time and is kept verbatim as the historical record. It was superseded the same
> day by **v3**: 87 cases, splits 37 train / 21 dev / 29 test, category quotas 21/19/14/17/9/7,
> difficulty 20 hard / 42 medium / 25 easy, test coverage 10/10 tools and 6/6 categories,
> `dataset_sha256 = 2c3abeefd189128415a7efd7ff67826d8099e2c98630c39edd83b67ae92a8eb4`, provenance
> audit `v3_multistep`. See the Resolution section below; do not read the body's figures as
> current.

Scope: `experiments/data/routing_cases.jsonl` (76 cases), splits, `README.md`,
`validation_report.json`, `tool_catalog_snapshot.json`, and the builder gates in
`experiments/scripts/build_routing_dataset.py`.

## Verdict

**Not good enough as the yardstick for complex multi-step planned work.** It is,
honestly, a good deterministic next-action suite: phase separation, exact-match
scorer, no tool-name leakage, frozen sha, real edit pipeline. It measures
*single-step routing under engineered confusions*. It does **not** measure the
things the assignment gates on: trajectory dependence (step N chosen given step
N−1's actual output), verification strategy choice, retry/replan after a failed
step, Indic multi-script long-document nuance, or plan quality. For the stated
goals (multistep nuances for Indian-language knowledge work), v2 is a strong
base to grow, not a benchmark to trust yet.

## What is solid

- 10 deterministic gates in the builder (schema, phase/tool match, accept ==
  [expect], no leakage, arg keys ⊆ schema, context files exist, finish status
  enum, duplicate task/id, exact split quotas, 70-case total — see quota issue
  below). Credit where due: these are the right invariants.
- Designed quota matrix, not a random draw; every tool + category appears in
  every split; `finish.status` decision rule is documented and consistent
  (impossible = absent capability/file/language/containment, failed =
  safety/budget/genuine error, completed = done).
- Phase legality is clean (`plan`→`submit_plan` only; `act`→everything else).
  76/76 fields complete, `acceptable_next_tools == [expected]` in 76/76, arg
  constraints all within tool schemas and type-correct. sha-256 matches
  `validation_report.json` exactly.

## Hard integrity failures (P0 — dataset fails its own gate today)

Gate 6 (`context_files` exist under RECIPE_ROOT) fails for **11 of 76 cases**,
because `sample_data/` was reorganized into `speech/`, `text/`, `documents/`,
`images/`, `edge_cases/` subdirectories after the benchmark was built, and the
builder was never re-run:

- `sample_data/hindi.wav` → C-13, C-15, K-06 (K-06 also plan-phase, see below)
- `sample_data/tamil.wav` → R-04, E-04
- `sample_data/bengali.wav` → E-08
- `sample_data/hindi_kavya.wav` → E-13
- `sample_data/hindi_email_image.jpg` → C-10, R-09, R-12
- `sample_data/fail_unsupported_french_video.webm` → K-05

`validate()` reports these and *refuses to write* in the current tree, yet the
committed `validation_report.json` shows `issues: []` and matches the frozen
sha — proof that the committed dataset is **not reproducible from the current
source tree**. The dataset was built against a filesystem layout that no longer
exists.

A second integrity drift: **README vs data**. README claims "70 next-action
decision points" with 31/15/24 splits; the canonical file has **76** cases in
**33/17/26**. Anyone rebuilding from the README's numbers will fail quota gates
or mis-report GEPA splits.

## What the data cannot see (multistep nuance gaps)

| Failure mode of complex multi-step work | Cases on disk | Where |
|---|---|---|
| Step ≥ 2 decision grounded in prior tool output | **13** of 76 (all `multi_step_pipeline`) | P-01…P-13 |
| Non-empty `history_summary` anywhere | **16 / 76** (median 105 chars, max 226) | pipelines + P/17 |
| **Error/recovery history** (tool failed → replan/retry) | **0** | — |
| Budget-exhaustion mid-pipeline | 3 mentions; only P-10 has explicit ₹ numbers | P-04, P-10 |
| Verification *choice* (back-transcribe vs coverage/residue check vs skip) | touched, never decided between | P-06/P-07 |
| Long Indic document pipelining (OCR → translate chunks → glossary consistency) | **0** | — |
| Code-mixed / romanized Indic input | **0** (`sample_text_10_hinglish_chat.txt`, `text_23_tanglish_chat.txt` exist but unused) | — |
| Register/terminology consistency across chunks | **0** | — |
| Devanagari/script ambiguity (Hindi vs Marathi vs Sanskrit…) | 1 case ("shared script" note lives in `detect_language` doc, M-cases lightly cover) | M-01.. |
| Unsupported Indic language → honest refusal | ≥1 (R-03/R-08?); covered only for non-Indic French | K-05, R-* (verify per id) |
| Plan-phase infeasibility | **6 plan cases, ALL categorized `control`** (incl. K-05 impossible plan); feasibility gate unscored as a category | K-01…K-09 |
| Duplicate paid call avoidance (same input → same model twice) | **0** | — |

Histogram reads:
- Difficulty: 24 easy (32%), 39 medium, 13 hard. Test split: 4 easy / 15
  medium / 7 hard — acceptable, but train split has **15 easy / 5 hard**, so
  GEPA is mostly optimizing against easy confusions.
- Tool balance: `finish` 19 (25%), `translate_text` 11; `submit_plan` 6;
  **dev split has zero `synthesize_speech`** (Pareto selection never sees TTS
  decisions); test split: `transcribe_audio` 6, `finish` 6, everything else
  1–4 — one case per tool is not measurable at 4.2 pts/case.
- `multilingual_script` category is the smallest (6), and contains nothing
  hard; it is the exact category the hidden set leans on.

## Recommended v3 scope (this is the actionable part)

Author v3 as a **new** file (`routing_cases_v3.jsonl`), keep v2 frozen for
regression, gate with the same validator + new quota. Minimum viable additions:

1. **Fix the 11 dead references** (repoint to `sample_data/speech/…`,
   `sample_data/text/…`, `sample_data/edge_cases/edge_fail_unsupported_language_pair.txt`-style targets; or point v2-only cases at `_archive` fixtures). Re-run builder; commit regenerated
   snapshot + README table together.
2. **≈20 new step≥2 cases** across 5 archetypes:
   - OCR → translate → verify long document (use `edge_doc_mixed_script_bilingual.pdf`,
     `documents/sample_doc_04_bn_mixed_scripts.pdf`, `documents/sample_doc_05_mr_dense_text.pdf`)
   - Short-audio pipeline with verification strategy choice (use existing demo*/sample_speech_*)
   - **Error recovery**: history contains a *failed* OCR/transcribe (e.g.
     corruption) → correct next call is retry w/ different tool or honest `finish`, not a repeat
   - **Budget halt**: history shows remaining ₹ < next call cost → `finish(failed)`
   - **Glossary consistency**: history shows prior partial translation of a long
     doc; correct args reuse the same term mapping
3. **Re-categorize the 6 plan cases** out of `control` into `failure_refusal`
   where feasible=false; score plan args (`feasible` present, `target_language`
   consistent with task) and add 2–4 harder plan cases (multi-file inventory,
   budget cap < cost, ambiguous target).
4. **Code-mixed & raw-script cases** using `text/sample_text_10_hinglish_chat.txt`,
   `edge_text_code_mixed_hinglish.txt`, `sample_text_21_sat_olchiki.txt`,
   `text/sample_text_23_tanglish_chat.txt`, and Devanagari disambiguation where
   the *only* disambiguator is the user-named language.
5. **Verification-decision cases**: after a synthesis, choose transcribe
   (`task="transcribe"`) to verify, vs refusing to spend v. trusting output and
   finishing. This single decision is the rubric's "verification" 20% in one tool call.
6. **Determinism**: retire/rename v1 archive references in README; add
   `dataset_sha256` assertion to the 8→`v3` docs; require `--strict` build.

## Bottom line for "is this good enough?"

For GEPA router optimization v2 is usable *today* — after fixing the dead
context paths. For claiming the agent plans multistep work properly and for
Indian-language knowledge work, **no**: the suite cannot distinguish an agent
that plans from one that pattern-matches confusions, and it has no Indic
long-document or error-recovery pressure at all. Both are fixable inside the
current builder/quota machinery without changing the schema.

---

## Resolution (2026-11-19): v3 applied

All findings from this audit were fixed in the builder and regenerated:

1. **11 dead `context_files` repointed** to the reorganised `sample_data/` tree;
   builder gate 6 passes with zero issues; `validation_report.json` now reports
   `issues: []` and dataset sha `2c3abeef…` matches the rebuilt file.
2. **README/split drift fixed**: data README now documents 87 cases with 37/21/29
   splits, the corrected category quota table, and the v3 changelog.
3. **11 new cases added** (P-14..P-17, R-14/R-15, R-16..R-19 plan-phase
   infeasibility re-tagged out of `control` and renamed to the `R-` refusal
   prefix, M-07..M-09), covering: OCR→translate with exact text pinning,
   glossary consistency across a long Hindi document, per-step budget/verification
   decisions, two error-recovery decisions (deterministic ToolError, corrupted
   OCR EOF), prompt injection inside a transcript, Devanagari Hindi-vs-Marathi
   disambiguation, Manipuri-in-Bengali-script translation, and romanized Hinglish.
4. **Plan-phase cases now 8**, with four categorized `failure_refusal`
   (feasible=false paths) so infeasible plans are measured separately from
   routine task closure; `control` reduced to 7.
5. Verified by mechanical re-audit: 87/87 schema-complete, no leaks, no
   duplicates, `acceptable == [expected]` 87/87, `context_files` all exist,
   multilingual_script coverage 9 cases (was 6).

**Still open** (see `audit_agent.md` / `audit_mcp_tools.md`): the benchmark
measures *routing decisions*, not trajectories. Nothing here tests the agent's
verification gate, recovery loop re-planning, or Indic text provenance — those
belong to the agent track. Difficulty inflation also persists at 25 easy
(28.7%); recommend re-scoring that split of the mix in the next iteration.
