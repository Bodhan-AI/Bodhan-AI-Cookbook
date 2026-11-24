# Agent flow

How one task runs, from the command line to `report.json`. Component view: [architecture.md](architecture.md).

## 1. Run lifecycle

![Run lifecycle](img/agent_lifecycle.png)

<details>
<summary>Mermaid source for this diagram (GitHub draws it; VS Code needs a Mermaid extension)</summary>

```mermaid
flowchart TB
  start(["python -m bodhan_agent --task --files --out"]) --> SETUP

  subgraph SETUP["Setup"]
    direction LR
    reset["Fresh out/ folder:<br/>clear work and artifacts,<br/>no spend carries over"] --> spawn["Start bodhan_mcp over stdio:<br/>only the input paths,<br/>the workspace, the cost cap"] --> pre["Preflight: inspect_media<br/>on every file, note missing<br/>paths, scan text for injection"] --> prompt["Prompt: TASK, wrapped<br/>file facts, step limit.<br/>The cost cap is NOT shown"]
  end

  SETUP --> L

  subgraph L["Agent loop  (up to --max-steps)"]
    direction TB
    step["Ask the LLM:<br/>one reply with tool calls"] --> kind{"what came back?"}
    kind -- "no tool call" --> nt{"3rd time in a row?"}
    nt -- no --> step
    nt -- yes --> leave
    kind -- "submit_plan" --> plan["Run first: record interpretation,<br/>assumptions, languages.<br/>An impossible plan stops here"]
    kind -- "other tool calls" --> exec["Guards, then run through MCP,<br/>independent calls in parallel,<br/>each one traced"]
    kind -- "finish" --> fin{"finish call<br/>accepted?"}
    fin -- "no: error back to the LLM" --> step
    fin -- yes --> leave
    plan --> step
    exec --> stop{"stop condition?<br/>budget hit, credits gone,<br/>wall clock, step cap, LLM error"}
    stop -- no --> step
    stop -- yes --> leave
    leave(["Leave the loop"])
  end

  leave --> ver["Verify in code<br/>only if finish was reached<br/>and the task was not refused"]
  ver --> fz["Finalize: status from the evidence,<br/>copy artifacts, write result.md<br/>and report.json"]
  fz --> done(["print status and cost lines,<br/>exit code 0, 2 or 3"])
```

</details>

Every `except` in `run()` still reaches the finalizer, so a crash leaves a readable `result.md` and `report.json` with status `failed` instead of nothing.

## 2. Guards on every tool call

![Guards on every tool call](img/agent_guards.png)

<details>
<summary>Mermaid source for this diagram (GitHub draws it; VS Code needs a Mermaid extension)</summary>

```mermaid
flowchart TD
  call(["LLM tool call"]) --> parse{"valid JSON object?"}
  parse -- no --> e1["INVALID_ARGUMENT back to the LLM"]
  parse -- yes --> why["pull out the model's own<br/>why field, log it as the reason"]
  why --> isplan{"plan submitted yet?"}
  isplan -- "no, first 2 times" --> e2["PLAN_REQUIRED back to the LLM"]
  isplan -- yes --> indic{"Indian-language text<br/>in the arguments?"}
  indic -- yes --> e3["INVALID_ARGUMENT:<br/>translate English with a tool<br/>and pass the text_ref"]
  indic -- no --> memo{"identical call<br/>already succeeded?"}
  memo -- yes --> m1["answer from memory,<br/>cost Rs 0, traced as memo"]
  memo -- no --> rep{"same call failed<br/>3 times?"}
  rep -- yes --> e4["REPEATED_CALL back to the LLM"]
  rep -- no --> src{"text_ref source<br/>has injection phrases?"}
  src -- yes --> flag1["flag it, keep going"]
  src -- no --> run
  flag1 --> run["Run the tool through MCP<br/>timeout scaled by input size"]
  run --> res{"result"}
  res -- ok --> post["Remember it, add paths to produced files,<br/>scan the whole result text for injection,<br/>trace args, latency, cost,<br/>result summary"]
  res -- error --> er{"which error?"}
  er -- "BUDGET_EXCEEDED" --> bh["record budget hit,<br/>loop stops cleanly"]
  er -- "INSUFFICIENT_CREDITS" --> cs["credit stop,<br/>nothing more is spent"]
  er -- "file not found x3" --> mf["hint: stop guessing,<br/>finish as unsupported<br/>reason missing_file"]
  er -- other --> ret["structured error to the LLM:<br/>retry, re-plan or stop"]
  post --> out(["result for the LLM,<br/>previews wrapped as untrusted"])
```

</details>

### The finish call is checked too

![The finish call is checked too](img/finish_checks.png)

<details>
<summary>Mermaid source for this diagram (GitHub draws it; VS Code needs a Mermaid extension)</summary>

```mermaid
flowchart TD
  f(["finish(status, summary, deliverables)"]) --> uns{"status is unsupported?"}
  uns -- yes --> chk{"reason verifiable?<br/>language is really unsupported,<br/>file really missing,<br/>or beyond the tools.<br/>Never a cost worry"}
  chk -- "no, up to 3 times" --> rej["rejected: LLM must try again"]
  chk -- "no, keeps insisting" --> part["recorded as partial,<br/>marked unverified"]
  chk -- yes --> ok1["accepted: no artifacts, Rs 0 spent"]
  uns -- no --> pr{"Indian-language text in<br/>summary, assumptions or descriptions?"}
  pr -- "yes, first 2 times" --> rej2["asked to rewrite in English"]
  pr -- "yes, after that" --> strip["text removed from result.md<br/>and a warning is added"]
  pr -- no --> del{"every deliverable<br/>produced by a tool?"}
  strip --> del
  del -- "no, first 2 times" --> rej3["asked to use tool paths only"]
  del -- "no, after that" --> drop["foreign files dropped,<br/>warning added"]
  del -- yes --> acc["finish accepted"]
```

