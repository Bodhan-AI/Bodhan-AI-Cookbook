# Bodhan Setu: MCP server and task agent

Bodhan Setu implements the Bodhan tool server and an unattended task agent. The MCP server exposes typed tools for Bodhan speech-to-text, translation, transliteration, speech synthesis, and OCR. The task agent plans with an OpenAI-compatible LLM, executes through MCP, enforces step and rupee budgets, records a trace, and writes a per-run report.

## Setup

```bash
cd examples/bodhan_setu
./setup.sh
```

The setup script creates a pinned virtual environment and a local `.env` placeholder. Add your per-model Bodhan keys and OpenAI-compatible LLM settings to `.env` before running the agent. Python 3.10+ is required. Install FFmpeg (`ffmpeg` and `ffprobe`) for audio/video conversion; Redis is started locally by the server when available, and Docker Compose can provide Redis with `docker compose up -d redis`.

To use the container instead, build the MCP-server image and launch it over stdio:

```bash
docker build -t bodhan-setu .
docker run --rm -i --env-file .env bodhan-setu
```

Evaluation media is hosted in the public [Hugging Face dataset](https://huggingface.co/datasets/1rsh/bodhan-setu-mcp-evals), not stored in the Cookbook repository. The evaluation runner verifies and downloads the synthetic fixtures automatically. To fetch them manually:

```bash
python evals/prepare_media.py
```

Fill the five Bodhan keys in `.env`. The server reads them from the environment and never accepts keys as tool arguments.

Redis is used for local cache metadata. When the MCP starts, it connects to Redis or starts a local `redis-server` automatically if the configured local service is unavailable. Redis data is stored under `~/.cache/bodhan-setu/redis`. Generated WAV and Markdown artifacts remain on disk. Set `BODHAN_CACHE_BACKEND=memory` to disable Redis, or `BODHAN_REDIS_AUTOSTART=false` to require an already-running Redis service. Docker Compose remains available if you prefer a separate Redis container: `docker compose up -d redis`.

File-producing tools also return a reference such as `setu:=<file_id>`. Pass that exact reference to a later file input parameter instead of reopening the path or copying bytes; text tools accept it in their `text` parameter to read UTF-8 text artifacts. References remain valid while their files are in `~/.cache/bodhan-setu/artifacts` or `BODHAN_ARTIFACT_DIR`. Artifacts do not expire by time; each user retains up to `BODHAN_ARTIFACT_MAX_FILES` files (default 20), and the oldest is evicted when the limit is exceeded. `output_file` remains a normal filesystem destination for deliverables.

## Run

From this directory:

```bash
python -m bodhan_mcp
```

By default, the server uses MCP stdio transport. An MCP client should launch this command and communicate over stdin/stdout. It does not print logs to stdout because stdout is reserved for MCP messages.

For clients that connect over a network, run Streamable HTTP with a bearer token:

```bash
export BODHAN_MCP_HTTP_TOKEN='replace-with-a-long-random-secret'
python -m bodhan_mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. Configure the client to send `Authorization: Bearer <token>` on MCP requests. The token is required for this transport; the server exits at startup if it is missing. To expose the service beyond loopback, bind to an appropriate interface and use HTTPS through a trusted reverse proxy. Stdio remains the default and does not require this token.

The same settings can be supplied with `BODHAN_MCP_TRANSPORT=streamable-http`, `BODHAN_MCP_HOST`, `BODHAN_MCP_PORT`, and `BODHAN_MCP_HTTP_TOKEN` instead of command-line flags.

## Task agent

Configure an OpenAI-compatible tool-calling endpoint in `.env`:

```dotenv
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=your-tool-calling-model
LLM_API_KEY=your-key
```

Run one task with one or more supplied files or directories:

```bash
python -m bodhan_agent \
  --task "Transcribe the recording and translate it into Marathi" \
  --files ./recording.wav \
  --out ./outputs \
  --max-steps 30 \
  --max-cost-inr 20 \
  --max-parallel-tools 3
```

Each run creates a unique directory under `--out` containing `result.md`, `report.json`, `trace.jsonl`, and an `artifacts/` directory. The CLI prints the run directory and exits nonzero when the run is blocked, failed, or partial. The agent launches the local stdio MCP server itself, reads its language, voice, and pricing resources, and exposes the registered MCP tools to the LLM. Independent tool calls returned together by the LLM run concurrently up to `--max-parallel-tools`; dependent tool calls happen after the prior results are returned. All calls share the run's step and cost budgets, and duplicate paid calls in one batch are reused. Before any paid tool call, the agent requests a dry-run estimate and reserves that cost against concurrent calls. `translate_video` and `translate_audio` may require a second estimate with duration-based output-size assumptions; that assumption is recorded. The configured LLM endpoint cost is not included in the Bodhan rupee budget.

The agent writes an atomic `checkpoint.json` after planning and each tool batch. Resume an interrupted run with `python -m bodhan_agent --resume ./outputs/<run-id>`; pass `--max-steps` or `--max-cost-inr` to raise the original limits. A run resumes from saved conversation/tool results and does not replay completed calls. If the process stopped while a tool call was in flight, the agent blocks resume and marks the call cost/result uncertain rather than risking duplicate paid work. Keep the same `LLM_MODEL` and `LLM_BASE_URL`; the API key may be refreshed.

Start the local chat workspace with:

```bash
PYTHONPATH=src python -m bodhan_agent.web --out ./outputs --port 7860
```

Open `http://127.0.0.1:7860`. The workspace has chat threads and a shared file drawer. Uploaded and generated artifacts are stored in the configured `BODHAN_ARTIFACT_DIR` (default `~/.cache/bodhan-setu/artifacts`) and tagged with the local user label, so threads under the same label can reuse them. The drawer offers downloads and attachments; the user label organizes local files but is not an authentication boundary. Files remain available until the per-user cache limit (20 by default) is exceeded, then the oldest files are removed. If a file remains in the cache folder but its Redis metadata expired under an older version, the workspace restores its reference from the thread attachment record.

The composer defaults to **Estimate only**. It forces all Bodhan model tools to `dry_run=True` and blocks local conversion/file-writing tools. The planner LLM still receives requests and may incur its own charges. Choose **Run task** to allow paid Bodhan operations, which remain behind the agent's cost estimates and budget. The header shows `LLM_MODEL` when configured; set `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY` in `.env`. The web server binds to loopback only and supports up to four concurrent runs.

See [task-agent architecture and evaluation](agent/README.md) for run isolation, concurrency semantics, benchmark design, current coverage, and next steps.

## How it works

The task agent uses the LLM for planning and tool selection, then runs tools through the local stdio MCP server. Python enforces file references, schemas, cost and step budgets, retries, checkpoints, and output verification. The [architecture diagram, agent-flow chart, and design notes](agent/README.md) explain those boundaries.

## Off-the-shelf MCP client

The server was smoke-tested with **GitHub Copilot CLI** using the `bodhan-setu` MCP server and an explicitly allowed dry-run transcription. No paid Bodhan request was made. After installing the recipe in the active virtual environment, run this from the recipe directory to register the stdio server:

```bash
copilot mcp add bodhan-setu \\
  --env "PYTHONPATH=$PWD/src" \\
  -- "$VIRTUAL_ENV/bin/python" -m bodhan_mcp
copilot mcp get bodhan-setu
```

For a permission-approved, no-Bodhan-spend smoke call:

```bash
copilot -p "Use bodhan-setu transcribe_audio on evals/media/audio/clip_ambiguous_01.wav with language hi and dry_run=true. Do not run a paid operation." \\
  --allow-tool='bodhan-setu' --silent
```

Copilot still makes its own planner request; configure its model/account costs separately. See [the six demo runs and their traces](demos/README.md) for task-agent behavior and [the PR description draft](PR_DESCRIPTION.md) for submission details.

Inputs are restricted to supplied paths and outputs from the current run. File IDs remain available for chaining without putting binary media in the LLM context; generated artifacts are downloaded into the run's `artifacts/` directory. Trace records include plans, tool arguments/results, latency, reported costs, artifact downloads, and verification outcomes. Large text values are summarized in the trace with a preview, length, and hash.

## Tools

### Tool granularity

The server keeps the four core model operations explicit: `transcribe_audio`, `translate_text`, `synthesize_speech`, and `ocr_document`. This lets a third-party MCP client inspect costs, reuse an intermediate transcript or OCR file, and choose its own sequence for tasks that need several models. Model limits stay inside those tools: transcription chunks audio, translation splits long text, synthesis splits long speech text, and OCR iterates PDF pages.

Composite tools are limited to workflows where local media handling and timing make a manual chain error-prone. `translate_audio` coordinates VAD, transcription, translation, speech generation, and splicing while preserving non-speech audio; `translate_video` reuses that workflow and replaces the video audio track while preserving video duration. Use them when the requested deliverable is translated audio/video. Use the explicit model tools and local helpers when the user needs intermediate transcripts, extracted audio, page images, or more control. `extract_video_audio`, `replace_video_audio_track`, `transcribe_video`, `convert_pdf_to_images`, `chunk_audio`, and `get_file` are focused media or artifact helpers; they do not hide extra model calls except where their descriptions say so.

- `convert_pdf_to_images`: use when page image files are needed. For text extraction, call `ocr_document` directly.
- `chunk_audio`: standalone local splitter when separate audio chunks are needed. Transcription tools chunk long audio internally; `smart_chunking=True` uses local frame-energy VAD near silence.
- `transcribe_audio`: transcribes a local audio file or `setu:=<file_id>`. WAV, MP3, M4A, AAC, FLAC, OGG, OPUS, WMA and other FFmpeg-readable formats are normalized internally to mono 16 kHz PCM WAV. Omit language if unknown to ask Bodhan to auto-detect; when known, pass its code because this can improve non-English transcription. The response has no detected-language/confidence field. It chunks long audio internally and merges transcript text in order.
- `translate_audio`: one-call VAD-aware audio translation. It normalizes common audio inputs to mono PCM WAV, detects speech regions locally, then transcribes, translates, and synthesizes each region. Generated speech is time-fit to its detected interval; original samples outside speech regions are restored, and output duration matches the normalized source. Omit `source_language` for vendor auto-detection. Select `voice` explicitly or omit it to use the closest available voice from the DSP library. Dry runs detect speech regions without model calls; pass output-token and speech-character estimates for a complete cost estimate.
- `transcribe_video`: use directly for a video transcript; audio extraction and chunking happen internally.
- `translate_video`: one-call video dubbing workflow. It uses the same VAD-aware audio translation pipeline as `translate_audio`, preserving non-speech source audio and keeping the muxed video duration at the source duration. `source_language` may be omitted; the API does not report the detected code or confidence. Its dry run detects speech regions and estimates transcription automatically; provide estimated translated output tokens and speech characters for a complete estimate.
- `extract_video_audio`: local extraction for users who need the audio file or for a manually orchestrated video chain.
- `replace_video_audio_track`: final step of a manual dubbing chain; writes a new video, preserves visuals, and keeps the source video duration. Short replacement audio is padded with silence; long replacement audio is trimmed. It does not lip-sync.
- `translate_text`: changes meaning into another language. Bodhan infers the source language; provide an explicit supported target code such as `mr` for Marathi. Inputs up to 1,000,000 characters are split at paragraph, sentence, then word boundaries into requests of at most 8,000 characters; results are reassembled in order. A failed chunk reports progress and known/estimated cost, and partial translations are not cached.
- `transliterate_text`: changes script while preserving language. It requires the source language code. `script` is the output script: `roman` means Latin letters; `native` means the language's native script.
- `synthesize_speech`: writes a WAV; language is required because Bodhan does not infer it. Provide a supported language code and voice listed in `bodhan://voices`.
- `ocr_document`: extracts text directly from a local image or PDF. It infers the page script, but does not return a separate language detection result. OCRs every PDF page in order and writes page-headed Markdown.
- `get_file`: accepts any path or `setu:=<file_id>` and returns a localhost download URL. UTF-8 text also returns a bounded slice; binary contents stay out of the MCP context.

Paid model tools accept `dry_run=True`; call the operation itself in dry-run mode first to get an estimate without making a paid API request. Transcription reads duration from uncompressed PCM WAV metadata and internally splits recordings into 25-second requests, then joins transcripts in order. Speech synthesis splits long text at sentence and word boundaries into conservative ~300-character requests (the API reference recommends a sentence or two/about 30 seconds), then merges returned WAV audio into one file. Both return planned chunk counts during dry runs. Translation and transliteration accept `estimated_output_tokens` and otherwise use input character count as a rough proxy. OCR estimates one image per call or one image per PDF page. Indic-Speak cost uses input characters. Estimates use the pricing table supplied for this task and may differ from final billing if actual usage differs.

The server retries HTTP 429 responses and connection-establishment timeouts up to `BODHAN_MAX_RETRIES` (default 2), honoring a bounded `Retry-After` when present. It does not retry read timeouts or connection resets because Bodhan may already have processed and billed those requests. A shared semaphore limits simultaneous outbound requests to `BODHAN_MAX_CONCURRENCY` (default 4); internal tool calls share this limit.

Language inference differs by model: Indic-Transcribe attempts vendor auto-detection when `language` is omitted, but returns no detected-language code or confidence; specify a known language code when possible, especially for non-English speech. Indic-Translate auto-detects the source language, while requiring an explicit target code. Indic-OCR infers the page script and emits text in that script, but does not return a separate language label. Indic-Transliterate and Indic-Speak require a language code. For OCR, infer the likely language from extracted text only when clear and record uncertainty otherwise; do not present an agent inference as a vendor detection.

Transcription accepts FFmpeg-readable audio formats and converts non-PCM-WAV input to mono 16 kHz PCM WAV inside the tool before duration inspection and splitting. Install FFmpeg for this normalization. Dry runs may perform this local conversion, but never call Bodhan. If a chunk fails, the tool reports how many completed and an estimate of cost already incurred; it does not cache incomplete results.

`chunk_audio` is a standalone local utility and makes no Bodhan calls. Fixed-duration splitting is the default. Smart mode requires 16-bit PCM WAV, groups speech using frame energy, pads speech edges slightly, and respects `max_chunk_seconds` (up to 25 seconds). This lightweight VAD can miss very quiet speech or classify loud background noise as speech.

Video and audio conversion tools require `ffmpeg` and `ffprobe` on PATH (Homebrew FFmpeg is detected on macOS). Extraction and track replacement are local and free. For an agent-controlled video chain, call `extract_video_audio`, then `translate_audio`, then `replace_video_audio_track`; for one-call orchestration, call `translate_video`. Both high-level translation tools use the DSP voice matcher when available. If the library is missing, invalid, incomplete, or audio features cannot be measured, they silently choose the first catalog voice for the target language and report the fallback. Build or refresh the optional library with `python scripts/build_voice_embeddings.py --dry-run` to inspect the small TTS cost, then `python scripts/build_voice_embeddings.py` to generate the embeddings file. DSP matching uses pitch, loudness, voicing, zero-crossing, and spectral-tilt proxies; it is approximate and does not identify a speaker. Audio speech regions are time-fit to their original VAD intervals; extreme duration changes can sound unnatural. Audio outside detected speech regions remains from the original source. Video muxing preserves the source video duration; replacement audio shorter than the source is padded with silence, and longer audio is trimmed. This does not lip-sync speech to speakers.

## MCP resources and prompt

- `bodhan://voices`: supported Indic-Speak voices grouped by language and voice type.
- `bodhan://languages`: language names and availability by model.
- `bodhan://pricing`: INR pricing used for estimates.
- `cost_first_workflow(task)`: prompt template instructing clients to estimate costs before paid calls, preserve untrusted content as data, and report uncertain estimates and failures honestly.

File inputs accept either filesystem paths or `setu:=<file_id>` references returned by tools. Passing an artifact ID keeps binary media and long text out of the agent's prompt context. Text responses are capped at 4,000 characters and include a truncation flag plus the full artifact's file ID when longer. `get_file` binds a download server to `127.0.0.1` on an automatically chosen port (`BODHAN_ARTIFACT_HTTP_PORT=0`); it sends attachments with the original filename, and the unguessable file ID acts as the download capability. The link works while the MCP process is running and the artifact remains in the bounded cache. MCP resources such as `bodhan://voices` are read as resources, not through `get_file`.

Model outputs are cached by semantic request payload: transcription uses input bytes and language, translation uses normalized text/target/script, transliteration uses text/language/script, synthesis uses text/language/voice, and OCR uses input bytes/render settings. Output filenames do not affect cache identity. Dry runs for exact cached requests estimate ₹0. Concurrent equivalent calls are single-flighted with Redis locks when Redis is configured, or process-local locks with the memory backend. Composite audio/video translation caches its final media by source bytes and selected target voice. Model audio/video cache artifacts are kept separately under `~/.cache/bodhan-setu/model-cache-artifacts` (override with `BODHAN_MODEL_CACHE_ARTIFACT_DIR`) so temporary work directories cannot invalidate cache entries. `translate_text` treats omitted script and explicit `script="native"` as the same cache operation and trims only leading/trailing text whitespace before model calls and keying.

For PDF workflows, call `convert_pdf_to_images` when the caller needs image files. Use `ocr_document` when the goal is text extraction; it renders pages internally and returns page-ordered Markdown.

The voice and language resources are read from the rules file packaged with `bodhan_mcp`; pricing is defined in the server alongside the estimate logic. Read `bodhan://languages` for codes and per-model availability, `bodhan://voices` before selecting a TTS voice, and `bodhan://pricing` when planning costs.

The synthetic evaluation fixtures can be regenerated locally on macOS with `swift evals/generate_document_fixtures.swift "$PWD/evals"`, `python evals/generate_audio_fixtures.py`, then `python evals/refresh_media_manifest.py`. The audio generator uses built-in macOS speech voices and FFmpeg; it makes no network or Bodhan calls.

This first phase keeps each Bodhan model as one explicit tool, while handling long audio and speech chunking internally. Caching is provided for repeat calls and artifact reuse.

Every tool publishes typed MCP input and output schemas. Return models are validated by the pinned MCP SDK (`mcp==1.29.1`), which also includes structured tool results.

## Design notes and limits

The agent uses the LLM for planning, tool selection, and English reporting only. Speech recognition, OCR, Indic translation/transliteration, and speech synthesis are delegated to Bodhan tools. It treats transcript and OCR contents as untrusted data, retries transient LLM errors, records tool errors, and asks the LLM to summarize honestly if the step budget is reached. Translation and synthesized audio receive advisory checks when the needed source/result is available. These checks are not native-speaker judgments; the report calls out uncertainty. Cost tracking is limited to what the MCP tools report, and the LLM provider cost is not included. Independent calls emitted in one LLM response run concurrently within the configured limit; later dependent calls run after those results return so budget checks can happen at each stage.

## Tests

```bash
pytest -q
```

The test suite includes a mocked end-to-end agent run through a real local MCP subprocess; it does not call paid Bodhan APIs or the configured LLM service.

Run the isolated task-agent evaluation suite with configured LLM/Bodhan environment settings:

```bash
python evals/task_agent/run.py --out ./eval-results --jobs 1
```

Use `--jobs` to run independent cases concurrently, `--case CASE_ID` to select cases, and `--max-parallel-tools` to set per-run MCP concurrency. The runner gives each case separate output and artifact directories and an in-memory cache by default. `--cache-only` uses shared Redis and refuses every Bodhan API request on a cache miss; this is the safe mode for a zero-Bodhan-cost run, though cold-cache cases will be blocked. Its JSON summary reports deterministic task pass rate and tool execution coverage separately; language quality still needs human review. Normal runs can make real Bodhan calls and incur charges, so check each case's cost limit before running the suite.
