# Bodhan Tool Server and Task Agent

**Bodhan models:** `indic-ocr`, `indic-transcribe`, `indic-translate`, `indic-speak`
**Languages:** all published Indic-Speak language codes (including Hindi, Tamil, Bengali, Telugu, Marathi) plus English where supported
**Format:** Python 3.10+ CLI plus independent MCP stdio server

An agentic language-workflow recipe for the [Bodhan AI Cookbook](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook). A general-purpose LLM plans tasks and calls **Bodhan-only** OCR, transcription, translation and speech tools, without generating Indian-language text itself. The agent works with files by reference; produces text/audio/subtitle artifacts, an auditable trace, a quality report and honest error statuses; and enforces model-cost reservations and step limits.

> **Verified to date (23 Nov 2026):** All six example scenarios have now been exercised. Bengali narration, Hindi subtitles, Telugu identification and the French MP4 negative test produced the expected class of results; the autonomous Marathi worksheet produced a translated document with formatting/QA warnings; the Tamil audio summary was generated, but OCR obtained only 5 of 8 pages. Independent MCP Inspector calls succeeded. The submitted project had 48 offline tests before the latest unsupported-language preflight regressions; re-run the expanded suite in the final submission clone before citing an updated count. The public demo video and evidence URLs are still **pending**. See [live validation results](docs/LIVE_VALIDATION_RESULTS.md).

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

See [Issues, failed attempts and verified fixes](docs/ISSUES_AND_FIXES.md) for a dated record of Windows setup problems, Bodhan translation HTTP 404, the working alternative API route, and how the Groq HTTP 429 did not recur in the first complete live run. Each issue distinguishes confirmed results from hypotheses.

### Six required demo tasks

All six example scenarios have been exercised through the autonomous agent.

- **Bengali:** Female-voice narration completed and verified through back-transcription.
- **Hindi:** English SRT subtitles generated successfully from a short audio sample.
- **Marathi:** Autonomous OCR and translation completed, with documented numeric-formatting warnings.
- **Tamil:** Tamil summary and MP3 generated, but OCR succeeded on only 5 of 8 scanned pages.
- **Telugu:** Correctly identified and translated the two Telugu clips among five inputs.
- **French MP4:** Correctly refused the unsupported language request without generating fabricated output.

See [Live validation results](docs/LIVE_VALIDATION_RESULTS.md) for execution traces, costs, quality checks and limitations.

A public demonstration video of no more than five minutes is required for the final submission.

### Local tests and external MCP verification

The Windows submission clone passed **48 offline tests** before the generic unsupported-language preflight patch; re-run the expanded suite and Cookbook checks in the actual final PR clone before final submission:

```bash
# From examples/bodhan_task_agent after pip install -e .
python -m pytest -q
# From the full Cookbook root:
python scripts/validate_recipe.py examples/bodhan_task_agent
make check
```

On Windows without GNU Make, the individual `Makefile` targets can be executed with the same Python/pip commands in that file, or use Git Bash/WSL. Do not claim `make check` passed until it actually runs.

An independent **MCP Inspector** connection succeeded: `tools/list` exposed nine tools; external `inspect_files` and `read_text` calls succeeded; and an external `ocr_document` call processed one worksheet image. These are real independent client invocations, not just unit tests. `tools/list` proves discoverability, while the actual tool calls prove selected tool execution; not every MCP tool was externally tested.

```powershell
npx.cmd -y @modelcontextprotocol/inspector --cli .\.venv\Scripts\python.exe -m bodhan_mcp --method tools/list
```

## Failure behavior and cost controls

- `--max-steps` counts both model-decision rounds and tool calls, with a hard ceiling.
- `--max-cost-inr` is enforced as a conservative **estimated Bodhan model-cost reservation** before every uncached model call. Voice synthesis additionally reserves room for back-transcription checks.
- Retry count for `429`/transient `5xx` is limited to three attempts with exponential backoff; the model's own budget enforcement and actual invoice may differ.
- Exact-input model outputs are cached, including binary TTS audio. This avoids recharging for routine repeats; partial runs can be resumed manually with the same output directory.
- Invalid file references, symlinks, unsupported languages/voices, missing credentials and failed upstream calls return errors. The agent may recover when possible; otherwise reports partial/failed outcomes.
- The tool server has **no general filesystem read/write permission outside `BODHAN_WORKSPACE`**. The CLI stages input files into its isolated output workspace.
- No input source or document content is executed as code. File-derived instructions are low-trust data.

## Trade-offs and known limitations

