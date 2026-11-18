# Bodhan Tool Server and Task Agent

The standalone stdio MCP server exposes short text translation, audio transcription, text-to-speech, and document OCR. The translation tool calls Indic-Translate on Bodhan's hosted `/v1/chat/completions` endpoint and returns translated text and token usage.

## Setup

From this recipe directory, using Python 3.12:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
[ -e .env ] || cp .env.example .env
```

Add the translation key to `BODHAN_TRANSLATE_KEY` in `.env`. The server loads this file from the recipe directory. An existing environment variable takes precedence. Git ignores `.env`.

For transcription, add `BODHAN_TRANSCRIBE_KEY` to `.env` and install FFmpeg on PATH. For text-to-speech, add `BODHAN_SPEAK_KEY`. For OCR, add `BODHAN_OCR_KEY`. PDF input requires Poppler's `pdfinfo` and `pdftoppm` on PATH.

## Run

Start the server using `.venv/bin/python -m bodhan_mcp`. With Node 22.18 or later, let MCP Inspector start the process on your behalf:

```sh
npx -y @modelcontextprotocol/inspector@0.21.1 --cli .venv/bin/python -m bodhan_mcp --method tools/list
npx -y @modelcontextprotocol/inspector@0.21.1 --cli .venv/bin/python -m bodhan_mcp --method tools/call --tool-name translate_text --tool-arg 'text=The class starts at 9 tomorrow.' --tool-arg target_language=kn
```

The second command calls the hosted API. Check the Kannada output for the class, 9, and tomorrow. Use `target_language=fr` to see validation reject an unsupported language before sending a request. Inspect `isError` in the tool result.

To transcribe the Cookbook's Hindi sample with Inspector:

```sh
npx -y @modelcontextprotocol/inspector@0.21.1 --cli .venv/bin/python -m bodhan_mcp --method tools/call --tool-name transcribe_audio --tool-arg audio_path=../../sample_data/sample_hindi.wav --tool-arg language=hi
```

The server decodes the file into 16 kHz mono WAV and sends audio to Bodhan in windows of at most 30 seconds. The response includes the joined transcript and per-window text and offsets. Offsets give window boundaries rather than word timings. Exact cuts can split words, and a failed window returns an error identifying its bounds.

To generate a WAV file with Indic-Speak:

```sh
npx -y @modelcontextprotocol/inspector@0.21.1 --cli .venv/bin/python -m bodhan_mcp --method tools/call --tool-name synthesize_speech --tool-arg 'text=Class starts at nine tomorrow. Please bring your notebook.' --tool-arg language=en --tool-arg voice=Arun --tool-arg output_path=outputs/class.wav
```

The tool splits at sentence boundaries and wraps longer sentences at word boundaries. Its 200-character cap per request is a local policy based on [Bodhan's short-input guidance](https://console.bodhan.ai/api-docs/#text-to-speech-api). The response gives the file path, duration, chunk count, and an AI-generated label. Choose a new path each time.

To extract text from a scanned PDF, replace the example path with your file:

```sh
npx -y @modelcontextprotocol/inspector@0.21.1 --cli .venv/bin/python -m bodhan_mcp --method tools/call --tool-name extract_document_text --tool-arg document_path=/path/to/scan.pdf
```

The tool also accepts a PNG or JPEG as a single page. It checks the PDF page count, renders each page, and returns numbered Markdown entries in order. The MCP call takes a file path. Only the server's Bodhan HTTP request encodes the image. Bodhan receives one page per request, with a 4,096-token budget per layout block. If a page fails, the tool reports which page failed.

Run the offline checks with `.venv/bin/python -m unittest discover -s tests -v`. They use an HTTP test transport to check the request and response mapping, truncation, rate-limit behavior, the exact 30-second audio boundary, TTS chunking, and OCR page counts and ordering.

The translation wrapper sends one short passage per call. Long translation chunking and the task agent are later increments.

[Translation API reference](https://console.bodhan.ai/api-docs/#translation-api)

## Run the task agent

Set `LLM_BASE_URL` (the OpenAI-compatible base, usually ending in `/v1`), `LLM_MODEL`, and `LLM_API_KEY` in the local `.env`. The Bodhan model keys remain separate.

```sh
.venv/bin/python -m bodhan_agent \
  --task "Read this scanned handout aloud in Kannada" \
  --files /path/to/scan.pdf \
  --out outputs/handout-run \
  --max-steps 12 --max-cost-inr 2
```

Choose a new or empty output directory. The agent writes `result.md`, `report.json`, `trace.jsonl` and `artifacts/`. The planner selects typed actions referencing known input and text IDs. The executor imports the same Bodhan operations exposed over stdio. It sends media directly to Bodhan and uses text previews for planning. The translation reviewer sees the full source and result for the supported short passages.

The cost limit covers Bodhan charges at the assignment rates. OCR and speech requests reserve cost before submission, as do each STT window and each translation request. Translation reserves for up to 2,048 output tokens and settles against usage if it is available. Uncertain charges stay reserved. Planner charges are separate. The agent checks translations for coverage through the planner and compares Bodhan's speech read-back to the source. The read-back comparison is a heuristic and can flag ASR variation as a speech mismatch.

The trace records translation review and speech read-back as verification events linked to the planner step that triggered them. Each event has its own latency and Bodhan request costs. Tool events exclude verification latency and costs, so those amounts are counted once. The step count reflects planner decisions and includes the finish decision.

This increment supports short text, transcription, page OCR and WAV output. Long-document translation, English summarization, subtitles, retry and resumable partial work are upcoming. The trace records completed provider requests if a tool aborts mid-call. A page or chunk completed inside a failed call is not yet recoverable as an artifact.
