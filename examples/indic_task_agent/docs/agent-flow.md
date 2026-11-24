# Agent flow

```mermaid
flowchart TD
  S([task + files]) --> I["Intake: resolve files, one probe_media call, no spend"]
  I -->|no usable file| F1["Stop: failed, MISSING_FILE, zero model calls"]
  I --> P["Plan: one LLM call returns a JSON plan"]
  P -->|invalid JSON or schema| R["One repair call with the validation error"]
  R -->|still invalid| F2["Stop: failed, LLM_INVALID_PLAN, nothing spent"]
  R --> D
  P --> D{"feasible?"}
  D -->|no: unsupported language, impossible task| F3["Stop: failed, refusal sentence, zero model calls"]
  D -->|yes| C["Price the plan in Python (x1.15 margin)"]
  C -->|over budget| RP2["One re-plan with the ceiling quoted"]
  RP2 -->|still over| F4["Stop: failed, BUDGET_EXCEEDED, nothing purchased"]
  C -->|affordable| A
  RP2 --> A

  subgraph LOOP["Act: steps in dependency order, independent steps in parallel"]
    A["Take a step slot, reserve cost"] --> T["Call tool (cache first)"]
    T -->|retryable error| RT["Backoff retry, max 3 (timeouts are never re-sent)"]
    RT --> T
    T -->|ok| ST["Settle measured cost, trace the result"]
    T -->|fatal error or budget or step limit| PART["Stop cleanly, keep artifacts so far"]
    ST --> N{"more steps?"}
    N -->|yes| A
  end

  N -->|no| V["Verify every artifact: L1 free checks, L2 back-transcription if budget allows"]
  PART --> V
  V --> ST2["status computed from checks, never asserted by the LLM"]
  ST2 --> W["Write result.md, report.json, trace.jsonl"]
  F1 --> W
  F2 --> W
  F3 --> W
  F4 --> W
  W --> E([exit 0 for every honest outcome; 2 only for CLI misuse])
```

## What the LLM decides, and what Python decides

| Decision | Who | Fallback if it fails |
|---|---|---|
| Understand the task, choose tools, write the plan | LLM, one call + one repair | `LLM_INVALID_PLAN`, run ends, nothing spent |
| Estimate generated-text length (`expected_chars`) | LLM | Python caps it at 12,000 chars; missing value is a schema error |
| Write the English draft (`write_text`) | LLM | Output with any Indic character is rejected and fails the step |
| Price, reserve, settle, stop | Python | n/a |
| Chunking, retries, caching, verification, status | Python | n/a |
