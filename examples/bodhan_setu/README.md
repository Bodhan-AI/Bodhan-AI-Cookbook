# Bodhan Setu: MCP server and task agent

Bodhan Setu implements the Bodhan tool server and an unattended task agent. The MCP server exposes typed tools for Bodhan speech-to-text, translation, transliteration, speech synthesis, and OCR. The task agent plans with an OpenAI-compatible LLM, executes through MCP, enforces step and rupee budgets, records a trace, and writes a per-run report.

## Setup

```bash
cd examples/bodhan_setu
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[test]"
cp .env.example .env
```

Fill the five Bodhan keys in `.env`. The server reads them from the environment and never accepts keys as tool arguments.

Redis is used for local cache metadata. When the MCP starts, it connects to Redis or starts a local `redis-server` automatically if the configured local service is unavailable. Redis data is stored under `~/.cache/bodhan-setu/redis`. Generated WAV and Markdown artifacts remain on disk. Set `BODHAN_CACHE_BACKEND=memory` to disable Redis, or `BODHAN_REDIS_AUTOSTART=false` to require an already-running Redis service. Docker Compose remains available if you prefer a container: `docker compose up -d redis`.

File-producing tools also return a temporary reference such as `setu:=<file_id>`. Pass that exact reference to a later file input parameter instead of reopening the path or copying bytes; text tools accept it in their `text` parameter to read UTF-8 text artifacts. Redis stores the reference metadata with `BODHAN_ARTIFACT_TTL_SECONDS` (default 3600); temporary copies live under `~/.cache/bodhan-setu/artifacts` or `BODHAN_ARTIFACT_DIR`. Expired references are rejected. Disk cleanup runs opportunistically when another artifact is published. Keep `output_file` as a normal filesystem destination for deliverables.

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
  --max-cost-inr 20
```

Each run creates a unique directory under `--out` containing `result.md`, `report.json`, `trace.jsonl`, and an `artifacts/` directory. The CLI prints the run directory and exits nonzero when the run is blocked, failed, or partial. The agent launches the local stdio MCP server itself, reads its language, voice, and pricing resources, and exposes the registered MCP tools to the LLM. Before any paid tool call, it requests a dry-run estimate and blocks the call if the estimate would exceed the remaining Bodhan budget. `translate_video` may require a second estimate with duration-based output-size assumptions; that assumption is recorded. The configured LLM endpoint cost is not included in the Bodhan rupee budget.

Inputs are restricted to supplied paths and outputs from the current run. File IDs remain available for chaining without putting binary media in the LLM context; generated artifacts are downloaded into the run's `artifacts/` directory. Trace records include plans, tool arguments/results, latency, reported costs, artifact downloads, and verification outcomes. Large text values are summarized in the trace with a preview, length, and hash.

## Tools

- `convert_pdf_to_images`: use when page image files are needed. For text extraction, call `ocr_document` directly.
- `chunk_audio`: standalone local splitter when separate audio chunks are needed. Transcription tools chunk long audio internally; `smart_chunking=True` uses local frame-energy VAD near silence.
- `transcribe_audio`: transcribes a local PCM WAV. Omit language if unknown to ask Bodhan to auto-detect; when known, pass its code because this can improve non-English transcription. The response has no detected-language/confidence field. It chunks long audio internally and merges transcript text in order.
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

Language inference differs by model: Indic-Transcribe attempts vendor auto-detection when `language` is omitted, but returns no detected-language code or confidence; specify a known language code when possible, especially for non-English speech. Indic-Translate auto-detects the source language, while requiring an explicit target code. Indic-OCR infers the page script and emits text in that script, but does not return a separate language label. Indic-Transliterate and Indic-Speak require a language code. For OCR, infer the likely language from extracted text only when clear and record uncertainty otherwise; do not present an agent inference as a vendor detection.

Automatic transcription duration inspection and splitting currently support uncompressed PCM WAV. Other audio formats need conversion to PCM WAV before calling the tool. If a chunk fails, the tool reports how many completed and an estimate of cost already incurred; it does not cache incomplete results.

`chunk_audio` is a standalone local utility and makes no Bodhan calls. Fixed-duration splitting is the default. Smart mode requires 16-bit PCM WAV, groups speech using frame energy, pads speech edges slightly, and respects `max_chunk_seconds` (up to 25 seconds). This lightweight VAD can miss very quiet speech or classify loud background noise as speech.

Video and audio conversion tools require `ffmpeg` and `ffprobe` on PATH (Homebrew FFmpeg is detected on macOS). Extraction and track replacement are local and free. For an agent-controlled video chain, call `extract_video_audio`, then `translate_audio`, then `replace_video_audio_track`; for one-call orchestration, call `translate_video`. Both high-level translation tools use the DSP voice matcher when available. If the library is missing, invalid, incomplete, or audio features cannot be measured, they silently choose the first catalog voice for the target language and report the fallback. Build or refresh the optional library with `python scripts/build_voice_embeddings.py --dry-run` to inspect the small TTS cost, then `python scripts/build_voice_embeddings.py` to generate the embeddings file. DSP matching uses pitch, loudness, voicing, zero-crossing, and spectral-tilt proxies; it is approximate and does not identify a speaker. Audio speech regions are time-fit to their original VAD intervals; extreme duration changes can sound unnatural. Audio outside detected speech regions remains from the original source. Video muxing preserves the source video duration; replacement audio shorter than the source is padded with silence, and longer audio is trimmed. This does not lip-sync speech to speakers.

## MCP resources and prompt

- `bodhan://voices`: supported Indic-Speak voices grouped by language and voice type.
- `bodhan://languages`: language names and availability by model.
- `bodhan://pricing`: INR pricing used for estimates.
- `cost_first_workflow(task)`: prompt template instructing clients to estimate costs before paid calls, preserve untrusted content as data, and report uncertain estimates and failures honestly.

