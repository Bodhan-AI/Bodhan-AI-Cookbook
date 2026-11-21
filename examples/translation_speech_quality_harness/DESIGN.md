# Design

## Architecture

```mermaid
flowchart LR
    A[JSONL dataset] --> B[Dataset and quarantine]
    B --> C[Free text checks]
    C --> D[Robust cache and budget]
    D --> E[Indic-Translate round trip]
    E --> F[Candidate judge]
    D --> G[Indic-Speak and Indic-Transcribe]
    F --> H[Combine and reconcile]
    G --> H
    H --> I[Calibration and reports]
```

## Check order

1. Normalize and deterministic number, format, language, and length checks.
2. Sentence-level Indic-Translate round trips only for candidates.
3. OpenAI-compatible LLM confirmation only for candidates or ambiguity.
4. Supplied audio back-transcription and deliberate generated-audio samples.

The scheduler is deterministic. Its decisions are logged per item. The LLM reads Indic text but never writes or rewrites it.
