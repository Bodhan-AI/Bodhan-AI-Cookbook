# Indic task agent

> A bounded command-line agent that plans Indic media tasks and runs them through Bodhan models.

**Bodhan models:** `indic-ocr`, `indic-transcribe`, `indic-translate`, `indic-speak`
**Languages shown:** Hindi, Tamil, Bengali, English (language support depends on the selected model)
**Format:** Python CLI and stdio MCP server

## What it does

Give the CLI a plain-language request and optionally a local image or audio file. A configurable OpenAI-compatible planner chooses among exactly four Bodhan tools. The runner applies a step limit, retries transient failures, caches repeated identical Bodhan tool requests, verifies basic outputs, and saves a Markdown result, JSON report, and JSONL trace. The MCP server exposes the same four Bodhan tools independently.

## Setup

From this directory, create and activate a virtual environment, then install the pinned packages:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally. Bodhan's console currently issues model-specific keys, so add the key for each model you plan to use (`BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, and `BODHAN_OCR_KEY`). Set `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY` for a separate OpenAI-compatible model that supports tool calls. Never commit `.env` or paste credentials into chat or traces. No keys are bundled or supplied by this recipe. For older Cookbook setups, `BODHAN_API_KEY` is accepted as a fallback for all four models.

## Run

Run a task (the path is an example; use your own local media):

```powershell
python app.py "Read this page and translate it into Hindi" --input .\sample_data\page.png
python app.py "Transcribe this recording" --input .\sample_data\notice.wav --max-steps 4 --max-budget-inr 5
python app.py "Translate this sentence into French" --dry-run
python -m indic_task_agent.mcp_server
```

Generated files go in `outputs/`: `result.md`, `report.json`, and `trace.jsonl`. Configure these through `--output-dir` if needed. The trace records tool names, step numbers, timestamps, and redacted file references; the report retains local media references to make the run reproducible.

## How it works

1. The planner receives the request and local media path, then selects tools from a closed four-tool allowlist.
2. Bodhan calls go to `https://api.bodhan.ai`; OCR uses `indic-ocr`, ASR uses `indic-transcribe`, translation uses `indic-translate`, and speech uses `indic-speak`.
3. The executor checks each result, retries bounded transient failures, and reuses cached results for duplicate identical tool requests.
4. The run stops when the configured tool-step limit is reached. If Bodhan returns a `cost_inr` field, known spend is tracked against the cap. When the API response does not include cost, the report marks that call unmetered rather than inventing a price.
5. Outputs are written as `result.md`, `report.json`, and `trace.jsonl`.

## Six demo tasks

These prompts are included as manual/API demos. Five require credentials and supported sample media; the French case is rejected locally before a Bodhan translation call.

1. `Transcribe this Hindi announcement` with an audio file.
2. `Translate this Hindi text into English` with source text.
3. `Read the text and table from this page` with an image.
4. `Read this page, translate the extracted text into Hindi, and speak it` with an image and a Bodhan voice selection.
5. `Transcribe this recording, translate it into Tamil, and save the spoken result` with audio and a Bodhan voice selection.
6. `Translate this sentence into French` — expected outcome: unsupported, with no Bodhan call.

## Testing

```powershell
python -m unittest discover -s tests -v
python app.py "Translate this sentence into French" --dry-run
cd ..\..
python scripts/validate_recipe.py examples/indic_task_agent
make check
```

The unit tests use mocks and make no live API calls. A real API run-through is required by the Cookbook contribution guide before a pull request; it cannot be claimed without valid Bodhan and planner credentials.

## Limitations

- Bodhan's documented `indic-translate` supports Indian languages and English; French is deliberately treated as unsupported.
- OCR expects an image file. Audio requests should stay near 30 seconds, per Cookbook guidance. This sample does not chunk long media automatically.
- Bodhan keys are scoped by model in the current console. The planner uses a separate key and endpoint of your choice.
- The rupee cap can stop further calls after Bodhan reports `cost_inr`; when cost is absent, the runner marks the call unmetered and relies on the maximum step count. A single in-flight call may cross the cap before its charge is reported. Planner-provider charges are outside the Bodhan budget report.
- Retries are bounded; 429 and server errors use short exponential backoff. The agent does not retry indefinitely.
- The recipe has not been run against live Bodhan services because no credentials were present in this workspace.