- **Six real task runs now recorded:** Bengali and Hindi workflows succeeded; the autonomous Marathi worksheet produced all required file types but has time-token formatting/QA warnings; Telugu correctly selected two Telugu clips; the unsupported French MP4 was refused generically. The Tamil pipeline produced narration from only 5/8 OCR pages (three HTTP 502s), and is **partial** rather than an all-pages success.
- **Marathi QA:** The first translation QA sidecar had `numbers_preserved: false`; a later independent audit of the saved documents found number order, table cells and LaTeX preserved. We report both and do not equate the audit with native-speaker review.
- **Time and speech alignment:** ASR chunk boundaries are coarse. The Hindi sample generated one subtitle cue for a 6.485-second clip, not word-level timings.
- **Model/API reliability:** `/translate` returned HTTP 404 in the tested environment; translation succeeded through the Bodhan `indic-translate` chat-completions fallback. Rate limiting caused a failure during a longer Marathi task. Transient retries are bounded.
- **Cost:** Bodhan cost figures below are conservative estimates in run reports, excluding LLM-provider costs and actual invoicing.
- **Third-party MCP coverage:** Discovery and three tool invocations verified; hidden third-party-client tests remain unknown.
- **Native-speaker evaluation:** Not independently performed; back-ASR similarity and roundtrip similarity are diagnostics only. No claim about hidden-set task completion.

## Live results (not the hidden benchmark)

### Demonstration evidence

[View execution reports, traces and generated artifacts](https://drive.google.com/file/d/1Gic-Uy80-4bDijzAQhJsO2miunY6BP2v/view?usp=sharing)

Includes evidence from six demonstration scenarios,
with documented partial results and known limitations.

These outcomes are based on actual local reports and traces, not hidden-test performance. Tamil is a **partial** result, and the Marathi run still has QA warnings. All prices below are estimated Bodhan charges, not invoices; general LLM costs are excluded.

| Demonstration | Outcome and caveats | Runtime | Est. Bodhan cost |
| --- | --- | ---: | ---: |
| English paragraph → Bengali female speech | Completed, 12.032-second WAV; back-ASR similarity 0.975 | 19.39 s | ₹0.1071 |
| Hindi WAV → English SRT | Completed on a 6.485-second sample, one coarse-timed cue; not a three-minute validation | 121.67 s | ₹0.0266 |
| Worksheet image → Marathi Markdown | **Autonomous `completed`**, OCR and translation outputs saved; table/math retained, but strict number QA fails due to formatting such as `9:00ए. एम.` | 74.40 s | ₹0.2953 |
| Eight-page scanned PDF → Tamil narration | **Partial quality:** only 5/8 pages OCR'd (HTTP 502 on pages 1, 2 and 8); Tamil MP3 2m42s rather than ~2m, back-ASR similarity 0.714; over five-minute multi-step target | 442.02 s | ₹3.6402 |
| Five audio clips → identify and translate Telugu | Completed, selected clip_01/clip_04 and translated only these; clip_03 correctly left unclassified when provider language was unknown | 88.34 s | ₹0.0411 |
| MP4 → French output video | **Appropriate refusal:** `UNSUPPORTED_LANGUAGE`, zero model/tool calls, no media artifacts; corrected generic preflight | 0.00 s reported | ₹0.00 |
| Independent MCP Inspector OCR | Tool-level success on worksheet, one page, preserved table and expression | — | Not separately reported |

The reports and JSONL traces must be made accessible to reviewers separately: **public evidence links are not yet supplied**. Raw private `.env`, cache files and generated media are intentionally not tracked in git. See [live validation details](docs/LIVE_VALIDATION_RESULTS.md), [evidence upload checklist](docs/DEMO_EVIDENCE_CHECKLIST.md), and [engineering log](docs/ISSUES_AND_FIXES.md).

### Demo recording

[Watch the 5-minute project demonstration](https://drive.google.com/file/d/1gPx73aZyV-6eOShBAY60Ebix0tvE3c6M/view?usp=sharing)

The recording covers:
- Project architecture and the autonomous agent workflow
- Independent MCP server and tool discovery
- Agent implementation and shared tool design
- Results from the six demonstration scenarios
- Output verification, failure handling and limitations

The demonstrated runs use actual Bodhan model outputs.
The Tamil chapter demo is reported as partial because OCR
succeeded on only 5 of 8 pages.



## Submission checklist

- [x] Runnable agent command and independent stdio server
- [x] Bodhan-only model capabilities, typed schemas, budgets and retries
- [x] Original 48 offline tests passed; expanded preflight suite must be rerun in submission clone
- [x] Successful live Bengali speech and Hindi subtitles tasks
- [x] Independent MCP Inspector discovery and selected tool execution
- [x] Negative unsupported-French test with no fabricated output
- [x] Cookbook single-recipe validator reported zero blocking errors locally
- [ ] Re-run expanded pytest suite and Cookbook checks in final submission checkout (`make check` has two documented upstream/native-Windows issues)
- [x] Execute all six example task scenarios and disclose Tamil/Marathi limitations
- [ ] Provide publicly accessible, rights-cleared example outputs/traces
- [ ] Record a public demo video of at most five minutes and link it here and in the PR
- [ ] Open PR to upstream, then email PR URL **and final commit SHA** by 23 Nov 2026

### AI assistance

This project was written with substantial assistance from an OpenAI AI coding assistant for architecture, implementation, mocks, tests and documentation. No live model results are fabricated.

### References

- [Bodhan cookbook rules and models](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/blob/main/scripts/bodhan_api_rules.json)
- [Bodhan API reference](https://console.bodhan.ai/api-docs/)
- [Cookbook contribution guidelines](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/blob/main/CONTRIBUTING.md)
