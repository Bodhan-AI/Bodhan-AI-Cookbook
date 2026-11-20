# Exact 4:44 Demo Video Script

**Target final runtime: 4 minutes, 44 seconds (284 seconds).**
**Format:** screen recording with voice-over and brief, clearly labeled edits
during provider waits.
**Demonstrations:** Hindi transcription (Task 2), worksheet OCR and Marathi
translation (Task 3), and an unsupported French video-dubbing request (Task 6).

This script is based on the retained reports. Use the actual report values if
you rerun the tasks; provider latency, retries, and estimates may change. The
Task 2 input available here is MP3, not the WAV in the original example. Keep
that caveat in the video.

## Exact timeline and screen plan

| Timecode | Duration | Picture |
|---|---:|---|
| 0:00–0:18 | 18 sec | Title and the three demo tasks |
| 0:18–0:48 | 30 sec | Architecture diagram and tool names |
| 0:48–1:32 | 44 sec | Task 2 command, completed report, transcript artifact |
| 1:32–2:20 | 48 sec | Task 3 command, OCR/translation artifacts and checks |
| 2:20–3:03 | 43 sec | Task 6 command and honest impossible result |
| 3:03–3:43 | 40 sec | Independent MCP server and structured error test |
| 3:43–4:22 | 39 sec | Trace, errors, budgets, measured results |
| 4:22–4:44 | 22 sec | Limitations and close |
| **Total** | **284 sec** | **4:44** |

The Task 2, 3, and 6 retained runs took 50.106, 52.190, and 41.481 seconds.
The screen segments are shorter, so use an edit through idle provider wait only
with a visible caption such as **“Provider wait shortened; report shows full
runtime.”** Do not speed up the trace, hide a failure, or imply the video shows
the full wall-clock time when it does not.

Before recording from the repository root, create a unique output root. Each
task below gets a new child directory; do not reuse an old run directory.

```powershell
New-Item -ItemType Directory -Force .\out | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$videoOut = Join-Path (Resolve-Path .\out).Path "video-$stamp"
New-Item -ItemType Directory -Path $videoOut | Out-Null
Write-Host "Video run outputs: $videoOut"
```

## Narration and shot directions

### 0:00–0:18 — Introduction

**On screen:** Title card: “Bodhan Tool Server and Task Agent.” Under it:
“Transcription · OCR + translation · honest impossible-task handling.”

**Say:**

> “This is the Bodhan Tool Server and Task Agent. In under five minutes, I’ll show
> Hindi transcription, worksheet OCR followed by Marathi translation, and an
> unsupported French video-dubbing request.”

### 0:18–0:48 — Architecture

