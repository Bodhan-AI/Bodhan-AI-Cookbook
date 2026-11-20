# DSPy Prompt & Tool-Description Optimization Lab

**Status:** implemented and executed (no-spend) · **Notebook:** `experiments/08_dspy_prompt_optimization.ipynb`
· **Model used for the smoke test:** `gpt-6-luna` · **DSPy:** 3.5.0 · **Benchmark:** 87 cases (v3)

This document records the design, the data, the execution results, and the exact steps to run the
lab. It is the long-form companion to the notebook and to `experiments/data/README.md`.

> **Revision note (2026-11-18).** The catalog changed after the first version of this lab.
> `chunk_audio` was deleted from the MCP server, and `get_cache_stats` / `clear_cache` were
> removed from the model-facing catalog (they stay callable by operators over MCP). The original
> 106-case benchmark is archived in `experiments/data/_archive/v1_106/`; its design flaw is
> documented there. This document describes the current 87-case v3 set and the 8-tool catalog.
>
> **Revision note (2026-11-19).** The benchmark was rebuilt as **v3**: 87 cases, splits 37
> train / 21 dev / 29 test; category quotas `near_neighbor_confusion` 21 (9/3/9),
> `failure_refusal` 19 (9/4/6), `argument_edge` 14 (6/4/4), `multi_step_pipeline` 17 (7/4/6),
> `multilingual_script` 9 (3/3/3), `control` 7 (3/3/1); difficulty hard/medium/easy = 20/42/25;
> test split covers all 10 tools and all 6 categories;
> `dataset_sha256 = 2c3abeefd189128415a7efd7ff67826d8099e2c98630c39edd83b67ae92a8eb4`;
> provenance audit string `v3_multistep`. The notebook's embedded run outputs date from the
> 2026-11-18 execution (against the pre-v2 70-case dataset, `Cases: 70 | 31 15 24`, sha
> `dd3ecb58…`) and were not re-executed for v3.

---

## 1. The question this lab answers

> Can DSPy optimize the `bodhan_tool_agent` prompts and tool descriptions even though the agent
> uses OpenAI-compatible APIs?

**Yes for prompt optimization. No for automatic docstring rewriting.**

DSPy is provider-agnostic. Any endpoint that speaks OpenAI-style chat completions works:

```python
import dspy

lm = dspy.LM(
    "openai/gpt-6-luna",          # "openai/" selects the protocol, not the vendor
    api_base="https://api.openai.com/v1",
    api_key="...",
    model_type="chat",
    timeout=60,
    cache=True,
)
dspy.configure(lm=lm)
```

The `openai/` prefix is mandatory for an OpenAI-compatible gateway. `api_base` must include `/v1`.
Tool calling is **not** required for the optimization programs used here.

What DSPy actually optimizes:

| Knob | What it means |
|---|---|
| **Instructions** | The natural-language docstring/instruction attached to a `dspy.Signature`. |
| **Demos** | Few-shot examples bootstrapped from successful traces. |
| **Weights** | Model parameters via `BootstrapFinetune`; not used here. |

Tool descriptions are **inputs**, not parameters. Optimizers do not rewrite them. That is why the
lab uses a separate description tournament.

---

## 2. The catalog this lab optimizes against

Exposure is now explicit. `agent.MODEL_FACING_TOOLS` is an allowlist, and
`_get_openai_tool_definitions()` filters the FastMCP registry through it.

| Class | Tools | Count |
|---|---|---:|
| **Model-facing** | `inspect_media`, `transcribe_audio`, `translate_text`, `synthesize_speech`, `extract_document_text`, `read_text_file`, `write_text_file`, `detect_language` | 8 |
| **Operator-only** (MCP, never advertised) | `get_cache_stats`, `clear_cache` | 2 |
| **Control** | `submit_plan` (plan phase), `finish` (act phase) | 2 |

`chunk_audio` was deleted entirely. `transcribe_audio` already chunks internally at
`server.py:279`, so the standalone slicer was a redundant entry point that invited a
chunk-then-transcribe-each workflow: five paid calls for one job.