</details>

## 3. One real run as a sequence

Task: "Make a short Tamil audio summary (about 20 seconds) of this chapter", input is a 2-page scanned PDF. Names and order come from `demo_outputs/tamil_summary/trace.jsonl`.

![One real run as a sequence](img/sequence_tamil_run.png)

<details>
<summary>Mermaid source for this diagram (GitHub draws it; VS Code needs a Mermaid extension)</summary>

```mermaid
sequenceDiagram
  autonumber
  participant A as Agent
  participant L as LLM
  participant S as MCP server
  participant B as Bodhan API

  A->>S: inspect_media(chapter.pdf)
  S-->>A: kind pdf, 2 pages
  A->>L: TASK plus wrapped file facts
  L->>A: submit_plan (Tamil audio, about 20 s, assumptions)
  A->>S: ocr_document(chapter.pdf)
  S->>B: Indic-OCR, one page image per request (2 calls)
  B-->>S: markdown per page
  S-->>A: markdown_ref, cost Rs 0.40 when uncached
  A->>S: read_text_file(markdown_ref)
  S-->>A: text, wrapped as untrusted for the LLM
  A->>S: detect_language(text)
  S-->>A: English
  L->>A: write_text_file (English summary sized to 20 s)
  A->>S: translate_text(text_ref, ta)
  S->>B: Indic-Translate
  B-->>S: Tamil text
  S-->>A: translation_ref, cost Rs 0.001
  A->>S: synthesize_speech(text_ref, ta)
  S->>B: Indic-Speak, sentence chunks
  B-->>S: WAV chunks
  S-->>A: audio_ref MP3, 16.4 s, cost Rs 0.115
  L->>A: finish (deliverable is the MP3)
  Note over A,B: Verification runs in code, not by the LLM
  A->>S: transcribe_audio(audio_ref, ta)
  S->>B: Indic-Transcribe
  B-->>S: back-transcript
  A->>A: similarity 0.97 or more, dropped words 0, length 16 s in 12 to 30 s
  A->>S: translate_text(sample of Tamil, en)
  S->>B: Indic-Translate (back-translation sample)
  A->>A: coverage check against the English source
  A->>A: status from evidence, write result.md, report.json, trace.jsonl
```

</details>

## 4. How the final status is decided

![How the final status is decided](img/status_rules.png)

<details>
<summary>Mermaid source for this diagram (GitHub draws it; VS Code needs a Mermaid extension)</summary>

```mermaid
flowchart TD
  s0(["after the loop and verification"]) --> r{"refused or finish says unsupported?"}
  r -- yes --> U["unsupported<br/>no artifacts, exit 0"]
  r -- no --> fc{"finish completed,<br/>deliverables all found,<br/>and something produced?"}
  fc -- yes --> hb{"a hard check failed?<br/>back-transcription, translation,<br/>subtitles, OCR pages"}
  hb -- yes --> P1["partial<br/>exit 2"]
  hb -- no --> w{"any warning, soft failure,<br/>injection flag or tool warning?"}
  w -- yes --> CW["completed_with_warnings<br/>exit 0"]
  w -- no --> C["completed<br/>exit 0"]
  fc -- no --> cr{"balance too low<br/>and nothing produced?"}
  cr -- yes --> F1["failed<br/>exit 3"]
  cr -- no --> bu{"budget hit,<br/>or cost at 98 percent of cap,<br/>or step cap reached?"}
  bu -- yes --> BE["budget_exhausted<br/>partial work kept, exit 2"]
  bu -- no --> nt{"model stopped calling tools<br/>or something was produced?"}
  nt -- "files exist" --> P2["partial<br/>exit 2"]
  nt -- "nothing" --> F2["failed<br/>exit 3"]
```

</details>

## 5. What the trace and report record

`trace.jsonl` is one JSON line per event, written and flushed as it happens, so it can be followed live with `tail -f`. Secrets are scrubbed before anything is written.

| Event `type` | Carries |
|---|---|
| `preflight` | the facts for every input file, missing paths |
| `plan` | interpretation, assumptions, steps, target languages |
| `tool_call` | step, tool, arguments, the model's `reason`, latency, cost, cumulative cost, cached flag, result summary, error, status (`ok`, `error`, `memo`) |
| `verify` | artifact, check, ok, scores, and for paid checks their cost and latency |
| `repair` | which speech chunks were re-synthesised, cost, latency |
| `injection_suspected` | where the text came from and which phrases matched |
| `refusal`, `llm_retry`, `llm_error`, `assistant_text`, `error`, `out_dir_reset` | recovery and failure events |
| `finish`, `final` | the accepted finish call, the end status |

`report.json` holds: `status`, `task`, `interpretation`, `assumptions`, `deliverables`, `steps`, `tool_calls_by_tool`, `verification`, `cost_inr` (total, cap, by model), `duration_s`, `llm` (model, tokens), `target_misses`, `warnings`, `injection_flags`, `tool_warnings`, `budget` and `unsupported_reason`.
