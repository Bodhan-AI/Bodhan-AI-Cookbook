# Bodhan Tool Server and Task Agent

**Bodhan models:** `indic-ocr`, `indic-transcribe`, `indic-translate`, `indic-speak`
**Languages:** all published Indic-Speak language codes (including Hindi, Tamil, Bengali, Telugu, Marathi) plus English where supported
**Format:** Python 3.10+ CLI plus independent MCP stdio server

An agentic language-workflow recipe for the [Bodhan AI Cookbook](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook). A general-purpose LLM plans tasks and calls **Bodhan-only** OCR, transcription, translation and speech tools, without generating Indian-language text itself. The agent works with files by reference; produces text/audio/subtitle artifacts, an auditable trace, a quality report and honest error statuses; and enforces model-cost reservations and step limits.

> **Current validation status (22 Nov 2026):** **27 offline tests passed on Windows**. A real **Indic-Translate** diagnostic succeeded via Bodhan's `/v1/chat/completions` fallback after `/translate` returned HTTP 404. **The full agent, real audio generation, the six required demos, a public demo recording, and independent MCP Inspector validation remain unverified.** Do not present the assignment as completed until those steps pass. See [Engineering troubleshooting log](docs/ISSUES_AND_FIXES.md).

## Setup

Python 3.10–3.13 supported (locally tested with 3.13); ffmpeg + ffprobe are required (including on Windows). PyMuPDF renders scanned PDFs as images; it never reads PDF text layers. No GPU is needed with hosted Bodhan APIs.

```bash
# From examples/bodhan_task_agent/
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
# Windows PowerShell: Copy-Item .env.example .env
# Linux/macOS: cp .env.example .env
```

Fill `.env` **locally** (never commit it or paste keys into a public issue):

