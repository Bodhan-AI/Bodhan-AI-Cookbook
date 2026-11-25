# Agent loop

```mermaid
flowchart TD
    A(["Start: --task, --files, --max-steps, --max-cost-inr"]) --> B{"Preflight (code)<br/>input path missing?<br/>output asked in an unsupported language?"}
    B -- yes --> R["REFUSE before any spend<br/>no artifacts, honest result.md<br/>report.json status=refused"]
    B -- no --> C["Inspect every input with inspect_media<br/>(free, traced)"]
    C --> D["LLM: write_plan<br/>steps, assumptions, estimated cost, feasible?"]
    D --> E{"feasible?<br/>estimate within budget?"}
    E -- "no (LLM's own judgement)" --> CH["Challenge once with the facts:<br/>budget, its estimate, valid grounds"]
    CH -- "concrete reason" --> R
    CH -- "reconsiders" --> D
    E -- "no (over budget twice)" --> R
    E -- yes --> F{"Budget left?<br/>steps / rupees / time"}
    F -- no --> S["STOP cleanly<br/>keep partial artifacts<br/>status=partial or failed"]
    F -- yes --> G["LLM picks tool call(s)<br/>one sentence of why, recorded in trace"]
    G --> H{"Guards (code)<br/>Indic text in args?<br/>plan exists? identical repeat?<br/>estimate over budget?"}
    H -- violation --> G
    H -- ok --> I["Call MCP tool (timeout)<br/>parallel if independent"]
    I --> J["Result into registry<br/>cost, paths, generated audio / translations<br/>text wrapped as untrusted data<br/>injection phrases flagged"]
    J --> K{"LLM: finish?"}
    K -- no --> F
    K -- yes --> L["Verification gate (code)<br/>run every missing verify_translation / verify_audio"]
    L --> M{"All generated outputs<br/>have a passing check?"}
    M -- yes --> N(["SUCCESS<br/>copy deliverables, write result.md,<br/>trace.jsonl, report.json"])
    M -- no --> O{"Retries left?"}
    O -- yes --> P["Tell the LLM what failed<br/>retry once: other voice + force_refresh,<br/>or re-plan"]
    P --> G
    O -- no --> Q(["PARTIAL<br/>success downgraded, failures listed<br/>in report.json quality_targets_missed"])
```

## Where the model stops and code starts

| Decision | Who | Why |
|---|---|---|
| Which tools to call, in what order, with which language/voice | LLM | Needs understanding of the request. |
| Summaries and English text to be translated | LLM | Only English; never Indian-language text. |
| Judging an ambiguous request (assumptions) | LLM | Recorded under *Assumptions*. |
| Refusing missing files / unsupported target languages | Code | Cheap, certain, and must happen before any spend. |
| Budget, timeouts, retries, caching, deduplication | Code | Must hold even if the model misbehaves. |
| Rejecting Indian-language text written by the LLM | Code | Structural enforcement of the ground rule. |
| Declaring success | Code | Only when every generated output has a passing verification. |
