# Agent flow

```mermaid
flowchart TD
    A([Start: --task, --files, budgets]) --> B["Pre-flight (code, free)<br/>missing files · inspect_files · list_capabilities · cost estimate"]
    B --> C["PLAN (LLM, forced submit_plan)<br/>understanding · deliverables · assumptions · feasible? · steps · ₹ estimate"]
    C -->|feasible = false| X1["Finish: impossible<br/>clear English reason · no artifacts"]
    C -->|feasible| D{"Budget check (code)<br/>steps · ₹ · wall time · LLM calls"}
    D -->|exhausted| W["Wrap-up turn: only `finish` offered<br/>'report honestly what was done'"]
    D -->|ok| E["ACT (LLM picks tools; independent calls in parallel)"]
    E --> G{"Guards (code)"}
    G -->|"LLM-written Indic text<br/>unknown tool · bad JSON"| R["Reject → error back to LLM<br/>(counts as a step)"] --> D
    G -->|"duplicate call"| DUP["Return earlier result, ₹0"] --> D
    G -->|ok| H["Inject output_dir, max_cost_inr = remaining ÷ parallel calls,<br/>verify=true → MCP tools/call (timeout)"]
    H --> I{"Tool result"}
    I -->|"error: budget_exceeded / rate_limited / timeout / ..."| J["LLM decides: retry differently · re-plan · report"] --> D
    I -->|ok| K["Check result fields (LLM + code)<br/>complete · coverage_ok · structure_preserved ·<br/>target_script_share · verification.all_passed"]
    K -->|"failed check"| J
    K -->|ok, more to do| D
    K -->|done| F["LLM calls finish(status, answer_en, artifacts, assumptions)"]
    W --> F
    F --> V{"Finish validation (code)"}
    V -->|"Indic text in answer · artifact not produced by a tool"| RF["Reject once → LLM fixes<br/>(second time: sanitise / drop)"] --> F
    V -->|ok| Q["Verification gate (code)<br/>artifact verified only if its tool's checks passed;<br/>success → partial if not;<br/>impossible/failed → no artifacts"]
    Q --> Z(["Write result.md · report.json · artifacts/ · trace.jsonl"])
    X1 --> Z
```

Retries happen at two levels:

1. **Transport** (inside the server): 429, 5xx, timeouts and dropped connections are retried with exponential backoff and jitter, honouring `Retry-After`. Other 4xx errors fail fast with a structured error.
2. **Content** (inside the tools): a truncated translation is re-sent as two smaller pieces; a TTS clip that fails back-transcription is re-synthesised as two smaller pieces; an OCR page that returns 502 is retried with a larger `max_tokens`. Each retry sends a *different* input, so the cache never hands back the same bad answer and no identical request is paid for twice.

Anything still failing goes back to the LLM as a structured error with a `hint`. The LLM can then take another route, re-plan, or finish with `partial`/`failed` and an honest explanation.
