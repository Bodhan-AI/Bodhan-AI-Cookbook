# Architecture

```mermaid
flowchart LR
  U[Task + optional local media] --> C[CLI]
  C --> P[OpenAI-compatible planner]
  P --> E[Bounded executor]
  E --> O[indic-ocr]
  E --> A[indic-transcribe]
  E --> T[indic-translate]
  E --> S[indic-speak]
  O --> B[Bodhan API]
  A --> B
  T --> B
  S --> B
  E --> V[Verification + cache + retry]
  V --> R[result.md + report.json + trace.jsonl]
  M[MCP stdio server] --> O
  M --> A
  M --> T
  M --> S
```

The CLI imports the shared Bodhan functions directly. The independent MCP stdio server exposes those same functions to MCP clients. The planner sees only the four allowlisted tools; media contents remain in local files and are passed by path. Tool outputs are cached by tool name and canonical arguments to avoid repeating identical Bodhan requests in one run.
