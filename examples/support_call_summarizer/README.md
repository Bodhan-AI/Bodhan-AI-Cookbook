# Support Call Summarizer

> Upload a recorded customer-support call in an Indian language and get a timestamped transcript in the original language and in English, ready to export to a CRM as JSON or CSV.

**Bodhan models:** `indic-transcribe` (speech to text), `indic-translate` (translation)
**Optional AI summary and sentiment:** Sarvam [`sarvam-105b`](https://docs.sarvam.ai/api/getting-started/models/sarvam-105b) LLM, with Claude (`claude-opus-5-5`) as the fallback
**Optional speaker labels:** NVIDIA [Nemotron-3-Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization), run locally on an NVIDIA GPU
**Languages shown:** Hindi, plus 22 other Indian languages and English
**Format:** FastAPI backend + React web app + CLI

## What it does

Many support calls in India happen in Hindi, Tamil, Telugu, Marathi, Bengali and other languages, while CRMs and QA reviews mostly work in English. This recipe chains two Bodhan APIs to turn a call recording into a searchable record:

1. You upload a call (WAV, MP3, M4A, AAC, AMR, OGG, FLAC or WebM). You can also choose the language and add a ticket ID and agent name.
2. The backend splits the call at pauses into pieces of at most 28 seconds and transcribes each piece with `indic-transcribe`.
3. Each piece is translated to English with `indic-translate`.
   *Optional:* on a machine with an NVIDIA GPU, a local speaker model first labels who spoke when (`Speaker 1`, `Speaker 2`, …), so you can tell the agent and customer apart. See [Speaker diarization](#speaker-diarization).
4. *Optional:* with a `SARVAM_API_KEY`, the whole transcript goes to the `sarvam-105b` LLM in one request. It returns a summary, the customer's issue, whether it was resolved, action items, topics, and the customer's sentiment (overall, a −1…+1 score, and whether it improved or worsened), and tags each segment positive, neutral or negative. See [AI summary and sentiment](#ai-summary-and-sentiment).
5. You get call details and a timestamped transcript, shown side by side (original | English). You can click a timestamp to play that moment, and export `JSON`, `calls.csv` (one row per call, for CRM import) or `segments.csv` (one row per piece).

A command-line tool gives the same output for back-office use.

The web app follows the Bodhan.AI console design: the same colour tokens, Poppins headings, the Bodhan.AI mark, and light and dark themes. The theme choice is stored under the same `bodhan-theme` key the Bodhan pages use. All design values are in [`web/src/styles/tokens.css`](web/src/styles/tokens.css).

## Setup

You need **Python 3.11+**, **Node.js 20+** and **ffmpeg** (which provides `ffprobe`) on your `PATH`.

```bash
cd examples/support_call_summarizer
cp .env.example .env            # then add your Bodhan key(s) from https://console.bodhan.ai

# backend
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cd ..

# web app
npm install
```

Install ffmpeg with `brew install ffmpeg` (macOS), `sudo apt install ffmpeg` (Debian/Ubuntu) or `winget install Gyan.FFmpeg` (Windows).

**Optional: speaker labels on an NVIDIA GPU.** Inside the same virtual environment:

```bash
cd backend
pip install -r requirements-diarization.txt    # PyTorch (CUDA 12.6 build), Transformers, librosa (~2.5 GB)
```

Then set `DIARIZATION_PROVIDER=nemotron` in `.env`. See [Speaker diarization](#speaker-diarization) for requirements and the Windows notes.

## Run

**Web app (development).** Run each command in its own terminal:

```bash
cd backend && uvicorn app.main:app --reload     # API on http://localhost:8000 (docs at /docs)
npm run dev                                      # web app on http://localhost:5173
```

**Web app (single server).** Build the web app once. The backend then serves it on the same origin:

```bash
npm run build
cd backend && uvicorn app.main:app               # open http://localhost:8000
```

**Command line:**

```bash
cd backend
python cli.py process ../sample_data/sample_support_call_hindi.wav --out ../outputs --language hi --ticket-id TKT-10452
```

This writes `<name>.json`, `<name>_calls.csv` and `<name>_segments.csv` to `outputs/`. The exit status is 0 when the call completed, 2 when it partly completed and 1 when it failed.

**Sample call.** The repo never ships real customer recordings. The following command makes a synthetic two-speaker Hindi support call with Bodhan's `indic-speak` (about 9 API calls; needs `BODHAN_SPEAK_API_KEY`):

```bash
cd backend && python scripts/make_sample_call.py   # -> sample_data/sample_support_call_hindi.wav
```

**Docker** (backend, web app and ffmpeg in one image):

```bash
docker build -t support-call-summarizer .
docker run --env-file .env -p 8000:8000 support-call-summarizer
```

**Tests.** None of these need an API key or internet access. They mock Bodhan.

```bash
cd backend && pytest            # 90 tests: splitter, exporters, client retries, pipeline, API, diarizer selection
npm test                        # web app component tests
RUN_LIVE=1 pytest -m live   # optional: one real end-to-end call with the keys in .env (costs a few paise)
RUN_GPU=1 pytest -m gpu     # optional: runs the real speaker model on the local GPU
```

## How it works

```
React app ──POST /api/calls──▶ FastAPI ── background job per call ─────────────────────────────┐
   │  ◀── call_id (202)                 1. validate type (by content), size, length              │
   │                                    2. ffmpeg → 16 kHz mono WAV                              │
   │──GET /api/calls/{id} (poll 2 s)──▶ 3. split at pauses, ≤ 28 s per piece                     │
   │                                       (optional: speaker model first, then split by turn)    │
   │  ◀── progress / record             4. each piece → indic-transcribe   (original text)       │
   │                                    5. each text  → indic-translate    (English)             │
   │                                    6. optional: transcript → sarvam-105b (summary, sentiment)│
   │──GET …/export?format=…──────────▶  7. build CallRecord; delete audio; keep result 1 hour   │
   └── JSON / CSV download                                                                       ┘
```

- **Why split the audio ourselves?** Bodhan Speech-to-Text accepts at most 30 seconds per request and returns text only, with no timestamps. [`segmenter.py`](backend/app/segmenter.py) measures loudness in 20 ms frames, finds pauses against an adaptive noise floor, and packs speech into pieces of about 15 seconds and never more than 28. If someone talks for longer than 28 seconds without pausing, the piece is cut at the quietest moment. Each piece's start and end become the transcript timestamps. Long silences are dropped, which saves cost because Bodhan bills per second of audio.
- **Why a backend?** If the Bodhan key were inside a browser or mobile app, anyone could extract it, and phones cannot easily run ffmpeg. The backend keeps the key and does all the processing.
- **Why background jobs?** A 10-minute call is about 40–50 API requests. The upload returns a `call_id` straight away and the app polls for progress. If the tab reloads or the network drops, the app picks up the same call again from `?call=<id>` in the URL.
- **One file per external API.** All Bodhan calls live in [`bodhan_client.py`](backend/app/bodhan_client.py) (`transcribe()` and `translate()`). It retries 429, 5xx and network errors with exponential backoff, honours `Retry-After`, and caps parallel requests. Tests swap it for a fake.
- **Translation transport.** The Bodhan docs prefer `POST /translate`, but on 2026-10-20 the live gateway returned 404 there. The documented alternative works: `POST /v1/chat/completions` with `model: "indic-translate"`, `target_language` and one user message. A `finish_reason` of `"length"` is that transport's version of `truncated: true`.

### Error handling

| Situation | What happens |
|---|---|
| Wrong type, empty, too large (default 50 MB), too long (default 30 min) | The upload is rejected with a clear message (`415`/`400`/`413`) |
| A piece fails after retries | The call is marked `partial`; that segment shows its error and the other segments are kept |
| Bodhan rate limit hit (`Transcription failed (429)`) | Retried with backoff; if retries run out, the piece fails as above. See [Rate limits (429 errors)](#rate-limits-429-errors) |
| `truncated: true` from translation | Treated as an error for that piece; a half translation is never shown as complete |
| Every piece fails, or the API key is rejected | The call is marked `failed` |
| Call language is English | Translation is skipped |
| Bhili / Bhojpuri (not supported by Translation) | Only the original transcript is shown; `english_text` is `null` |
| Sarvam analysis fails (error, no credits, cut-off answer, bad JSON, rejected key) | Claude is tried next. If every model fails, a warning is added and `analysis` is `null`; the call keeps its status |

## Output

`GET /api/calls/{id}/export?format=json` (abridged):

```json
{
  "call_id": "9f2c41d07ab35e18",
  "source_file": "call_2026_10_10_1432.m4a",
  "ticket_id": "TKT-10452",
  "agent_name": "Agent-07",
  "duration_seconds": 312.4,
  "source_language": "hi",
  "target_language": "en",
  "translation_available": true,
  "processed_at": "2026-10-15T14:32:10+05:30",
  "status": "completed",
  "speaker_count": null,
  "segments": [
    {
      "segment_no": 1, "start": 0.35, "end": 6.2, "speaker": null,
      "original_text": "नमस्ते, मेरा इंटरनेट दो दिन से बंद है।",
      "english_text": "Hello, my internet has not been working for two days.",
      "error": null,
      "sentiment": "negative"
    }
  ],
  "full_text_original": "…",
  "full_text_english": "…",
  "analysis": {
    "summary": "The customer's internet has been down for two days. The agent found an outage in the area …",
    "customer_issue": "Internet not working for two days.",
    "resolution": "partially_resolved",
    "action_items": ["Agent to call back tomorrow morning to confirm the fix"],
    "topics": ["internet outage"],
    "overall_sentiment": "mixed",
    "sentiment_score": -0.2,
    "customer_sentiment_trend": "improved",
    "sentiment_explanation": "Frustrated at first, calmer once given a timeline.",
    "model": "sarvam-105b"
  },
  "warnings": []
}
```

| File | One row per | Columns |
|---|---|---|
| `calls.csv` | call | call_id, source_file, ticket_id, agent_name, duration_seconds, source_language, processed_at, status, summary, customer_issue, resolution, action_items, topics, overall_sentiment, sentiment_score, customer_sentiment_trend, transcript_english, transcript_original |
| `segments.csv` | piece | call_id, segment_no, start, end, speaker, sentiment, original_text, english_text |

The analysis columns are empty when the call was not analysed.

CSV files are UTF-8 with a BOM so Excel displays Indian scripts correctly. Cells that start with `=`, `+`, `-` or `@` are prefixed with `'` so a spreadsheet does not run them as formulas.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/calls` | multipart: `file`, optional `language`, `ticket_id`, `agent_name` → `202 {call_id}` |
| `GET /api/calls/{call_id}` | status (`queued`, `processing`, `completed`, `partial`, `failed`), progress, record when ready |
| `GET /api/calls/{call_id}/export?format=json\|calls_csv\|segments_csv` | the export file |
| `GET /api/languages` | supported languages and whether translation is available for each |
| `GET /api/config` | limits the app checks before uploading |
| `GET /health` | liveness check |

Errors always have the shape `{"error": {"code": "...", "message": "..."}}`. Interactive docs are at `/docs`.

## Configuration

All settings come from `.env` (see [`.env.example`](.env.example)). The main ones are `BODHAN_API_KEY` (or per-model `BODHAN_TRANSCRIBE_API_KEY` and `BODHAN_TRANSLATE_API_KEY`, since a Bodhan key can be scoped to one model), `MAX_FILE_MB`, `MAX_DURATION_SECONDS`, `RESULT_TTL_SECONDS`, `BODHAN_CONCURRENCY` and `BODHAN_RPM` (a client-side cap on requests per minute). `APP_KEY` is a shared key that the app must send in `X-App-Key`; the web app reads it from `VITE_APP_KEY`. `SARVAM_*` settings control the optional AI summary (see [AI summary and sentiment](#ai-summary-and-sentiment)). `DIARIZATION_PROVIDER`, `DIARIZATION_MIN_VRAM_GB` and `DIARIZATION_MODEL` control the optional speaker model (see [Speaker diarization](#speaker-diarization)).

### Rate limits (429 errors)

**Symptom.** A long call finishes as **Partially completed**, and many segments show `This piece couldn't be fully processed: Transcription failed (429)` with "No transcript for this piece." HTTP 429 means *Too Many Requests*: Bodhan rejected the request because the key's rate limit was exceeded. The audio and the app are fine.

**Cause.** Every piece needs one transcription request and one translation request. A 13-minute call split into 105 pieces, which comes to about 210 requests. Without these settings in `.env`, the code defaults apply (`BODHAN_CONCURRENCY=4`, `BODHAN_RPM=0`, meaning no cap per minute), and the app sends them as fast as it can and reaches the limit within seconds. The default 4 retries back off 1 + 2 + 4 + 8 ≈ 15 s in total. That is shorter than the 60-second window a per-minute limit takes to reset, so the retries also get a 429. Retries count against the limit too. In the run where we found this, 89 of 105 pieces failed.

**Fix.** Pace the requests on the client and allow retries long enough to outlast a full window. Add the following to `.env`:

```
BODHAN_RPM=50            # requests per minute across transcription and translation
BODHAN_CONCURRENCY=2     # requests in flight at once
BODHAN_MAX_RETRIES=6     # backoff 1+2+4+8+16+20 ≈ 51 s, enough to outlast a 1-minute window
```

Restart the backend so it reads the new values, and upload the call again. Failed pieces cannot be retried from the results page. With these settings the same 13-minute call processed successfully. Expect a long call to take longer: about 210 requests at 50/min is roughly 4–5 minutes. If 429s continue, your key's limit is lower than expected, so try `BODHAN_RPM=30` or `20`.

`.env.example` notes that keys seen so far allow 120 requests/min and 10 in parallel. Bodhan sends the `x-ratelimit-limit-requests` and `x-ratelimit-limit-parallel-requests` headers only on successful responses, so check a successful call to see your key's real limits. One rate limiter covers every job on the server, so `MAX_CONCURRENT_JOBS=2` does not double the request rate.

## Security and privacy

Call recordings contain personal data, so all audio and transcripts are treated as sensitive.

- The Bodhan key stays on the backend. `.env` is git-ignored.
- Uploaded audio is deleted when processing ends, even on failure. Results are kept in memory only and deleted after `RESULT_TTL_SECONDS` (default 1 hour). There is no database.
- Logs contain only the call ID, status, timing and Bodhan `x-request-id`. They never contain audio or transcript text.
- File type is checked by content, not only by extension. Call IDs are random 64-bit values because, without a login, the ID is what grants access to a result.
- `APP_KEY` only keeps out casual traffic. Anyone with the web bundle can read it, so it is not a login. Put the server behind HTTPS.
- If you reuse this code with your own customers' calls, follow your company's data policy and India's DPDP Act, 2023.

## AI summary and sentiment

Set `SARVAM_API_KEY` and/or `ANTHROPIC_API_KEY` in `.env` to turn it on. Sarvam is asked first; if it fails for any reason (error, no credits, cut-off or unusable answer), the same request goes to Claude. `analysis.model` says which model wrote the result. Without either key, `analysis` is `null`, segments have no `sentiment`, and the web app hides the summary card and the "Summarizing" step (`GET /api/config` reports `analysis_enabled`).

After translation, [`analysis.py`](backend/app/analysis.py) sends the whole call to Sarvam's `POST /v1/chat/completions` in **one request**. Each segment is one numbered line, `[n] (mm:ss) Speaker 1: text`, using the English text where there is one and the original text otherwise. The request uses `response_format: json_schema` (strict), so the answer is a fixed JSON shape. To keep the answer short enough for a 30-minute call, the model lists only the segments that are clearly positive or negative, and every other segment with text is tagged `neutral`. All Sarvam HTTP calls live in [`sarvam_client.py`](backend/app/sarvam_client.py). It sends the key in the `api-subscription-key` header and retries 429, 5xx and network errors. Claude calls live in [`claude_client.py`](backend/app/claude_client.py) and use the official `anthropic` SDK: the schema goes in `output_config.format`, and `fallbacks: "default"` lets the API re-run a refused request on another Claude model.

| Setting | Default | Meaning |
|---|---|---|
| `SARVAM_API_KEY` | empty (off) | Sarvam subscription key |
| `SARVAM_MODEL` | `sarvam-105b` | 128K context, enough for a 30-minute call |
| `SARVAM_REASONING_EFFORT` | `low` | `low`, `high`, `max`, or `none` (off). Reasoning tokens count against the token budget |
| `SARVAM_MAX_TOKENS` | `4096` | Answer + reasoning. A cut-off answer is a warning, never a half summary |
| `SARVAM_RPM` | `0` | Client-side requests/minute cap (pay-as-you-go allows 60 for `sarvam-105b`) |
| `ANTHROPIC_API_KEY` | empty (off) | Claude key for the fallback |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Claude model |
| `ANTHROPIC_EFFORT` | `medium` | `low` … `max`. Claude Opus 5.5 always thinks; effort sets how much |
| `ANTHROPIC_MAX_TOKENS` | `16000` | Answer + thinking budget for Claude |

Analysis is an extra, like speaker labels: if it fails, the call keeps its status and gets a warning. Speaker labels from the diarization model are generic, so the prompt asks the LLM to work out from what is said who is the agent and who is the customer. Sentiment is the **customer's**.

## Speaker diarization

Diarization labels who spoke when, for example agent and customer. Bodhan models do not do this, so the app can optionally run NVIDIA's [Nemotron-3-Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization) model **locally on an NVIDIA GPU**. It is off unless you turn it on, and it turns itself off on machines that cannot run it, so the rest of the app never depends on it.

### The model

| | |
|---|---|
| What it does | Speaker diarization only: start time, end time and a speaker number. It does **not** transcribe; Bodhan still does that |
| Speakers | Up to 8 per recording, numbered in order of first appearance (Sortformer architecture) |
| Size | ~100M parameters. Weights are ~400 MB (FP32 safetensors); a ~107 MB Q8 GGUF also exists for NeMo-Speech.cpp |
| Input | 16 kHz mono audio. The app's ffmpeg step already produces this, so 8 kHz phone recordings work |
| Long calls | The offline mode splits a recording into ~30 s chunks with a speaker cache, so GPU memory stays flat whatever the call length |
| Licence | OpenMDW-1.1. The model card says it is ready for commercial use |
| Released | 23 September 2026. Trained on ~10,000 hours of real conversations plus simulated multi-speaker mixtures |
| Supported GPUs (model card) | NVIDIA Ampere (RTX 30xx, including the RTX 3050), Ada Lovelace (RTX 40xx), Blackwell (RTX 50xx) and Hopper data-centre GPUs |
| Supported OS (model card) | **Linux only.** See [The Windows catch](#the-windows-catch) |

**Measured on a laptop RTX 3050 Ti (4 GB) on Windows 11**, with a real 13.4-minute call:

| | |
|---|---|
| Diarization time | 2.4–3 s, about 300× faster than real time |
| Peak GPU memory | ~490 MiB (weights 379 MiB) |
| Model load at server start | ~8 s, or ~50 s the first time while the ~400 MB download runs |

The training data listed on the model card is largely English, with some Mandarin and multilingual sets. Check its accuracy on your own calls and languages before relying on the labels.

### The Windows catch

The model card lists **Linux** as the only supported OS, and its main instructions use the NVIDIA NeMo toolkit (`nemo-toolkit[asr]`), which does not install reliably on native Windows. There are three ways to run the model:

| Option | Windows | Used here |
|---|---|---|
| **Hugging Face Transformers** (`AutoModelForAudioFrameClassification`) | Works natively. It is plain PyTorch, so it does not depend on the OS | **Yes** |
| NVIDIA NeMo toolkit | Use WSL2 (Windows Subsystem for Linux), which can use the GPU with a normal Windows NVIDIA driver | No. Only needed for fine-tuning |
| NeMo-Speech.cpp (C++ runtime with the GGUF file) | Not verified | No |

Things that differ from the model card, found while setting this up on Windows:

- **The PyPI release of Transformers is enough.** The card says to install Transformers from GitHub source, but `transformers==5.21.0` from PyPI already includes the model. This also avoids needing GitHub access.
- **`librosa` is required.** The model's feature extractor needs it, and the card does not mention it. It is in `requirements-diarization.txt`.
- **Install the CUDA build of PyTorch.** A plain `pip install torch` gives a CPU-only build on Windows. `requirements-diarization.txt` points pip at the CUDA 12.6 build, which needs an NVIDIA driver that supports CUDA 12.6 or later (`nvidia-smi` shows the version).
- **A harmless warning about symlinks.** On Windows the Hugging Face cache warns that it cannot use symlinks unless Developer Mode is on. Everything still works; the cache just uses a little more disk.
- **Windows reserves some GPU memory** for the display. On a 4 GB laptop GPU about 3.2 GB was free, still far more than the model needs.

### Set it up

1. Install the extra packages into the backend's virtual environment: `pip install -r requirements-diarization.txt`
2. In `.env`, set `DIARIZATION_PROVIDER=nemotron`.
3. Start the backend. The first start downloads the model from Hugging Face (~400 MB, no account or token needed) into the Hugging Face cache (`~/.cache/huggingface`).

The log shows `Loading nvidia/Nemotron-3-Diarization on the GPU`, or a warning that starts with `Speaker diarization is off:` and says why.

| Setting | Default | Meaning |
|---|---|---|
| `DIARIZATION_PROVIDER` | `none` | `none` (off) or `nemotron` (local GPU) |
| `DIARIZATION_MIN_VRAM_GB` | `2` | Smallest total GPU memory the model will be used on. It needs ~0.5 GB, so 2 leaves headroom |
| `DIARIZATION_MODEL` | `nvidia/Nemotron-3-Diarization` | Hugging Face model ID or local folder |

### How the GPU check works

With `DIARIZATION_PROVIDER=nemotron`, [`diarization.py`](backend/app/diarization.py) checks at startup, in order:

1. PyTorch and Transformers are installed.
2. PyTorch can see a CUDA GPU (this fails on machines without an NVIDIA GPU, and with a CPU-only PyTorch build).
3. The first GPU has at least `DIARIZATION_MIN_VRAM_GB` of total memory.
4. The model loads (this fails, for example, if the first download cannot reach Hugging Face).

If any step fails, diarization is switched off for that server run, a warning is logged, and calls are split at pauses as before. `GET /api/config` reports `diarization_enabled` from what actually loaded, not from `.env`, so the web app only shows the "Identifying speakers" step and the speaker note when the model is really running. The CLI uses the same check.

There is no CPU fallback on purpose. The model would run on a CPU, but more slowly, and the feature is meant for machines with a suitable GPU.

### What changes when it is on

- The pipeline runs the speaker model on the whole call, then [`segmenter.py`](backend/app/segmenter.py) (`split_by_turns`) cuts each speaker turn at pauses, so every piece belongs to exactly one speaker. Overlapping speech is resolved and speakers are labelled `Speaker 1` … `Speaker 8` in order of first appearance.
- `speaker` is filled in each segment, `speaker_count` in the call record, and transcripts read `Speaker 1: …`.
- **More pieces, so more Bodhan requests.** A piece never crosses a speaker change, so pieces are shorter. On the 13-minute call above, pause-based splitting gave 105 pieces and speaker-based splitting gave 127 (about 20% more). Each piece is a transcription and a translation request, so calls take longer under `BODHAN_RPM` (see [Rate limits](#rate-limits-429-errors)).
- One call uses the GPU at a time (each takes seconds), even with `MAX_CONCURRENT_JOBS=2`.
- If the model fails on a call, that call falls back to pause-based splitting with a warning. Diarization never fails a call.

The Docker image does not include the speaker model. It is a CPU image without PyTorch; GPU containers need the NVIDIA Container Toolkit and a CUDA base image.

### Without an NVIDIA GPU

| Option | Cost | Trade-off |
|---|---|---|
| A hosted Nemotron-3-Diarization endpoint, for example [DeepInfra](https://deepinfra.com/nvidia/Nemotron-3-Diarization) (Baseten and DigitalOcean also host it) | About $0.0006 per audio minute on DeepInfra | Not built in: implement `Diarizer` and register it in `build_diarizer`. Needs a separate account and key, and audio goes to another provider, so check your data policy |
| Split stereo channels | Free | No model needed, and it only works when the agent and customer were recorded on separate channels |

As of October 2026 the model is not on build.nvidia.com, so an NVIDIA API key cannot call it. Keep any hosted provider's key in its own environment variable, and prefer providers that diarize the whole call in one request: if a provider only accepts short clips, `speaker_0` in one clip may be a different person in the next.

## Limitations

- **Speaker labels need an NVIDIA GPU.** Without one (or with `DIARIZATION_PROVIDER=none`), `speaker` is `null`. Labels are generic (`Speaker 1`, `Speaker 2`); the app does not know which one is the agent. See [Speaker diarization](#speaker-diarization).
- **Language detection is not reported.** Bodhan can detect the language, but it does not say which language it found, so `source_language` is filled only when the user chooses it. Choosing it also improves accuracy.
- **Single server, in-memory.** Jobs and results live in one process. Restarting the server loses them, and running more than one worker needs a shared store (for example Redis).
- **Context across pieces.** Each piece is transcribed and translated on its own, so a sentence that crosses a cut may read less smoothly.
- **The summary is AI-generated.** It can be wrong, especially when the translation is poor. The app labels it as such and keeps the transcript next to it.
- **Cost.** Speech-to-Text is billed per second of audio, and Translation is billed per output token. The call is cut at every pause, with each piece at most 28 s, so the number of pieces depends on how often people pause as well as on length. A call with long stretches of speech can be about 20–25 pieces per 10 minutes. A conversational call can be far more: a 13-minute call split into 105 pieces (about 8 s each). Each piece is one transcription request plus one translation request, so the piece count also determines how long the call takes under `BODHAN_RPM`.

## Project structure

```
support_call_summarizer/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app and routes
│   │   ├── jobs.py            # background jobs, in-memory store with expiry
│   │   ├── pipeline.py        # convert → split → transcribe → translate → analyse
│   │   ├── bodhan_client.py   # every Bodhan API call
│   │   ├── sarvam_client.py   # every Sarvam API call (sarvam-105b chat)
│   │   ├── claude_client.py   # every Claude API call (fallback for the analysis)
│   │   ├── llm.py             # the interface both LLM clients follow
│   │   ├── analysis.py        # optional summary + sentiment prompt, schema and parsing
│   │   ├── audio.py           # validation and ffmpeg conversion
│   │   ├── segmenter.py       # split at pauses / by speaker turn
│   │   ├── diarization.py     # optional speaker model (Nemotron on a local GPU) + GPU check
│   │   ├── models.py          # CallRecord, Segment (one schema for API + exports)
│   │   ├── exporters.py       # JSON and CSV
│   │   ├── languages.py       # supported languages
│   │   └── config.py          # settings from .env
│   ├── cli.py                 # command-line tool
│   ├── requirements-diarization.txt  # optional: PyTorch + Transformers for the speaker model
│   ├── scripts/make_sample_call.py
│   └── tests/                 # pytest, Bodhan, Sarvam and Claude mocked
├── web/                       # React + Vite + TypeScript app (Bodhan.AI design: src/styles/tokens.css)
├── sample_data/               # synthetic sample calls (git-ignored)
├── outputs/                   # CLI output (git-ignored)
├── Dockerfile
└── .env.example
```