`bypass_cache` remains in the Python signatures (the cache decorator binds it) but is stripped
from the schema sent to the LLM. A planner setting it only doubles spend.

Tests in `tests/test_catalog_guard.py` pin all of this: the advertised set must equal the
allowlist, the operator tools must stay registered, `chunk_audio` must stay gone, and
`bypass_cache` must never appear in an advertised schema.

---

## 3. What is optimizable in this repository

| Artifact | Location | Optimizable? | Approach |
|---|---|---|---|
| Agent routing policy (`SYSTEM_PROMPT`) | `src/bodhan_agent/agent.py` | ✅ | GEPA / MIPROv2 over a replica next-action router |
| Next-action reasoning | ReAct loop | ✅ | Bootstrapped demos and optimized instructions |
| `submit_plan` / `finish` descriptions | `agent.py` | ⚠️ partially | Move policy into signature instructions, or A/B manually |
| 8 MCP tool docstrings | `src/bodhan_mcp/server.py` | ❌ not automatically | Description tournament scored on the benchmark |
| `translate_text` instruction variants | `server.py` | ✅ strongly | Not wired up; needs human-verified references |
| `_ocr_images` instruction | `server.py` | ✅ strongly | Not wired up; needs reference pages |
| Budget ledger, quarantine, verification, read sandbox | `agent.py`, `common/paths.py` | ❌ | Keep as code; never let an optimizer rewrite them |

### The key insight

The highest-value DSPy targets are **the prompts sent to the weak specialized Indic models**, not
the agent's system prompt. `indic-translate` is highly prompt-sensitive; the server already
hand-rolls two instruction variants because the live model echoed input when the source language
was named. That is exactly the kind of prompt search DSPy can formalize.

The agent's system prompt is already strong. Optimizing it is useful, but it is a smaller win.

---

## 4. Architecture: an offline lab, not a runtime dependency

The lab never edits `src/`. It:

1. Loads a **snapshot** of the model-facing catalog.
2. Loads a **routing benchmark** of 87 decision points.
3. Compiles a **single-step next-action router** with DSPy.
4. Scores tool descriptions as a search space.
5. Exports candidate text for human review.

Production keeps its budget circuit breaker, untrusted-data quarantine, read sandbox,
post-execution verification, tracing, and run archiving.

```
experiments/
├── 08_dspy_prompt_optimization.ipynb     # the lab (22 cells, 10 code)
├── requirements-optim.txt                # isolated venv
├── scripts/
│   ├── refresh_catalog_snapshot.py       # regenerate the snapshot (outside Jupyter)
│   └── build_routing_dataset.py          # rebuild the 87 cases + all gates
├── data/
│   ├── routing_cases.jsonl               # 87 canonical cases
│   ├── routing_cases_{train,dev,test}.jsonl
│   ├── validation_report.json            # counts, coverage, SHA-256
│   ├── tool_catalog_snapshot.json        # 8 MCP + 2 control tools
│   ├── fixtures/                         # DOCX/TXT fixtures
│   └── _archive/v1_106/                  # retired 106-case benchmark
└── outputs/exp08_dspy_optimization/
    └── export/
        ├── optimized_router_instructions.md
        ├── optimized_tool_descriptions.json
        ├── optimization_report.md
        └── apply_diff.md
```

---

## 5. The routing benchmark

### 5.1 Why decision points, not whole trajectories

Each row is **one next-action decision**. A task like "PDF → OCR → translate → TTS" becomes four
decision points, each with a `history_summary`. This makes labels unambiguous, lets one
trajectory produce several training examples, separates planner behaviour from executor
behaviour, and keeps evaluation cheap.

### 5.2 Size and split

| Split | Cases | Purpose |
|---|---:|---|
| Train | 37 | GEPA reflective updates |
| Dev | 21 | GEPA Pareto selection |
| Test | 29 | Held out; do not tune against it |
| **Total** | **87** | |

Frozen dataset SHA-256 (also recorded in `experiments/data/validation_report.json`; it changes
whenever the dataset is rebuilt — see `experiments/data/README.md`):

