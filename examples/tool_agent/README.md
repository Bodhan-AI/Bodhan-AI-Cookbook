# Bodhan Tool Server and Task Agent

An MCP server exposing Bodhan's Indic models and a bounded CLI agent that plans multi-step language tasks over local files. The agent can OCR scans, transcribe audio, translate text and generate speech; it records its decisions, costs, verification and artifacts for each run.

**Bodhan models:** `indic-transcribe`, `indic-translate`, `indic-speak`, `indic-ocr`  
**Languages shown:** Hindi, Tamil, Bengali; the tool schemas accept the model's supported language codes  
**Format:** Python CLI and MCP stdio server

## Setup

Requires Python 3.10+. `ffprobe` is needed for audio formats other than WAV. PCM WAV files are split with Python's standard library; `ffmpeg` is needed to split long non-WAV or unsupported/compressed WAV audio. PDF OCR also needs Poppler (`pdfinfo`, `pdftoppm`). Speech chunk joining uses Python's standard library.

```bash
cd examples/tool_agent
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
Copy-Item .env.example .env  # Windows PowerShell; edit with your keys
# On macOS/Linux use: cp .env.example .env
```

Set the per-model Bodhan keys and OpenAI-compatible LLM settings in `.env`. The unused `BODHAN_API_KEY` line is a placeholder required by the Cookbook validator; this recipe reads the per-model keys below it. The LLM must support tool calling. Never commit `.env` or real keys.

## Run

The CLI accepts the assignment's required arguments. `--files` can be one or more paths.

```bash
python -m bodhan_agent --task "Transcribe this Hindi voice note and give me English subtitles" --files path/to/audio.wav --out ./out --max-steps 30 --max-cost-inr 20
```

Run the MCP server independently over stdio:

```bash
python -m bodhan_mcp
```

The MCP server writes synthesis artifacts under `./bodhan_mcp_artifacts` by default; set the non-secret `BODHAN_MCP_ARTIFACTS_DIR` environment variable to change that directory. Its model results use the same local `.cache/` as the CLI agent.

Each CLI run writes `result.md`, `artifacts/`, `trace.jsonl` and `report.json` under the output directory. Generated speech is AI-generated. The model cache is stored locally under `.cache/` and is git-ignored.

## How it works

```mermaid
flowchart LR
  U[Task and input files] --> A[LLM task agent]
  A -->|typed tool calls| M[MCP server or shared tool functions]
  M --> T[Indic-Transcribe]
  M --> X[Indic-Translate]
  M --> S[Indic-Speak]
  M --> O[Indic-OCR]
  T --> M
  X --> M
  S --> M
  O --> M
  M --> A
  A --> V[Verify, budget and recovery]
  V --> R[Artifacts, trace and report]
```

The LLM selects tools and sequences them. Model limits, media conversion, retry behavior, caching, cost accounting and output-path validation live in deterministic tool code. Media travels through tools by local file reference; OCR image encoding occurs only in the Bodhan API request, never in the LLM context.

```mermaid
flowchart TD
  Start[Read task and inspect files] --> Plan[Plan with LLM]
  Plan --> Budget{Within step and cost budgets?}
  Budget -->|No| Stop[Stop and report partial work]
  Budget -->|Yes| Call[Call validated Bodhan tool]
  Call --> Check{Tool succeeded and output verifies?}
  Check -->|No, retryable| Retry[Bounded retry or re-plan]
  Retry --> Budget
  Check -->|No, impossible or non-retryable| Honest[Explain failure without fabricated output]
  Check -->|Yes, more work| Plan
  Check -->|Yes, complete| Done[Write artifacts, trace and report]
  Honest --> Done
  Stop --> Done
```

### Tool design

