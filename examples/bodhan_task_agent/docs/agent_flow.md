# Agent flow: plan, act, verify, retry, stop

```mermaid
flowchart TD
  S([start: task + files]) --> P0[Preflight - code<br/>expand dirs · inspect_media free<br/>missing files · languages named in task · injection scan]
  P0 --> PLAN[LLM: submit_plan<br/>understanding · feasible? · assumptions · steps]
  PLAN --> F{feasible?}
  F -- no --> INF[status = infeasible<br/>English explanation · no model calls · no artifacts]
  F -- yes --> B{budgets left?<br/>steps · ₹ · time}
  B -- no --> STOP[stop cleanly<br/>status = budget_exhausted / incomplete<br/>verify what exists · report it]
  B -- yes --> ACT[LLM: next tool calls<br/>+ one-sentence reason]
  ACT --> G{guards pass?<br/>no Indian text from LLM<br/>paths allowed · tool exists}
  G -- no --> FB[error fed back to LLM] --> B
  G -- yes --> EX[execute - independent calls in parallel<br/>₹ budget split · per-call timeout · memoised duplicates]
  EX --> R{tool result}
  R -- error: retryable --> RT[client already retried with backoff<br/>LLM may re-plan: smaller input, other voice, no lang hint] --> B
  R -- error: budget / unsupported / missing --> FB
  R -- ok, verification failed --> TV[tool-level recovery already tried<br/>split chunk · re-synthesise piece · rebuild table cell-wise<br/>LLM sees failed checks] --> B
  R -- ok --> FIN{LLM calls finish?}
  FIN -- no --> B
  FIN -- yes --> V[Verify - code<br/>every audio: back-transcription CER<br/>every translation: script · length · numbers · tables<br/>every deliverable: exists · decodes · SRT parses · duration target]
  V --> VP{problems?}
  VP -- yes, first time --> REJ[finish rejected with reasons<br/>LLM gets one chance to fix] --> B
  VP -- yes, again --> DOWN[accept, status capped at partial]
  VP -- no --> DONE[status from facts:<br/>completed]
  DOWN --> OUT
  DONE --> OUT[/write result.md · artifacts/ · report.json · trace summary/]
  INF --> OUT
  STOP --> OUT
```

Every box that says **code** is deterministic and independent of the LLM. Each step is written to `trace.jsonl` as it happens, so a crash still leaves a complete history.