```
2c3abeefd189128415a7efd7ff67826d8099e2c98630c39edd83b67ae92a8eb4
```

The split is a **designed quota matrix**, not a random draw. Every tool and every category
appears in test:

| Category | Total | train | dev | test |
|---|---:|---:|---:|---:|
| `near_neighbor_confusion` | 21 | 9 | 3 | 9 |
| `failure_refusal` | 19 | 9 | 4 | 6 |
| `argument_edge` | 14 | 6 | 4 | 4 |
| `multi_step_pipeline` | 17 | 7 | 4 | 6 |
| `multilingual_script` | 9 | 3 | 3 | 3 |
| `control` | 7 | 3 | 3 | 1 |

### 5.3 Why v1 was replaced

The 106-case set was stratified by **tool**, which produced a held-out split with 12 of 14
`argument_edge` cases and **zero** `near_neighbor_confusion`, `multilingual_script`, or
`single_tool_happy` cases. The reported score was effectively an argument-edge score, while the
description tournament targets confusion pairs that were never held out. The train split had 20
happy-path cases and one pipeline case. See `data/_archive/v1_106/ARCHIVE_NOTE.md`.

### 5.4 The four confusion pairs

Deleting `chunk_audio` retired transcribe/chunk; hiding the cache tools retired
cache-stats/cache-clear. Four remain:

- `extract_document_text` vs `read_text_file` (OCR/tables vs free bounded paging)
- `extract_document_text` vs `inspect_media` (paid content vs free metadata)
- `translate_text` vs `synthesize_speech` (text out vs audio out)
- `write_text_file` vs `synthesize_speech` (text artifact vs audio artifact at `output_path`)

### 5.5 Design rule

Every case must name the failure mode it catches. There are no filler happy paths: the happy
path is represented implicitly by the confusion pairs, where the correct tool is the non-obvious
member of the pair. `acceptable_next_tools` is always exactly one element — hedging hides
routing errors.

### 5.6 Certification honesty

`provenance.certification = "agent_reviewed"`, `provenance.audit = "v3_multistep"`. Every case was authored and checked against the
tool contracts and the deterministic gates in `build_routing_dataset.py`. This is not a claim of
independent human review. Six calls are matters of taste rather than fact and are listed in
`outputs/exp08_dspy_optimization/review_taste_calls.md`.

---

## 6. Notebook walkthrough

`experiments/08_dspy_prompt_optimization.ipynb` contains 22 cells (10 code), executed
end-to-end with **0 errors** on 2026-11-18 — against the pre-v2 70-case dataset (sha
`dd3ecb58…`); the execution predates even the v2 rebuild, and the notebook has not been
re-executed since. Its embedded outputs are historical.

| Section | What it does |
|---|---|
| 0 | Path bootstrap, `.env` loading, spend toggles, output dirs |
| 1 | DSPy LM construction + optional hard-timeout probe |
| 2 | Load the catalog snapshot, assert it equals `MODEL_FACING_TOOLS`, assert operator tools are absent |
| 3 | Load and validate the 87-case dataset, coverage matrix, frozen hash |
| 4 | `RouteTool` signature, `Router` module, GEPA-shaped metric |
| 5 | Production baseline + DSPy zero-shot + DSPy CoT on the test split |
| 6 | `dspy.GEPA(auto="light")` compile |
| 7 | Held-out evaluation and confusion matrix |
| 8 | Tool-description tournament (current / contract-first / contrastive) |
| 9 | Export candidate instructions, descriptions, report, diff |
| 10 | Apply checklist and caveats |

Section 2 is a **contract test**. It parses `MODEL_FACING_TOOLS` out of `agent.py` with `ast` and
fails if the snapshot disagrees, so a half-applied catalog change cannot silently pass.

### 6.1 The router signature

```python
class RouteTool(dspy.Signature):
    """You are the next-action router for an Indic media and document agent.
    Select exactly one tool from the catalog ..."""
    task: str = dspy.InputField()
    phase: str = dspy.InputField()
    history: str = dspy.InputField()
    tool_catalog: str = dspy.InputField()
    tool_name: str = dspy.OutputField()
    arguments_json: str = dspy.OutputField()
```