- Obtain `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY` and `BODHAN_OCR_KEY` from [Bodhan console](https://console.bodhan.ai/). A shared `BODHAN_API_KEY` is an optional fallback for the Cookbook validator; per-model keys take priority.
- Set `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` for an OpenAI-compatible general LLM endpoint supporting standard chat `tool_calls`. The LLM is **not** used for speech/OCR/translation.
- Default Bodhan base URL: `https://api.bodhan.ai`. Do not add `/v1` to `BODHAN_BASE_URL`. Translation first tries `/translate`; if that route returns 404, it falls back to Bodhan's documented `/v1/chat/completions` route with `model=indic-translate`.
- Run `ffmpeg -version` and `ffprobe -version` in the same terminal to confirm binaries are accessible.

Read the official [API documentation](https://console.bodhan.ai/api-docs/) for updated keys, voices and model availability. Dependencies are pinned in `requirements.txt` and `pyproject.toml`.

## Run

Start from the recipe root after installing dependencies and setting `.env`.

```bash
# Agent, with a folder of task files:
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ \
  --out ./outputs/tamil_summary \
  --max-steps 30 --max-cost-inr 20

# Independent MCP stdio server, for ANY MCP client:
python -m bodhan_mcp
```

The required command names and flags are stable. On Windows PowerShell, write the command on one line or use backticks instead of shell backslashes. `--files` can be a directory or a single file. The CLI copies the files to the run's private workspace so model tool paths cannot escape into arbitrary directories. A nonzero exit code indicates failed, partial or unverified completion.

**Output files** (per run):

```text
outputs/tamil_summary/
├── result.md           # task, output links, assumptions, limitations
├── artifacts/          # Markdown, translated text, WAV/MP3, SRT, quality JSON, transcripts
├── trace.jsonl         # time-stamped plan/LLM/tool events, arguments, statuses, costs
├── report.json         # completion status, verification, latency, budget
└── inputs/             # staged source files, not committed
```

`report.json` includes estimated Bodhan costs; exact charges must be confirmed in the console. General LLM cost is not estimated because each possible external provider prices differently. For reproducible paid-call minimization, responses are cached by **exact input hash and model settings** under `.bodhan_cache/` in the run workspace.

## How it works

1. **Inspect.** Enumerate files and file extensions; load text directly, render PDF pages to PNG and use Indic-OCR for all image text, or normalize audio with ffmpeg for ASR.
2. **Plan.** General LLM decides tool order and English summary content. Output text inside files and OCR responses is untrusted data, never a new instruction.
3. **Execute.** Each model call passes through a typed shared tool implementation; MCP exposes the same tools to external clients.
4. **Adapt.** For long audio, ffmpeg chunks to 25-second windows before Indic-Transcribe. TTS splits long prose into short pieces before Indic-Speak. PDF OCR runs page-by-page. Language and voice are validated against a published registry.
5. **Verify.** Translation checks exact numeric/math-token preservation, markdown table syntax, and optional Bodhan backtranslation similarity. Synthesized audio is back-transcribed using Indic-Transcribe and given a character-similarity diagnostic. QA warnings go into sidecar JSON instead of being concealed.
6. **Stop.** Budget exhaustion, missing files and unsupported language requests must yield structured errors, never fake artifacts. Save run traces, final summary and links to real files.

The LLM is the planner, not a replacement for Bodhan capabilities. The source remains text from Indic-OCR or Indic-Transcribe; translated Indic content must originate from Indic-Translate and speech from Indic-Speak. Indian-to-Indian translation takes an English pivot, matching the assignment's guidance.

### Tools exposed through MCP

| Tool | Input and output | Deterministic or Bodhan |
|---|---|---|
| `inspect_files` | Directory reference → file list | Deterministic |
| `read_text` | Text file reference → preview | Deterministic |
| `ocr_document` | Scanned PDF/image/folder → Markdown + block metadata | **Indic-OCR** |
| `transcribe_audio` | WAV/MP3/MP4 → transcript + coarse timestamp segments | **Indic-Transcribe** |
| `translate_text` | Source text/file + target language → translated Markdown + QA | **Indic-Translate** |
| `translate_segments` | Timestamped segments → translated timestamped segments | **Indic-Translate** |
| `speak_text` | Saved source text + language/voice → AI-generated WAV/MP3 + QA | **Indic-Speak**, verified by **Indic-Transcribe** |
| `create_subtitles` | Timestamped segments → SRT | Deterministic |
| `save_english_text` | English-only summary → file | Deterministic, Indic script rejected |

Each tool has JSON Schema inputs with required fields. Errors have `code`, `message`, and `retryable` fields. The independent stdio server implements MCP JSON-RPC `initialize`, `ping`, `tools/list`, and `tools/call` with newline-delimited messages (without importing a third-party MCP SDK).

### Architecture

See [Architecture and workflow diagrams](docs/ARCHITECTURE.md) and [Design decisions and QA](docs/DESIGN_NOTES.md).

### Engineering issue history

See [Issues, failed attempts and verified fixes](docs/ISSUES_AND_FIXES.md) for a dated record of Windows setup problems, Bodhan translation HTTP 404, the working alternative API route, and the outstanding Groq HTTP 429 issue. Each issue distinguishes confirmed results from hypotheses.

### Six required demo tasks

See [Live demonstration runbook](docs/LIVE_DEMO_RUNBOOK.md). The take-home requires **at least six real successful/appropriately refused demos**, including a documented impossible task; never substitute the mocked tests for these. The demo must be captured in a public, sign-in-free video of **at most five minutes** before the PR is considered complete.

### Local tests

For Windows, `setup_windows.ps1` performs the installation and offline tests and copies the environment template without overwriting an existing `.env`.

```bash
python -m pytest -q
# From a full Cookbook clone, additionally:
python scripts/validate_recipe.py examples/bodhan_task_agent
make check
```

Tests include path traversal, invalid schemas, supported languages, model budgets and cache, PDF/image OCR, timing-aware audio splitting, translation structure, speech verification, MCP stdio JSON-RPC and a locally scripted LLM run. They do **not** assert genuine model accuracy. To test from an independent MCP client, use [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector --cli python -m bodhan_mcp --method tools/list
```

If a particular Inspector version requires flags in a different order, follow its linked CLI documentation. **External Inspector validation is pending**, not claimed as done.

## Failure behavior and cost controls

- `--max-steps` counts both model-decision rounds and tool calls, with a hard ceiling.
- `--max-cost-inr` is enforced as a conservative **estimated Bodhan model-cost reservation** before every uncached model call. Voice synthesis additionally reserves room for back-transcription checks.
- Retry count for `429`/transient `5xx` is limited to three attempts with exponential backoff; the model's own budget enforcement and actual invoice may differ.
- Exact-input model outputs are cached, including binary TTS audio. This avoids recharging for routine repeats; partial runs can be resumed manually with the same output directory.
- Invalid file references, symlinks, unsupported languages/voices, missing credentials and failed upstream calls return errors. The agent may recover when possible; otherwise reports partial/failed outcomes.
- The tool server has **no general filesystem read/write permission outside `BODHAN_WORKSPACE`**. The CLI stages input files into its isolated output workspace.
- No input source or document content is executed as code. File-derived instructions are low-trust data.

## Trade-offs and known limitations

- **Partial live validation only:** a real Bodhan translation probe passed using the alternate chat endpoint. Speech synthesis, OCR/ASR on real media, end-to-end agent behavior, latency at scale, and native-speaker quality remain unverified.
- **Subtitle timestamps:** 25-second ASR chunk boundaries are correct but coarse; the pipeline does not claim word alignment. Silence-aware segmentation would improve subtitle UX.
- **Speaker diarization:** not implemented. English and Indic speech from fixed Bodhan voices, no voice cloning.
- **Indian language identification:** script-based detection is conservative. Several languages share scripts; the agent should preserve ambiguity or request/assume a hint rather than invent confidence.
- **LLM cost:** unknown without provider pricing; only Bodhan quotes are reserved. Actual billing should be reviewed in the console.
- **MCP:** minimal stdio implementation, not yet externally tested with Inspector; HTTP MCP and UI are out of scope.
- **Markdown preservation:** table pipes, numbers and LaTeX tokens are checked and restored with a conservative fallback, but complex nested Markdown may need stronger parsing; keep QA warnings visible.
- **OCR layout and audio verification:** OCR provides blocks and confidence indicators, but complex page reordering and character-similarity metrics are imperfect. Native-speaker review remains necessary.
- **No model generalization claim:** local tests cover error handling and API request shapes, not the hidden test set.

## Submission checklist

- [x] Runnable agent command and independent stdio server
- [x] Bodhan-only model endpoints, typed schemas, budget, caching, retries
- [x] Setup docs, clean-source requirements, architecture and workflow diagram
- [x] Local isolated/mocked tests and mock HTTP shape tests
- [x] Confirm model credentials present and a live Bodhan Indic-Translate probe succeeds (22 Nov 2026)
- [ ] Complete a full live agent run, including speech/verification and Groq tool calling
- [ ] Run **six real demo tasks** including one unsupported task, and link outputs/traces
- [ ] Verify at least one independent MCP client connection with MCP Inspector
- [ ] Perform Cookbook recipe validation and `make check` in a true upstream fork
- [ ] Record a real, publicly viewable demo video (≤5 minutes)
- [ ] Open a public PR in [Bodhan AI Cookbook](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook), then reply to the recruiter email with PR URL + final commit SHA

### AI assistance

This project was written with substantial assistance from an OpenAI AI coding assistant for architecture, implementation, mocks, tests and documentation. The applicant must review the resulting code, run genuine model demos, and be able to explain it in the engineering debrief. No live model results are fabricated.

### References

- [Bodhan cookbook rules and models](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/blob/main/scripts/bodhan_api_rules.json)
- [Bodhan API reference](https://console.bodhan.ai/api-docs/)
- [Cookbook contribution guidelines](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/blob/main/CONTRIBUTING.md)