**On screen:** Show the architecture diagram from
[`README.md`](./README.md#architecture), or use this simple sequence:

```text
User task + local files
        |
        v
CLI -> TaskRunner <-> OpenAI-compatible planner
        |
        v
Validated shared ToolService <-> Bodhan Indic models
        |
        +--> artifacts/ + result.md + report.json + trace.jsonl

External MCP client -> stdio MCP server -> the same ToolService
```

**Say:**

> “The CLI inventories file metadata locally and gives the task and registered
> tool schemas to an OpenAI-compatible planner. The planner selects one tool
> at a time; it does not transcribe, translate, synthesize speech, or perform
> OCR. TaskRunner validates arguments and paths, enforces step and cost
> limits, then calls the shared ToolService. A separate stdio MCP server
> exposes that same service to external clients. Every run writes artifacts,
> a report, and a JSONL trace.”

### 0:48–1:32 — Task 2: Hindi transcription

**On screen:** Show the command below, then the completed run summary. Keep the
input filename visible so the MP3/WAV caveat is clear.

```powershell
& .\.venv\Scripts\python.exe -m bodhan_agent `
  --task "Transcribe this Hindi audio and save a plain transcript. Do not create SRT timestamps." `
  --files .\demo-inputs\hindi_audio.mp3 `
  --out (Join-Path $videoOut 'task-02') `
  --max-steps 30 `
  --max-cost-inr 1
```

Use a fresh output directory for a new run. If it already exists, choose a new
name; runs are not overwritten.

**Say:**

> “The available Hindi sample is an MP3, not the WAV named in the assignment
> example. The agent chose `transcribe_audio`; Bodhan returned a transcript
> artifact. The API does not provide timestamps, so the agent does not invent
> an SRT. The retained run completed in about fifty seconds, with a recorded
> Bodhan estimate of about three paise. Actual billing is unavailable.”

**Show:** `status: completed`, the transcript artifact filename, and the
`transcribe_audio` result in `trace.jsonl`. Do not display transcript contents
unless you have permission to publish them.

### 1:32–2:20 — Task 3: OCR and Marathi translation

**On screen:** Show the command, then the two artifact names and verification
statuses.

```powershell
& .\.venv\Scripts\python.exe -m bodhan_agent `
  --task "OCR this worksheet photo, then translate the extracted text into Marathi. Preserve any table structure present in the OCR output; do not invent missing cells." `
  --files .\demo-inputs\worksheet.webp `
  --out (Join-Path $videoOut 'task-03') `
  --max-steps 30 `
  --max-cost-inr 1
```

**Say:**

> “For this worksheet, the planner selected OCR and then Marathi translation.
> The OCR text moves to the next tool by local artifact path, not by putting
> the image into the planner context. The retained run has two Bodhan tool
> operations. Its page and translation chunk checks passed, and its estimated
> Bodhan cost was about twenty-one paise. These checks confirm coverage, not
> perfect table reconstruction or semantic translation quality.”

**Show:** OCR and translation artifact filenames; report verification names
and statuses. If the actual output does not preserve a table, say so plainly.

### 2:20–3:03 — Task 6: unsupported French video dubbing

**On screen:** Show the task, the source filename, then the final report. Do not
show or play private video content.

```powershell
& .\.venv\Scripts\python.exe -m bodhan_agent `
  --task "The user requests French dubbing of this MP4. Explain that video dubbing is unsupported and French is not in the documented Indic-Speak language list. Do not call audio, OCR, or translation tools, and do not generate or claim any audio." `
  --files .\demo-inputs\video.mp4 `
  --out (Join-Path $videoOut 'task-06') `
  --max-steps 5 `
  --max-cost-inr 0.01
```

**Say:**

> “This request is unsupported for two reasons: the tools do not dub video,
> and French is not a listed Indic-Speak language. The correct result is an
> explicit `impossible` status, not fabricated media. In the retained run,
> there were zero Bodhan tool calls and no artifacts. The planner still made
> three LLM requests, so zero Bodhan cost does not mean zero total provider
> cost.”

**Show:** `status: impossible`, zero Bodhan submissions, and an empty artifact
list. If a new run behaves differently, stop and investigate rather than
claiming this expected result.

### 3:03–3:43 — Independent MCP server and structured errors

**On screen:** Open MCP Inspector 1.0.6, connect to the stdio server, select
**Tools → List Tools**, and show the five tool names. Call `transcribe_audio`
with `outputs/does-not-exist.wav` and show the structured `missing_input`
result. This check needs no Bodhan key and makes no paid provider request.

```powershell
npx --yes @modelcontextprotocol/inspector@1.0.6 python -m bodhan_mcp
```

**Say:**

> “The MCP server runs independently over standard input and output; the task
> agent is not required. MCP Inspector, an off-the-shelf client, connected to
> the server and discovered all five tools. A missing-file transcription
> request returned a structured `missing_input` error without crashing the
> client or making a paid request. Automated tests separately exercise a valid
> translation using a mocked Bodhan response.”

### 3:43–4:22 — Trace, budgets, and failure handling

**On screen:** Show only selected `plan_decision`, `tool_result`, and
`run_finished` fields from a trace, then the report's duration, cost, and
verification fields.

**Say:**

> “The trace makes the workflow inspectable: it records selected tools,
> outcomes, latency, estimated cost, retries, and verification. A cost
> estimate is reserved before a Bodhan request, and the agent stops when its
> step or cost budget is exhausted. Safe connection and rate-limit failures
> get bounded retries; when a paid request may already have reached the
> provider, it is not blindly repeated. Completed artifacts are retained if
> the run must stop. In this workspace, 100 tests pass and Ruff passes; the
> separate 11-scenario evaluation is a simulation, not live model-quality
> evidence.”

Do not open `.env`, print environment variables, or show full traces if they
contain private file names or task content.

### 4:22–4:44 — Close

**On screen:** Show a compact three-row summary:

```text
Task 2: transcript produced; MP3 input caveat
Task 3: OCR + Marathi translation; coverage checks passed
Task 6: impossible; no Bodhan call and no fake output
```

**Say:**

> “These examples show a supported single-step task, a multi-step task, and an
> honest unsupported-task result. The README documents the design and
> limitations. Bodhan estimates exclude LLM charges, and translation coverage
> is not a semantic-quality guarantee. Thank you.”

**End at 4:44.** If the final export includes a title or credits card, include
that time within the 4:44 total.

## Recording safeguards

- The recorded values are from retained reports: Task 2 took 50.106 seconds
  and estimated INR 0.03296; Task 3 took 52.190 seconds and estimated INR
  0.21382; Task 6 took 41.481 seconds and estimated INR 0.00 for Bodhan.
  Fresh runs can differ. `actual_cost_inr` and planner cost are unavailable.
- Those three historical Bodhan estimates total about INR 0.24678; the
  commands use separate ceilings, not one shared budget.
- Confirm you have rights to use and publicly show the source media and
  extracted text. Do not include source media or generated audio in Git.
- Do not claim that all six demo tasks passed. Task 1 and Task 4 summaries are
  truncated; Task 2 used MP3 rather than WAV; Task 4 language detection is
  script-based and only probable.
- Add the hosted video URL to the README and PR description after recording.