`dspy.ChainOfThought(RouteTool)` adds the reasoning field. The output is a JSON string rather
than a `dict` for maximum adapter compatibility.

### 6.2 The metric

The metric is designed for GEPA's feedback mode:

```python
return dspy.Prediction(score=score, feedback=feedback)
```

| Component | Weight | Rule |
|---|---:|---|
| Tool choice | 0.70 | Predicted tool in `acceptable_next_tools` |
| Arguments | 0.20 | Proportional match against `arg_constraints`; full credit if none |
| Concise reasoning | 0.10 | Non-empty and ≤ 600 characters |
| Phase violation | hard 0 | `submit_plan` in `act`, or anything else in `plan` |
| Invalid `finish.status` | −0.20 | And flagged unsafe for `impossible` cases |

The metric also records `unsafe`: choosing a paid tool when the task is impossible, or choosing
`submit_plan` during execution.

### 6.3 Reading the result at N=29

One test case is ~3.4 points. A single regression looks like a large accuracy drop; a single fix
looks like a large gain. Report **which case ids failed and in which direction**, per category.
Do not publish a single accuracy figure.

### 6.4 Why not `dspy.ReAct`

`dspy.ReAct` would replace the production loop and lose the forced Turn-0 `submit_plan`, the
pre-flight rupee circuit breaker, the `<untrusted_input_data>` quarantine, the read sandbox,
post-loop verification, and trace/report/run archiving. The lab therefore optimizes a **single
decision step** and exports text.

Note that production now sends only `submit_plan` at Turn 0, because `tool_choice` pins it and
the other schemas were pure prompt bloat. That matches the benchmark's plan catalog exactly.

---

## 7. Running the lab

### 7.1 Environment

Do **not** install DSPy into the recipe's pinned runtime venv: `requirements.txt` pins
`openai==1.65.1`, while DSPy/LiteLLM may want a newer OpenAI SDK.

```powershell
cd examples\bodhan_tool_agent
python -m venv .venv-optim
.venv-optim\Scripts\python -m pip install -r experiments\requirements-optim.txt
.venv-optim\Scripts\python -m ipykernel install --user --name bodhan-dspy --display-name "Python 3 (bodhan-dspy)"
```

`requirements-optim.txt` pins the compatibility-critical packages: `dspy[litellm]>=3.5`,
`mcp==1.3.0` (2.x renamed FastMCP and breaks the recipe), `pydantic==2.10.6`, plus
`pymupdf`, `numpy`, `python-dotenv`, `httpx`, `pyyaml`, `ipykernel`.

### 7.2 Spend toggles

| Toggle | Default | Effect |
|---|---|---|
| `RUN_LM_PROBE` | `False` | One OpenAI-compatible call with a 20s hard timeout |
| `RUN_BASELINES` | `False` | Production + zero-shot + CoT on the test split |
| `RUN_OPTIMIZATION` | `False` | `dspy.GEPA(auto="light")`, capped by `MAX_METRIC_CALLS=120` |
| `RUN_TOOL_DESC_TOURNAMENT` | `False` | Compare current / contract-first / contrastive catalogs |
| `RUN_FULL_TOOL_GREEDY` | `False` | Per-tool coordinate descent (more calls) |

Recommended order: `RUN_BASELINES` → `RUN_TOOL_DESC_TOURNAMENT` → `RUN_OPTIMIZATION` only if the
tournament shows the confusion pairs still failing.

### 7.3 Cost expectations

- The **compile** step dominates cost. GEPA can make hundreds of metric calls.
- `dspy.LM(..., cache=True)` is on; repeated identical calls hit the DSPy cache.
- `auto="light"` is the small budget. `MAX_METRIC_CALLS` is a practical cap.
- Baselines on 87 cases = 261 model calls (production, zero-shot, CoT).
- The dataset's free tool paths (inspect/read/detect) cost nothing; paid Bodhan paths are not
  executed by the notebook.

