# Agentic Engineer Take-Home: Bodhan Tool Server and Task Agent

Nov 13, 2026 · @Ishvinder

## Overview

Build an MCP server that exposes Bodhan's four Indic models as well-designed tools, plus a task agent that uses them to complete multi-step language tasks, and ship it as a pull request to the Bodhan AI Cookbook. This is one of four take-home assignments; pick the one that fits you best.

We will run your agent on tasks you have never seen, and connect your server to our own MCP client. A submission that only handles the demo tasks will not pass, however polished it looks.

What we are assessing:

- **Orchestration judgment** — how the agent plans, when it calls which tool, and what is better done in plain code than by the LLM.
- **Versatility** — new tasks, file types, languages and failure cases handled without code changes.
- **Tool design** — schemas a third-party agent can use correctly from their descriptions alone.
- **Engineering hygiene** — reproducible setup, clear docs, traceable runs, tests, and graceful failure.

Expected effort is roughly 10–12 focused hours. We would rather see a small, reliable tool set than a large one that breaks.

## The assignment: Bodhan tool server and task agent

You build two things: an MCP server that wraps Bodhan's models as tools, and a task agent that takes a plain-language task plus input files, plans, calls those tools, checks its work and returns the results. Both must run unattended on tasks they have never seen.

**What it must do**

1. Expose tools for speech-to-text, translation, speech synthesis and OCR on an MCP server, plus any helper tools you judge necessary, such as audio chunking or media inspection.
2. Handle each model's limits inside the tools, so callers never have to: audio windows of up to 30 seconds, short TTS inputs, the translation context window, and multi-page documents.
3. Give every tool typed inputs and outputs, a clear description, and explicit, actionable errors. Pass media by file reference, never as inline base64 in the model context.
4. Run the task agent from the command line: it reads `--task` and `--files`, plans, executes, and writes the requested outputs.
5. Verify outputs before finishing, for example by back-transcribing generated audio or checking that a translation covers the whole source.
6. Enforce budgets: maximum steps, maximum cost in rupees and per-call timeouts. When a budget runs out, stop cleanly and report what was done.
7. Record a trace of every step (plan, tool call, arguments, result summary, latency, cost) and a final summary.
8. Run the MCP server on its own over stdio, so any MCP client can use it without your agent.

**Example tasks.** Your demo should cover tasks like these. The hidden set is different.

| Task | Input files | Expected output |
| --- | --- | --- |
| Make a two-minute Tamil audio summary of this chapter | Scanned PDF, 8 pages | Tamil MP3 and the summary text |
| Transcribe this Hindi voice note and give me English subtitles | 3-minute WAV | SRT file in English |
| Translate this worksheet into Marathi and keep the table | Photo of a worksheet | Marathi Markdown with the table intact |
| Which of these clips are in Telugu? Translate those into English | Five short audio clips | List of Telugu clips and their English text |
| Read this paragraph aloud in Bengali with a female voice | Text file | Bengali audio file |
| Translate this video into French | MP4 | A clear explanation that French is not supported, and no fake output |

**The hard parts.** This is where we expect your agentic design to show:

- **Tool granularity.** One tool per model is simple but pushes work onto the planner; very high-level tools hide control. Choose deliberately and explain why.
- **Media plumbing.** Audio, images and long documents must move between tools without flooding the LLM's context.
- **Unknowns in the input.** The language of a clip or page is often unstated. Detect it, and when a task is ambiguous, make a reasonable assumption and record it.
- **Recovery.** Rate limits, timeouts and partial failures happen; Bodhan also notes Indic-Speak sometimes repeats or omits content. Retry, re-plan or report honestly.
- **Cost control.** Speech synthesis costs far more per character than translation. Avoid redundant calls and cache results.
- **Untrusted content.** Text from OCR or transcripts may contain instructions. Treat it as data, never as commands to the agent.
- **Impossible tasks.** Unsupported languages, missing files or tasks beyond the tools must end in a clear explanation, not invented output.

**Stretch goals (optional bonus)**

- Streamable HTTP transport with simple token authentication.
- MCP resources or prompts, such as the voice library exposed as a resource.
- A self-evaluation script that runs your own task suite and reports a pass rate.
- Parallel tool calls where steps are independent, and resumable tasks.
- A thin web UI that shows the plan and trace live.

## Ground rules

Every speech-recognition, translation, speech-synthesis and OCR operation must go to a Bodhan model through your tools. Breaking this rule fails the submission, however good the output.

