# Routing Benchmark — DSPy Optimization Lab

106 next-action decision points for the Bodhan Tool Agent, used by
`experiments/08_dspy_prompt_optimization.ipynb`.

## What this is

Each row is **one routing decision**, not a full trajectory. The model sees a task, a phase
(`plan` or `act`), a compact history, and the live tool catalog. It must select exactly one
next tool and its arguments.

The benchmark covers the **real advertised catalog**:

- 11 MCP tools: `transcribe_audio`, `translate_text`, `synthesize_speech`,
  `extract_document_text`, `inspect_media`, `chunk_audio`, `read_text_file`,
  `write_text_file`, `detect_language`, `get_cache_stats`, `clear_cache`.
- 2 agent control tools: `submit_plan` (phase `plan` only) and `finish` (phase `act` only).

## Schema

| Field | Meaning |
|---|---|
| `id` | Stable case id (`A-001` … `G-008`). |
| `phase` | `plan` → only `submit_plan` is available; `act` → MCP tools + `finish`. |
| `task` | Natural user request. Must not name the correct tool. |
| `context_files` | Real files on disk the task refers to, relative to the recipe root. |
| `history_summary` | Prior tool calls/observations needed to make the decision; empty on first step. |
| `expected_next_tool` | The single best next tool. |
| `acceptable_next_tools` | Defensible alternatives, including `expected_next_tool`. Prefer one element. |
| `arg_constraints` | Only arguments the task makes unambiguous. Never invented values. |
| `category` | Coverage bucket (`single_tool_happy`, `near_neighbor_confusion`, `multilingual_script`, `failure_refusal`, `boundary_feasible`, `adversarial`, `budget`, `path_traversal`, `multi_step_pipeline`, `argument_edge`, `cache_admin`). |
| `difficulty` | `easy`, `medium`, `hard`. |
| `rationale` | Why this is the correct next action, citing the tool contract. |
| `split` | `train`, `dev`, or `test`. Stratified by expected tool. |
| `source_slice` | Which seed writer produced it. |
| `provenance` | Writer, audit, certification status. |
| `schema_version` | `1.0`. |

## Files

| File | Purpose |
|---|---|
| `routing_cases.jsonl` | Canonical 106-case dataset. |
| `routing_cases_train.jsonl` | 64 training cases for GEPA. |
| `routing_cases_dev.jsonl` | 21 validation cases for GEPA selection. |
| `routing_cases_test.jsonl` | 21 held-out cases. **Do not tune against this split.** |
| `validation_report.json` | Counts, test-tool coverage, issues (must be empty), SHA-256. |
| `fixtures/` | Synthetic-but-real DOCX/TXT fixtures for argument cases. Kept out of `sample_data/` so the authentic dataset catalog stays uncontaminated. |
| `_swarm/` | Raw writer slices. Kept for provenance and reproducibility. |

## How the set was built

1. **Six parallel writers** produced category slices: single-tool, near-neighbor confusions,
   multilingual/script, failure/refusal, multi-step pipelines, and argument edges.
2. A **cache-admin slice** was added after the live catalog audit revealed
   `get_cache_stats`/`clear_cache` are advertised to the model.
3. Every line was **machine-audited** for: schema completeness, valid phase/tool, expected
   tool in the acceptable set, no tool-name leakage in the task, arguments inside the tool
   schema, real context files, `finish.status` validity, `submit_plan.feasible` presence, and
   duplicate task text.
4. Free tool paths were **executed** against the fixtures: DOCX paging, HTML table extraction,
   24,500-character Hindi paging, and Unicode script detection.
5. The split is deterministic and stratified by expected tool, so every tool appears in the
   test split.

## Certification status

`provenance.certification = "machine_validated"` means all deterministic gates pass. It does
**not** mean "infallible human gold". The notebook's Section 3b adds an optional panel of three
independent LM solvers and flags disagreements for adjudication. For a publishable benchmark,
spot-check 10–20% of cases by hand, starting with:

- `B-005`/`B-006` (DOCX paging vs table extraction),
- `F-004`/`F-014` (path-traversal refusal vs an attempted write),
- `D-005` (plan must reject "write the Kannada yourself"),
- `C-009` (Bengali/Assamese script ambiguity),
- `B-014` (translate before synthesize).

## Reproduce

```bash
# From the recipe root, with the standard recipe venv:
python -m pytest tests -q
```

Then open `experiments/08_dspy_prompt_optimization.ipynb` and run Sections 0–3. They validate
the dataset and the live catalog without any paid model call. Enable Sections 5–8 only after
the endpoint probe succeeds.

## Known limitations

- The labels are LLM-written and machine-audited; they are not a substitute for a human
  adjudication round on all 106 cases.
- `history_summary` is a compact summary, not a full replay. End-to-end scoring still requires
  the production trace/replay harness.
- The fixtures under `fixtures/` are synthetic test data, not authentic Indic corpora. The
  real datasets live in `sample_data/` and are documented in `sample_data/DATASETS.md`.
- The test split is small (21 cases). Report confidence intervals or per-tool counts alongside
  accuracy; a single case is ~4.8 points.