### 7.4 The Jupyter/Windows deadlock

**Symptom:** the kernel becomes unresponsive after importing the FastMCP registry or touching
`Tool.parameters`; even `print()` stops responding.

**Cause:** accessing `mcp._tool_manager.list_tools()[i].parameters` inside the Jupyter kernel
deadlocks the kernel's event loop. It works fine in a normal process.

**Fix used by the notebook:**

1. Generate `experiments/data/tool_catalog_snapshot.json` **outside Jupyter** with
   `experiments/scripts/refresh_catalog_snapshot.py`.
2. Load the JSON in the kernel; never touch `Tool.parameters`.
3. Extract `SYSTEM_PROMPT` and `MODEL_FACING_TOOLS` from `agent.py` with `ast.literal_eval`,
   not by importing `agent.py`.
4. Read `.env` directly; do not import `src.common.config`.

This removes every `src/` import from the kernel and makes the notebook stable.

---

## 8. Results so far

### 8.1 Notebook execution

- 22 cells, 10 code cells, all executed, **0 errors**.
- Spending toggles off; no Bodhan rupees spent.
- Section 2 prints `Catalog contract: OK -- 8 model-facing tools`.
- Section 3 prints the case counts and a matching frozen hash. For v3 that line reads
  `Cases: 87 | train/dev/test: 37 21 29` (the notebook's embedded output actually shows
  `Cases: 70 | train/dev/test: 31 15 24` with hash
  `dd3ecb58359d411f354fae722518a12352d1239ae007be1d9b399aaf0996442d` — the notebook predates
  even the v2 rebuild and has not been re-executed since).

### 8.2 Router smoke test

The first smoke test (2 live calls against `gpt-6-luna`) is archived with the v1 dataset, because
its case ids no longer exist. Its finding survives as case `R-16` (the voice-cloning plan-phase
infeasibility case; `K-01` in the v2-era numbering, re-tagged to `failure_refusal` in v3):

| Case | Gold | Behaviour |
|---|---|---|
| `R-16` (voice cloning from a reference clip) | `submit_plan`, `feasible=false` | The first router chose `submit_plan` but marked it feasible |

No tool accepts reference audio or a speaker embedding: `synthesize_speech` takes text,
language, voice name, style and output path only. The metric gives that answer 0.8/1.0
(0.7 tool + 0.0 arguments + 0.1 reasoning), which is exactly the signal GEPA needs.

### 8.3 What the pilot did not do

- Did not run GEPA.
- Did not run the baseline comparison.
- Did not run the description tournament.
- Did not spend Bodhan rupees on `indic-translate`/`indic-ocr`.

Those are deliberate, toggle-gated next steps.

---

## 9. Optimizer guidance

| Situation | Use |
|---|---|
| Cheap first baseline | `dspy.BootstrapFewShot` |
| Instructions look wrong, demos fine | `dspy.COPRO` or `dspy.GEPA` |
| Instructions + demos both weak | `dspy.MIPROv2(auto="light")` |
| Failure cases share a nameable pattern | `dspy.SIMBA` or `dspy.InferRules` |
| Agent / tool-use task with feedback-rich metric | `dspy.GEPA` |
| Multiple competent programs | `dspy.Ensemble` |

Recommendation for this repo:

1. **BootstrapFewShot** on the 37-case train split — cheapest useful baseline.
2. **Description tournament** — at 87 cases this is the highest-yield step, because 4 pairs × 2
   variants is a search you can read.
3. **GEPA(auto="light")** with the feedback metric — a hypothesis generator at this size, not an
   optimizer. Treat its output as one candidate, not the answer.
4. Do not fine-tune. Prompt-only optimization is the right first lever.

### 9.1 What "best optimized prompt" means

There is no global best. GEPA's output is tied to the student model, the train/dev split, and the
metric. If `LLM_MODEL` changes, rerun the lab. If the metric rewards speed, it learns terse
prompts; if it rewards safety, it learns cautious ones. The metric is the product decision.

### 9.2 Safety warning

