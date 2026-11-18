# Agent flow

![Agent flow: preflight, plan, feasibility, act loop, verify, repair, finish](images/agent_flow.svg)

([PNG version](images/agent_flow.png))

```
preflight (code) → plan (LLM, JSON) → feasibility check (code)
  → act loop: LLM picks a tool → guards (code) → MCP call → compact result
  → verify every deliverable (code) → one repair round if needed → finish
```

| Step | Who | What happens | Ends early when |
|---|---|---|---|
| Preflight | code | Expand `--files`, run `inspect_media` on each file (free), build a short manifest | Inputs are missing → honest failure |
| Plan | LLM | JSON plan: deliverables (kind, language, format, target duration), steps, assumptions | |
| Feasibility | code | Every deliverable language is supported by the model that produces it; cost estimate ≤ `--max-cost-inr` | Unsupported language or budget too small → honest failure |
| Act loop | LLM + code + MCP | The LLM picks a tool. Guards reject Indian-language text written by the LLM, force `out_dir` and `verify`, and check budgets. The call goes to `bodhan_mcp` over stdio, and the result comes back compacted, with untrusted text wrapped | Steps, ₹ or time run out → clean stop with partial results |
| Verify | code | Audio verified by back-transcription and duration; translation coverage and tables; valid SRT; every planned deliverable exists | |
| Repair | LLM | One round: the LLM is told exactly what failed | Still failing → partial, reported honestly |
| Finish | code | Writes `result.md`, `artifacts/`, `trace.jsonl`, `report.json` | |

```mermaid
flowchart TD
  A[CLI: task, files, budgets] --> B[Preflight - code: expand files, inspect_media, manifest]
  B --> C{Inputs present?}
  C -- no --> X[Honest failure: explain in result.md, no artifacts]
  C -- yes --> D[Plan - LLM: JSON deliverables, steps, assumptions]
  D --> E{Feasible? languages, cost within budget}
  E -- no --> X
  E -- yes --> F[LLM picks next tool]
  F --> G[Guards - code: no Indian-script LLM text, forced args, budget check]
  G -- out of budget --> S[Stop cleanly: partial results + report]
  G --> H[MCP call to bodhan_mcp over stdio]
  H --> I[Compact result: paths, previews, untrusted wrapper]
  I --> F
  F -- finish --> V{Verify deliverables - code}
  V -- pass --> W[Finish: result.md, artifacts, trace.jsonl, report.json]
  V -- fail --> R[Repair round, once]
  R --> F
  R -- still fails --> P[Partial: reported honestly]
```
