# Agent Execution Flow

The Bodhan Autonomous Task Agent follows a resilient 6-stage lifecycle:

```mermaid
flowchart TD
    Start([Task Request]) --> Inspect[Stage 1: File Inspection & Security Scan]
    Inspect --> Plan[Stage 2: Structured DAG Planning]
    Plan --> PreBudget{Stage 3: Pre-Execution Budget & Cache Check}
    PreBudget -- Budget Exceeded --> Halt[Halt with BudgetExceededError]
    PreBudget -- Cache Hit --> NextStep[Re-use Cached Result]
    PreBudget -- Cache Miss --> Exec[Execute Bodhan Model Operation]
    Exec --> Chunking{Requires Chunking?}
    Chunking -- Audio > 28s --> AChunk[Split on Silence/Time -> Transcribe Chunks -> Stitch]
    Chunking -- Multi-Page PDF --> OChunk[Rasterize Pages -> OCR Pages -> Aggregate Markdown]
    Chunking -- Long Document --> TChunk[Split on Paragraphs -> Translate -> Reconstruct]
    Chunking -- Single Clip/Image --> CallAPI[Direct API Call with Backoff]
    AChunk & OChunk & TChunk & CallAPI --> RecordCost[Record Cost in Budget & Trace]
    RecordCost --> Ckpt[Persist Checkpoint]
    Ckpt --> Verify[Stage 4: Verification Engine]
    Verify --> VerifyCheck{Verification Status?}
    VerifyCheck -- Passed --> NextStep
    VerifyCheck -- Retryable Failure --> Retry[Exponential Backoff / Alternative Planning]
    Retry --> Exec
    NextStep --> MoreSteps{More Steps in Plan?}
    MoreSteps -- Yes --> PreBudget
    MoreSteps -- No --> Report[Stage 5: Final Report & Artifact Generation]
    Report --> End([out/result.md & out/report.json])
```

## Stage Descriptions

1. **Task Understanding**: Inspects input files, resolves durations and page counts, detects requested source/target languages, and neutralizes injection vectors.
2. **DAG Formulation**: Generates ordered steps with explicit inputs, outputs, and dependencies.
3. **Execution & Budget Guard**: Validates that remaining step count and INR balance permit the operation before calling the Bodhan API.
4. **Resilient Chunking**:
   - Audio files longer than 28 seconds are chunked into windows and rejoined sequentially.
   - Multi-page PDFs are rasterized to PNG images at 150 DPI and OCR'd page-by-page.
   - Text documents are chunked along paragraph boundaries to maintain Markdown structure.
5. **Multi-Vector Verification**: Evaluates artifact existence, non-emptiness, audio frame duration, translation paragraph counts, and SRT subtitle formatting.
6. **Reporting**: Emits machine-readable event streams (`trace.jsonl`), audit data (`report.json`), and readable summaries (`result.md`).