**Never put safety rules inside the tunable instruction.** The English-only reasoning rule, the
untrusted-data quarantine, the honest-failure rule, and the read sandbox must stay outside the
optimized block, or the metric must penalize violations explicitly. GEPA will delete a rule it
is not rewarded for keeping.

---

## 10. Tool-description tournament

DSPy does not rewrite docstrings. The lab treats descriptions as a search space:

| Variant | Description |
|---|---|
| `current` | Shipped docstring. |
| `contract_first` | One-line purpose + `Use when` + `Do NOT use when` + cost. |
| `contrastive` | Contract-first + nearest confusable tool + boundary. |

The global comparison is three catalogs × the dev set. Per-tool greedy coordinate descent is a
second, more expensive pass.

Why this often beats prompt tuning: most routing errors are **near-neighbor confusions**, not
policy failures. The benchmark covers all four surviving pairs explicitly.

Actionable output is the per-pair result, not a single accuracy number.

---

## 11. Applying the results

1. Review `outputs/exp08_dspy_optimization/export/optimized_router_instructions.md`.
2. Keep the safety block outside the candidate text.
3. Patch `SYSTEM_PROMPT` manually; never auto-apply.
4. Review `optimized_tool_descriptions.json`; patch `server.py` manually.
5. Re-run `pytest` and the notebook's held-out evaluation.
6. Re-run the lab whenever `LLM_MODEL`, `server.py`, or `MODEL_FACING_TOOLS` changes.

The exported files are candidates, not patches. The notebook never writes to `src/`.

---

## 12. File inventory

| Path | What it is |
|---|---|
| `experiments/08_dspy_prompt_optimization.ipynb` | Executed lab, 22 cells, 0 errors |
| `experiments/requirements-optim.txt` | Isolated DSPy environment |
| `experiments/scripts/refresh_catalog_snapshot.py` | Regenerates the model-facing snapshot |
| `experiments/scripts/build_routing_dataset.py` | Rebuilds the 87 cases and re-runs every gate |
| `experiments/data/routing_cases.jsonl` | 87 canonical cases |
| `experiments/data/routing_cases_{train,dev,test}.jsonl` | 37 / 21 / 29 |
| `experiments/data/validation_report.json` | Counts, coverage, SHA-256, zero issues |
| `experiments/data/tool_catalog_snapshot.json` | 8 MCP + 2 control tools |
| `experiments/data/fixtures/` | DOCX/TXT fixtures (including one path with spaces) |
| `experiments/data/_archive/v1_106/` | Retired 106-case benchmark, freeze-dried |
| `experiments/data/README.md` | Dataset contract and limitations |
| `experiments/outputs/exp08_dspy_optimization/export/` | Generated candidates |

---

## 13. Next steps

1. **Run the baseline.** Flip `RUN_BASELINES=True`. Compare production vs zero-shot vs CoT on a
   12-case stratified pilot of the held-out test split (`PILOT_N=12`, 2 cases per category).
2. **Run the description tournament.** Flip `RUN_TOOL_DESC_TOURNAMENT=True`. Check the four pairs.
3. **Resolve the taste calls** in `review_taste_calls.md` — six labels are judgement, not fact.
4. **Run GEPA light** only if the tournament leaves confusion pairs failing.
5. **Optional Bodhan prompt lab.** Add 20–30 human-verified translation references before
   optimizing `indic-translate`; it is not wired into the notebook on purpose.

---

## 14. References

- DSPy optimizers: <https://dspy.ai/current/diving-deeper/choosing-an-optimizer/>
- GEPA walkthrough: <https://dspy.ai/current/getting-started/gepa-optimization/>
- Advanced tool use / SIMBA: <https://dspy.ai/current/tutorials/tool_use/>
- MCP tools in DSPy: <https://dspy.ai/current/tutorials/mcp/>
- `dspy.Tool` API: <https://dspy.ai/current/api/primitives/Tool/>
- `dspy.ReAct` API: <https://dspy.ai/current/api/modules/ReAct/>
- `dspy.LM` API: <https://dspy.ai/current/api/models/LM/>
