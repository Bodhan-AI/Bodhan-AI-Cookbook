# Agent flow

The agent loop: plan, act, verify, retry and stop. Components are in [architecture.md](architecture.md);
design reasoning is in [design-notes.md](design-notes.md). Diagram is Mermaid (rendered by GitHub).

```mermaid
flowchart TD
    start([task + inventory of input files]) --> plan

    plan["plan-step<br/>LLM picks tool calls<br/>(one sentence of reasoning logged)"]
    plan -->|"tool calls"| tools
    plan -->|"finish_task, alone"| fin
    plan -->|"plain text, first time"| nudge["nudge: 'call finish_task'"]
    nudge --> plan
    plan -->|"plain text again"| verify

    tools["call-tools (MCP)<br/>1. refuse if estimate > remaining budget<br/>2. per-call timeout<br/>3. screen result for injection text<br/>4. queue outputs for verification"]
    tools -->|"cost budget hit"| budget
    tools -->|"ok / error result"| stepcheck{"steps left?"}
    stepcheck -->|yes| plan
    stepcheck -->|no| budget

    fin["finish-task<br/>record status, summary, assumptions"] --> verify

    verify["verify (plain code)<br/>audio: back-transcribe and compare<br/>translation: coverage, structure, script<br/>subtitles: SRT sanity"]
    verify -->|"a check failed, repair round unused"| plan
    verify -->|"all passed, or repair already used"| done

    done["finish-completed<br/>status = planner's, downgraded to 'partial'<br/>if a check still fails or no finish_task"]
    budget["finish-budget-exhausted<br/>status = budget_exhausted<br/>what was done, what is unverified"]

    done --> out
    budget --> out
    out([result.md, report.json, trace.jsonl, artifacts/])
```

Stops: `completed`, `partial`, `impossible` (planner's honest end), `budget_exhausted` (steps or rupees), `blocked` (instruction-like text in an input: halted, no LLM sees it).
Retry rules: a failed tool call may be retried once by the planner if it is fixable; verification gets exactly one
repair round; transient API errors (429, 5xx, timeouts) are retried inside the tools with exponential backoff.