File inputs accept either filesystem paths or `setu:=<file_id>` references returned by tools. Passing an artifact ID keeps binary media and long text out of the agent's prompt context. Text responses are capped at 4,000 characters and include a truncation flag plus the full artifact's file ID when longer. `get_file` binds a short-lived download server to `127.0.0.1` on an automatically chosen port (`BODHAN_ARTIFACT_HTTP_PORT=0`); it sends attachments with the original filename, and the unguessable file ID acts as the download capability. The link works while the MCP process is running and the artifact TTL is valid. MCP resources such as `bodhan://voices` are read as resources, not through `get_file`.

Model audio cache artifacts are kept separately under `~/.cache/bodhan-setu/model-cache-artifacts` (override with `BODHAN_MODEL_CACHE_ARTIFACT_DIR`) so temporary video-workflow directories cannot invalidate Redis cache entries. `translate_text` treats omitted script and explicit `script="native"` as the same cache operation and trims only leading/trailing text whitespace before model calls and keying.

For PDF workflows, call `convert_pdf_to_images` when the caller needs image files. Use `ocr_document` when the goal is text extraction; it renders pages internally and returns page-ordered Markdown.

The voice and language resources are read from the repository's `scripts/bodhan_api_rules.json`; pricing is defined in the server alongside the estimate logic. Read `bodhan://languages` for codes and per-model availability, `bodhan://voices` before selecting a TTS voice, and `bodhan://pricing` when planning costs.

The synthetic evaluation fixtures can be regenerated locally on macOS with `swift evals/generate_document_fixtures.swift "$PWD/evals"`, `python evals/generate_audio_fixtures.py`, then `python evals/refresh_media_manifest.py`. The audio generator uses built-in macOS speech voices and FFmpeg; it makes no network or Bodhan calls.

This first phase keeps each Bodhan model as one explicit tool, while handling long audio and speech chunking internally. Caching is provided for repeat calls and artifact reuse.

Every MCP tool publishes a typed `outputSchema` as well as typed input parameters. Tool result schemas cover normal, dry-run, and cache-hit responses.

## Design notes and limits

The agent uses the LLM for planning, tool selection, and English reporting only. Speech recognition, OCR, Indic translation/transliteration, and speech synthesis are delegated to Bodhan tools. It treats transcript and OCR contents as untrusted data, retries transient LLM errors, records tool errors, and asks the LLM to summarize honestly if the step budget is reached. Translation and synthesized audio receive advisory checks when the needed source/result is available. These checks are not native-speaker judgments; the report calls out uncertainty. Cost tracking is limited to what the MCP tools report, and the LLM provider cost is not included. Tool workflows remain sequential so each step can use the prior result and budget checks.

## Tests

```bash
pytest -q
```

The test suite includes a mocked end-to-end agent run through a real local MCP subprocess; it does not call paid Bodhan APIs or the configured LLM service.
