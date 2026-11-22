# Agent flow: plan, act, verify, retry, stop

```mermaid
flowchart TD
    S([start]) --> I[Inventory inputs in code<br/>inspect_files: kind, duration, pages,<br/>script-based language, cost estimates,<br/>missing paths]
    I --> C{LLM configured?}
    C -- no --> F1[status failed:<br/>explain, no outputs]
    C -- yes --> P[Plan: LLM returns JSON<br/>goal, feasible, languages, deliverables,<br/>assumptions, steps, verification, cost]
    P --> B{Budget left?<br/>steps / rupees / time}
    B -- no --> X[status budget_exhausted:<br/>list completed steps,<br/>publish nothing]
    B -- yes --> L[LLM turn with tools]
    L -- no tool call --> N{nudged twice?}
    N -- no --> B
    N -- yes --> F2[status failed]
    L -- tool calls --> G[Guards in code:<br/>limit batch to remaining steps,<br/>resolve paths, cap max_cost_inr,<br/>force speech verification,<br/>check text provenance and duplicates]
    G -- rejected --> E[structured error to LLM] --> B
    G --> T[Call MCP tools<br/>parallel when independent,<br/>per-call timeout]
    T --> W[Wrap transcript/OCR/file text<br/>as untrusted, flag injections,<br/>sync spend from bodhan://usage]
    W --> B
    L -- finish --> V[Verification gate in code:<br/>deliverables exist, audio back-transcribed,<br/>translations covered, planned kinds present,<br/>answer has no unproven Indian-language text]
    V -- problems, retries left --> R[reject finish with problems<br/>LLM fixes or re-plans] --> B
    V -- ok, or retries used --> D[accept; downgrade completed to partial<br/>if problems remain]
    D --> O[copy deliverables to artifacts/<br/>write result.md, report.json, trace.jsonl]
    X --> O
    F1 --> O
    F2 --> O
    O --> Z([exit 0 with status in report.json])
```

## Retry layers

| Layer | What is retried | How |
|---|---|---|
| HTTP client | timeouts, connection errors, 429 (rate limit), 5xx | exponential backoff with jitter, honours `Retry-After`, up to `BODHAN_MAX_RETRIES` (3). 429 caused by exhausted credit is not retried. A 401 is retried once if that key already worked in this process (as the API docs advise). |
| OCR tool | 502 (a block exceeded `max_tokens`) | resend with `max_tokens` 4096, then 8192 (a different request, not a duplicate) |
| Translate tool | `truncated: true` | split the unit in half at sentence boundaries and translate the halves |
| Speech tool | a chunk whose back-transcription shows repetition, omission or low similarity | regenerate that chunk as smaller pieces (different inputs), keep the better version |
| Agent | tool errors, rejected finish | the LLM sees the structured error (`code`, `hint`) and decides: fix the call, re-plan, or finish honestly |
| LLM client | timeouts, 429, 5xx; unsupported `temperature`; no native tool calling | backoff; drop `temperature`; switch to a JSON-in-text tool protocol |

## Stop conditions

* `finish` accepted by the gate: status `completed`, `partial`, `impossible` or `failed`
* steps, rupees or wall-clock exhausted: `budget_exhausted` (checked before every LLM turn; paid tools also refuse before spending if their estimate exceeds the remaining rupees)
* LLM unavailable after retries, or it stops calling tools: `failed`
* any internal exception: `failed`, and the report is still written