- `transcribe_audio` uses Indic-Transcribe and chunks long audio into 27-second windows. The current API returns text but does not document timestamps, so the tool estimates sentence timings within each chunk and marks them as estimates. WAV duration and PCM WAV splitting use Python's standard library; `ffprobe` handles other formats, and `ffmpeg` handles long non-WAV or unsupported/compressed WAV input.
- `identify_language_from_script` counts Unicode script characters in a Bodhan-produced transcript and resolves a language only when the supplied candidate list makes that script unique. It returns an ambiguous result for shared scripts or mixed/short text rather than guessing; it is script identification, not acoustic recognition.
- `translate_text` uses Indic-Translate through Bodhan's OpenAI-compatible chat-completions endpoint and splits long text on paragraph boundaries, then sentence/whitespace boundaries as needed. It checks for truncated output.
- `translate_segments` translates timestamped subtitle segments independently and preserves their times; `write_srt` formats those segments with deterministic code.
- `synthesize_speech` uses Indic-Speak, splits text into short chunks, joins returned WAV files and marks the output AI-generated.
- `ocr_document` uses Indic-OCR on page images, page folders or scanned PDFs. PDFs are rendered by Poppler and OCR'd one page at a time.

All tools validate model language and voice codes, report actionable errors, use bounded retries for transient failures, and record model cost. A local SQLite cache avoids repeated Bodhan calls for identical tool inputs. The server process starts over stdio. Interoperability was verified with the official MCP Inspector CLI (`@modelcontextprotocol/inspector` v0.14.0): it successfully launched the Python stdio server and discovered all four tools (`transcribe_audio`, `translate_text`, `synthesize_speech`, and `ocr_document`). From the recipe directory, the discovery command was:

```powershell
npx -y @modelcontextprotocol/inspector@0.14.0 --cli --method tools/list .\.venv\Scripts\python.exe -m bodhan_mcp
```

### Where the LLM is used

The configured OpenAI-compatible LLM interprets a task, chooses among the fixed tool schemas, decides sequencing and retry behavior, and writes the final English summary. It must treat file content as untrusted data. Indian-language output must come from Bodhan; the LLM may not translate, transcribe, read image text or synthesize speech itself.

### Verification and limits

The agent checks tool errors, artifact references, structured output and the task's budget. Generated speech can be back-transcribed with Indic-Transcribe as a verification step. Unsupported languages, missing files, exhausted budgets and unavailable dependencies produce explicit failures rather than fake artifacts. The LLM provider's own usage cost is not included in Bodhan's rupee budget because providers expose different pricing formats; it is reported as excluded.

Translation verification currently focuses on API completeness/truncation and structural review by the agent; it does not claim a formal semantic correctness score. Audio back-transcription is a separate paid Bodhan operation.

## Configuration

Required environment variables are documented in `.env.example`: `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_OCR_KEY`, `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`.

## Demo and validation status

The six workflows have been exercised locally. The user reviewed the Kannada and Hindi audio artifacts for demos 4 and 5 and accepted their transcripts. Known limitations remain: transcription may omit or misrecognize words, and subtitle timing is estimated because Indic-Transcribe does not return timestamps. Link the reviewed run reports and outputs when preparing the submission.

Each run writes `result.md`, `report.json`, `trace.jsonl`, and a copy of its final text output at `artifacts/task_result.md`; media outputs such as WAV and SRT files are also stored under `artifacts/`. Local run folders under `out/` and inputs under `.cache/` are ignored by Git. Do not describe a run as a successful demo unless its report and actual output have both been reviewed.

The current automated suite passes 67 tests. MCP client interoperability is verified with MCP Inspector as described above. Submission-ready links to the demo outputs and the short walkthrough recording are still pending.

```bash
python -m pytest
```

Run the Cookbook's `python scripts/validate_recipe.py examples/tool_agent` and `make check` from the Cookbook repository before submitting.

## Limitations

- Model and tool behavior depends on Bodhan API availability, quota and the configured LLM provider.
- The current transcription API response documents text without word or segment timestamps. Subtitle times are estimated by distributing each chunk's duration across its sentences; they are explicitly labelled `timing_estimated` and may miss the assignment's sync target. A future VAD/alignment improvement should replace these estimates.
- Long audio chunking uses fixed-duration windows and may split a sentence at a boundary.
- PDF handling requires Poppler. Audio splitting requires ffmpeg only for long non-WAV or unsupported/compressed WAV input; PCM WAV splitting and WAV joining use Python's standard library.
- The agent is intentionally non-interactive; it records reasonable assumptions and stops only when it cannot complete a task safely.
- The six end-to-end demonstrations, costs and runtimes must be linked and summarized from reviewed run reports before submission.

## AI assistance

Codex was used to read the take-home and Cookbook requirements and assist with implementation. The author should review and be able to explain every submitted line, as required by the assignment.