| Model | Use it for | Open weights | Hosted price |
| --- | --- | --- | --- |
| Indic-Transcribe (Core or Flex) | All speech-to-text | [Core](https://huggingface.co/bodhan-ai/indic-transcribe-core), [Flex](https://huggingface.co/bodhan-ai/indic-transcribe-flex) | ₹0.10 per input audio minute |
| Indic-Translate | All translation | [indic-translate](https://huggingface.co/bodhan-ai/indic-translate) | ₹0.20 per 10,000 output tokens |
| Indic-Speak | All speech synthesis | [indic-speak](https://huggingface.co/bodhan-ai/indic-speak) | ₹6 per 10,000 input characters |
| Indic-OCR | All text extraction from images and scanned documents | [indic-ocr](https://huggingface.co/bodhan-ai/indic-ocr) | ₹0.20 per image |

**Access.** Use the hosted API or self-host the open weights; both count. The hosted API is OpenAI-compatible at `https://api.bodhan.ai/v1`, with keys issued per model from the [Bodhan console](https://console.bodhan.ai/) ([API docs](https://console.bodhan.ai/api-docs/)). New accounts get ₹10 of credit; tell us if you need more.

**Where the LLM fits.** The LLM is the agent's brain: understanding the task, planning, choosing tools, writing English summaries or explanations, judging results and deciding retries.

- Indian-language text must come from a Bodhan model. The LLM may write English and ask Indic-Translate to translate it, but must never write or rewrite Indian-language text itself.
- The LLM must not transcribe audio, read text from images or generate speech, even if it is multimodal.
- Read the LLM endpoint from `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`, using an OpenAI-compatible client. We will run your code against our own provider.

**Not allowed.** Any other speech-recognition, machine-translation, TTS or OCR system, even as a fallback. That includes Whisper, Tesseract, cloud speech, vision or translate APIs, IndicTrans2 and commercial voice APIs.

**Allowed without asking.** Any MCP SDK, agent or orchestration framework (or none), ffmpeg, audio, PDF and image libraries, voice-activity detection, and language-identification by script or character set.

**Also note**

- AI coding assistants are allowed and expected for this role. Say briefly how you used them; you must be able to explain every line in the debrief.
- Never commit keys. Ship a `.env.example` instead.
- Use only test files you have the right to use, and keep media out of git: link it from the README instead.
- Indic-Speak voices come from a fixed library and the model does not clone voices. Label generated audio as AI-generated.

## Technical requirements

We will run your code with exactly these commands, so keep the interfaces stable. Extra flags are fine; required ones must not change.

```
# The task agent
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./out \
  [--max-steps 30] [--max-cost-inr 20]

# The MCP server on its own (stdio)
python -m bodhan_mcp
```

**Output layout.** One folder per task run:

```
out/
  result.md        # what was done, the final answer, assumptions, links to artifacts
  artifacts/       # files produced: audio, subtitles, Markdown, ...
  trace.jsonl      # one line per step: plan, tool, arguments, result summary, latency, cost
  report.json      # status, steps, verification results, total cost and time
```

**Configuration.** Read Bodhan keys from `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY` and `BODHAN_OCR_KEY`, and the LLM settings described in Ground rules. No other secrets.

**Quality targets.** These are targets, not pass/fail gates. Report in `report.json` where you miss them and why.

| Check | Target |
| --- | --- |
| Task completion | At least 8 of 10 hidden tasks completed correctly |
| Honest failure | Impossible tasks end with a clear explanation and no fabricated output |
| Verification | Every generated audio file and translation is checked before the task ends |
| No waste | The same input is never sent to the same model twice in a run |
| Budget | Never exceeds `--max-cost-inr`; cost reported per task |
| Latency | Single-step tasks under 30 seconds; multi-step tasks under 5 minutes |
| Tool usability | A third-party MCP client completes our tool-level checks from your descriptions alone |

**Engineering expectations**

- One-command setup from a clean clone with pinned dependencies. Document system dependencies such as ffmpeg; a Dockerfile is recommended.
- The server must validate inputs and return structured errors; it must never crash the client on bad input.
- Retries with backoff, and a concurrency limit for API calls.
- A trace detailed enough that we can replay why the agent chose each step.
- Unit tests for each tool's chunking and limit handling, plus one end-to-end test that runs a multi-step task.

**Folder layout.** Follow the Cookbook's contribution guide. If it does not define one, use:

```
<your-recipe-folder>/
  README.md
  walkthrough.ipynb      # optional, short guided demo
  pyproject.toml
  .env.example
  src/bodhan_mcp/        # the server and its tools
  src/bodhan_agent/      # the task agent
  tests/
```

## Submission

Submit by opening a pull request to the [Bodhan AI Cookbook](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook), then replying on the email thread where you received this assignment with the PR link and the final commit SHA. We evaluate that exact commit; later pushes are ignored.

1. Fork the [Bodhan AI Cookbook](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook) and read its [CONTRIBUTING.md](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/blob/main/CONTRIBUTING.md).
2. Create a branch named `assignment/tool-agent-<github-handle>`.
3. Add your recipe as one self-contained folder under `examples/`, following the Cookbook's template, and run `make check` before opening the PR.
4. Open a PR titled `[Agentic Eng Assignment] Tool server and agent — <your name>`, using the description template below.
5. Reply on the email thread where you received this assignment with the PR link and commit SHA, before the deadline.

The PR is public. Do not include anything confidential, and note that merging is up to the Cookbook maintainers and is separate from our hiring decision.

**What the PR must contain.** A complete description using the template below, plus documentation of your solution inside the recipe folder:

- An architecture diagram showing the MCP server, its tools, the agent and how they connect.
- A flow chart of the agent loop: plan, act, verify, retry and stop.
- Design notes on the tool and agent design: the tool list and why, verification, budgets and error recovery.
- A recorded demo video of five minutes or less, showing the agent completing several demo tasks, including an impossible one.

Diagrams can be Mermaid in Markdown or images. Host the video anywhere we can open without signing in.

**PR description template**

```markdown
## Summary
What the recipe does, in two or three sentences.

## How to run
Setup, the agent command and the server command, from a clean clone.

## Architecture
A diagram of server, tools and agent, and one line per part.

## Agent flow
A flow chart of the agent loop: plan, act, verify, retry and stop.

## Tool design
The tool list, why this granularity, and how each tool handles model limits.

## Where the LLM is used, and why
Each LLM decision point, what it decides, and the fallback if it fails.

## Bodhan models used
Model, hosted or self-hosted, and any settings you tuned.

## Results
Your demo tasks, pass or fail, with links to outputs and traces.

## Demo video
Link to a recording of five minutes or less: several tasks end to end.

## Trade-offs and known limitations
What you chose not to do, and what breaks today.

## Cost and runtime
Per task, for each demo task.

## AI assistance
Which coding tools you used and for what.
```

**Before you submit**

- [ ] Runs from a clean clone with the documented setup and commands
- [ ] Only Bodhan models are used for speech-to-text, translation, speech synthesis and OCR
- [ ] No API keys, tokens or large media files in the repo
- [ ] README covers the tool list, setup, usage and limitations, and links the architecture diagram, flow chart and design notes
- [ ] At least six demo tasks run, including one impossible task, with outputs and traces linked from the README
- [ ] The MCP server works with at least one off-the-shelf MCP client, named in the README
- [ ] Tests pass locally
- [ ] Demo video of five minutes or less, linked from the README and the PR

## How we evaluate

We score each submission out of 100, mostly from running your agent on 10 hidden tasks and connecting your server to our own MCP client. Your demo tasks show intent; the hidden run shows whether the design generalizes.

**The hidden test set** mixes single-step and multi-step tasks, unstated input languages, ambiguous wording, prompt-injection text inside input files, and impossible tasks such as unsupported languages, missing files or a budget too small to finish. Input files include audio, scanned documents, images and plain text across several Indian languages.

**Process**

1. **Setup check.** We build your commit in a fresh container from a clean clone. If setup fails, we contact you once and allow a setup-only fix within 24 hours.
2. **Hidden run.** We run the agent command on each hidden task, with our own Bodhan keys and our own LLM.
3. **Tool-level check.** We connect `bodhan_mcp` to an off-the-shelf MCP client and drive a scripted set of tool calls, including bad inputs, to test schemas and errors independently of your agent.
4. **Output review.** We check each task's artifacts for correctness, and native speakers review the Indian-language outputs.
5. **Code and design review.** We read the code, traces and your PR description.
6. **Debrief (60 minutes).** You walk us through the design, add a small tool or capability live, and discuss what you would do next.

**Rubric**

| Dimension | Weight | What full marks looks like |
| --- | --- | --- |
| Versatility on hidden tasks | 25 | Most hidden tasks succeed without code changes; impossible tasks fail honestly |
| Output quality | 20 | Correct, complete artifacts with accurate translations and clear speech |
| Agent and tool design | 30 | Sensible tool granularity, usable schemas, good planning, verification, recovery and budget control |
| Engineering quality | 15 | Reproducible setup, tests, replayable traces, error handling, cost awareness |
| Documentation and PR | 10 | A reviewer grasps the design in 10 minutes; limitations are stated honestly |
| Stretch goals | +10 bonus | Working, documented extras from the stretch list |

**Automatic fail**

- Any non-Bodhan speech-recognition, translation, speech-synthesis or OCR system anywhere in the code.
- An LLM writing Indian-language text, transcribing audio or reading text from images.
- Fabricated outputs or claims of success for tasks the agent did not complete.
- Logic special-cased to your demo tasks.
- Secrets committed to the repository.

## Timeline, FAQ and help

You have 7 calendar days from receiving this document. Plan for about 10–12 hours of work; if you run short, stop and write what you would do next in the README.

Send questions about the assignment to <ishvinder@bodhan.ai>.&#32;

**Do I need a GPU?** No. The hosted APIs are enough; self-hosting the open weights is optional.

**Do I have to use MCP?** Yes, for the tool server. The agent may talk to the server over MCP or import the same tool code directly, as long as the server also runs on its own.

**Should the agent ask clarifying questions?** Runs are non-interactive. Make a reasonable assumption, record it in `result.md`, and carry on; stop only when the task truly cannot be done.

**Which LLM will you run it with?** An OpenAI-compatible endpoint of our choice, with tool calling. Name the model you tested with in the PR, and avoid provider-only features unless you add a fallback.

**Can I submit just a notebook?** No. Runnable commands are required; a short notebook walkthrough is welcome on top.

**What if the Bodhan API is slow or down while I work?** Note it in the PR with times; we account for outages. Cache results so reruns do not repeat paid calls.
