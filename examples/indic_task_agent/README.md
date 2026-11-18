# Indic Task Agent: Bodhan tool server and task agent

> An MCP server that exposes Bodhan's Indic speech, translation, speech synthesis and OCR models as tools, and a task agent that uses them to finish multi-step language tasks.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak, indic-ocr
**Languages shown:** Hindi, Tamil, Bengali, Marathi (see the tool `list_languages` for the full lists)
**Format:** Python package (MCP stdio server, command-line agent)

> **Status: work in progress.** The MCP tool server, the task agent and their tests are done, and the tools have run against the live API. Runs with a real LLM and the demo task set are next.

## What it does

The MCP server has seven tools: `list_languages`, `inspect_media`, `detect_language`, `transcribe_audio`, `translate_text`, `speak_text` and `ocr_document`. Files move between tools by path, never as base64. Each tool validates its input, handles the model limits (30-second audio windows, sentence-sized speech, PDF pages rendered to images), and returns a structured error when it cannot help.

## Setup

```bash
cd examples/indic_task_agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt   # or: pip install -e .
cp .env.example .env   # then paste your Bodhan key from https://console.bodhan.ai
```

System dependency: `ffmpeg` and `ffprobe` (for example `brew install ffmpeg` or `apt install ffmpeg`).

## Run

Run the task agent (needs `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` in `.env`, any OpenAI-compatible endpoint with tool calling):

```bash
PYTHONPATH=src python -m bodhan_agent \
  --task "Read this paragraph aloud in Bengali with a female voice" \
  --files path/to/inputs/ \
  --out ./out \
  [--max-steps 30] [--max-cost-inr 20]
```

Each run writes `out/result.md`, `out/artifacts/`, `out/trace.jsonl` and `out/report.json`.
Exit code: 0 when the task is done or honestly reported impossible, 2 when it stopped early (budget, step limit, partial), 1 when it failed.

Run the MCP server on its own, over stdio (works with any MCP client):

```bash
PYTHONPATH=src python -m bodhan_mcp
```

Run the tests (the audio tests need ffmpeg; they skip without it):

```bash
PYTHONPATH=src:tests python -m pytest tests -q
```

## How it works

1. `inspect_media` tells the caller what a file is (audio, video, PDF, image, text).
2. `transcribe_audio` converts to 16 kHz mono WAV, cuts the audio at silences into windows of at most 28 seconds, and sends each window to `indic-transcribe`.
3. `translate_text` splits Markdown by structure. Tables, headings, lists, code and LaTeX keep their shape. Only the words are translated. Numbers-only cells are copied, not sent. Checks cover structure, numbers and sentence count.
4. `speak_text` splits the text into sentences, calls `indic-speak` for each, joins the WAVs with short pauses, then transcribes the result back and compares it with the input. Output is labelled as AI-generated.
5. `ocr_document` renders each PDF page to PNG, runs `indic-ocr` once per page, and flags pages that were cut off, came back empty, or have low-confidence layout blocks.

Every paid call goes through one client with retries and backoff on 429 and 5xx, a concurrency limit, a disk cache keyed by the exact input (the same input is never sent twice), and a rupee budget that stops before the next call would overspend.

## Known API issues

- `POST /translate` returns `404 {"detail":"Not Found"}` on the hosted gateway (checked 17 and 18 Nov 2026, reported to Bodhan).
  The client tries it first, then switches to the documented second transport, `POST /v1/chat/completions` with model `indic-translate`,
  for the rest of the run. On that route only `script: native` is supported here; `roman` and `codemix` return a clear error.

## Limitations

- Not yet tested against the live API. Unit tests use mocked HTTP responses from the API reference.
- Translation is only offered for the languages in the Bodhan rules. `bhb` and `bho` are not supported for translation.
- Speech back-check reads digits as digits. A number read out as words can lower the similarity score.
- Video is accepted for transcription (audio track only). Translating or dubbing video is not built here.
