# Flow: dataset to report

Order is by cost. Free deterministic checks run for every item; the paid speech check runs only
for triaged items; the budget can stop paid calls at any point without failing the run.

```mermaid
flowchart TD
    A[Read JSONL] --> B{valid line?}
    B -- no --> B1[log + skip + count] --> A
    B -- yes --> C[normalize source & translation]
    C --> D{translation present?}
    D -- no & --generate --> D1[Indic-Translate generate] --> E
    D -- no & not --generate --> D2[status=skipped: no_translation]
    D -- yes --> E[Round-trip back-translation (cheap)]
    E --> F[Run T1..T7 deterministic checks]
    F --> G{judge enabled?}
    G -- yes --> H[LLM judge: blind rubric pass<br/>validate verbatim quotes]
    G -- no --> I
    H --> I[Collect evidence per item]

    I --> J{speech enabled?}
    J -- no --> N
    J -- yes --> K[Triage: suspicious ∪ stratified sample<br/>bounded by --speech-sample-rate & budget]
    K --> L{budget left & selected?}
    L -- yes --> M[Signal checks free → ASR paid<br/>CER/WER, repetition, omission]
    L -- no --> M2[skip: record skipped_for_budget]
    M --> N[Combiner]
    M2 --> N
    N --> O{evidence type?}
    O -- strong deterministic --> P[FLAG]
    O -- 2+ independent noisy, or judge+verbatim --> P
    O -- lone noisy --> Q[UNCERTAIN → review_queue.csv]
    O -- none --> R[CLEAN]
    P --> S[Calibration vs labels<br/>fit thresholds (no model calls)]
    Q --> S
    R --> S
    S --> T[Write summary.md/json, items.jsonl,<br/>calibration.json, meta.json]
    T --> U{--baseline?}
    U -- yes --> V[diff.md + --fail-on-regression exit code]
    U -- no --> W[done]
```

## When costly checks trigger
- **Translation generation** (`--generate`): only for items missing a translation.
- **Back-translation**: cheap, run for every scored item (one Indic-Translate call), cached.
- **LLM judge**: every scored item unless `--no-judge`; cost is on the evaluator's LLM, not Bodhan credit. `--judge-repeats N` re-judges a sample to measure consistency.
- **Speech synthesis / ASR** (the expensive step): only for items the triage selects — anything already suspicious, plus a deterministic stratified sample of the rest — and only while budget remains. Everything is cached, so reruns are free.
