# Agent flow

```mermaid
flowchart TD
    START([python -m bodhan_agent --task --files --out]) --> CHECK{Input files exist?}
    CHECK -- some missing --> NOTE[Record missing files<br/>as facts for the planner]
    CHECK -- yes --> INV
    NOTE --> INV[Inventory in code:<br/>inspect_file on every input<br/>kind, duration, pages, script, cost estimates]
    INV --> PLAN[LLM: submit_plan<br/>goal, interpretation, assumptions,<br/>target languages, steps, feasibility]
    PLAN --> FEAS{Feasible?}
    FEAS -- "no: unsupported language,<br/>missing input, beyond the tools" --> IMP[finish status=impossible<br/>explanation, no artifacts]
    FEAS -- yes --> ACT

    subgraph LOOP[Act loop]
        ACT[LLM picks tool calls] --> GUARDS{Guards in code}
        GUARDS -- "Indic text passed inline,<br/>path outside inputs/work,<br/>step budget hit" --> REJECT[Return structured error<br/>to the LLM]
        REJECT --> ACT
        GUARDS -- ok --> BUDGET{Estimated cost fits<br/>remaining budget?}
        BUDGET -- no --> STOPB[Stop: budget]
        BUDGET -- yes --> CALL[Run tools<br/>parallel if independent<br/>per-call timeout]
        CALL --> RES{Tool result}
        RES -- "retryable error<br/>(429, 5xx, timeout)" --> RETRY[Client already retried with backoff;<br/>LLM may retry once or re-plan]
        RETRY --> ACT
        RES -- "permanent error<br/>(unsupported_language, file_not_found)" --> REPLAN[LLM re-plans or<br/>declares impossible]
        REPLAN --> ACT
        RES -- ok --> TRACE[Append trace line:<br/>args, result summary,<br/>latency, cost]
        TRACE --> MORE{LLM calls finish?}
        MORE -- no --> ACT
    end

    MORE -- yes --> GATE{Verification gate in code:<br/>every audio and translation<br/>artifact verified?}
    GATE -- "unverified" --> VERIFY[Run verify_speech /<br/>verify_translation]
    VERIFY --> GATE
    GATE -- "failed check" --> REPAIR{Repair attempt left<br/>and budget left?}
    REPAIR -- yes --> ACT
    REPAIR -- no --> PARTIAL[finish status=partial<br/>failures listed]
    GATE -- passed --> DONE[finish status=completed]

    STOPB --> PARTIAL
    DONE & PARTIAL & IMP --> WRITE[Write result.md, report.json,<br/>copy artifacts, close trace]
    WRITE --> END([exit])
```

## Stop conditions

| Condition | Where it is enforced | Outcome |
| --- | --- | --- |
| LLM calls `finish` and the verification gate passes | agent, in code | `completed` |
| Task cannot be done: unsupported language (for example French), missing input, a capability the tools do not have | LLM judgement, backed by tool errors such as `unsupported_language` and `file_not_found` | `impossible`, with an explanation and no artifacts |
| `--max-steps` reached | agent, in code | `partial` (or `failed` if nothing was produced), with what was done so far |
| Next paid call would exceed `--max-cost-inr` | Bodhan client pre-check (`budget_exceeded`) and agent pre-check | `partial`, spend never exceeds the cap |
| Verification still fails after one repair | verification gate | `partial`, failed chunks and issues listed in `report.json` |
| LLM endpoint unreachable or returns garbage repeatedly | agent | `failed`, with the error, and any finished artifacts kept |

## What the trace records

One JSON line per step in `out/trace.jsonl`:

`{step, ts, type: plan|llm|tool|verify|finish|error, tool, arguments, result_summary, latency_s, cost_inr, cumulative_cost_inr, cache_hits}`

Arguments are summarised (long strings truncated, paths kept), so the trace shows why each step was taken without copying whole documents into it.
