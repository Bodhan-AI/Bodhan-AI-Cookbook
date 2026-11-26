# Video Dubbing Pipeline (`bodhan_dub`)

> Agentic English-to-Indic video dubbing pipeline using Bodhan `indic-transcribe`, `indic-translate`, and `indic-speak` with duration-preserving synchronization, background audio ducking, subtitle generation, and automated CER quality verification.

**Bodhan models:** `indic-transcribe`, `indic-translate`, `indic-speak`
**Languages shown:** Hindi (`hi`), Tamil (`ta`), Bengali (`bn`) — plus all 13 production-tier Indic languages
**Format:** Python CLI (`python -m bodhan_dub`)

---

## 1. What it does

`bodhan_dub` takes an English video (`MP4`, `MOV`, `MKV`, or `WebM`, 1–15 minutes), transcribes the spoken English track using Bodhan `indic-transcribe`, translates segments into one or more Indian languages using `indic-translate`, synthesizes dubbed speech with `indic-speak`, and synchronizes the speech back onto the original video without altering video playback speed. An agentic planner monitors segment fit (`0.85x`–`1.20x` tempo bounds), condenses overlong English source text (never rewriting Indic translations directly), runs `indic-transcribe` back-transcription with Levenshtein Character Error Rate (CER) checks, mixes ducked background audio at `-16 LUFS ±1 dB`, and exports dubbed MP4s, `.srt`/`.vtt` subtitles, and a structured `report.json`.

---

## 2. Bodhan models and languages

| Capability | Bodhan Model | Endpoint | Notes |
|---|---|---|---|
| Speech Recognition | `indic-transcribe` | `POST /v1/audio/transcriptions` | Chunked into <= 25s WAV windows |
| Translation | `indic-translate` | `POST /translate` | Native or `codemix` script; output tokens billed |
| Speech Synthesis | `indic-speak` | `POST /v1/audio/speech` | Language-matched voices (PCM16 WAV) |
| Back-Transcription QA | `indic-transcribe` | `POST /v1/audio/transcriptions` | Verifies synthesized Indic speech via CER |

### Supported Languages (13 Production-Tier)

| Code | Language | Default Voice | Code | Language | Default Voice |
|---|---|---|---|---|---|
| `hi` | Hindi | `Kavya` | `ml` | Malayalam | `Lakshmi` |
| `ta` | Tamil | `Anitha` | `pa` | Punjabi | `Kaur` |
| `bn` | Bengali | `Ishita` | `or` | Odia | `Itishree` |
| `te` | Telugu | `Sravani` | `as` | Assamese | `Prastuti` |
| `mr` | Marathi | `Anagha` | `ur` | Urdu | `Saba` |
| `gu` | Gujarati | `Dhara` | `sa` | Sanskrit | `Bharati` |
| `kn` | Kannada | `Deepika` | | | |

---

## Setup

### Prerequisites
- Python 3.11+
- `ffmpeg` and `ffprobe` installed and available on `PATH`
- A Bodhan API key from [console.bodhan.ai](https://console.bodhan.ai)

### Installation

```bash
cd examples/video_dubbing
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
cp .env.example .env
# Edit .env and set BODHAN_API_KEY=<your key>
```

Required environment variables in `.env` (see `.env.example`):
- `BODHAN_API_KEY` — Your Bodhan API key
- `BODHAN_BASE_URL` — Defaults to `https://api.bodhan.ai`

---

## Run

### Standard CLI Execution

```bash
python -m bodhan_dub --input path/to/video.mp4 --langs hi,ta,bn --out ./outputs
```

### Optional Flags

```bash
python -m bodhan_dub \
  --input path/to/video.mp4 \
  --langs hi,ta,bn \
  --out ./outputs \
  --glossary configs/glossary.example.yaml \
  --voices configs/voices.example.yaml \
  --code-mixed
```

### Offline / Mock Execution (No API Credits Required)

```bash
python -m bodhan_dub --input path/to/video.mp4 --langs hi,ta,bn --out ./outputs --mock
```

---

## How it works

1. **Video Validation (`VideoEngine`)**: Inspects container format (`.mp4`, `.mov`, `.mkv`, `.webm`), streams, frame rate, sample rate, channels, and duration via `ffprobe`. Handles videos without audio streams gracefully.
2. **Dual Audio Extraction (`VideoEngine`)**: Extracts a 16 kHz mono WAV for `indic-transcribe` and a 48 kHz stereo WAV for background music/effects preservation.
3. **Windowed Transcription (`BodhanTranscribeClient`)**: Splits long audio into <= 25-second windows and calls `POST /v1/audio/transcriptions` (`indic-transcribe`) to produce `transcript.en.json`.
4. **Agentic Planning (`PipelinePlanner`)**: Initializes per-segment execution state and bounded retry budgets (`MAX_CONDENSATION_RETRIES=2`).
5. **Translation (`BodhanTranslateClient`)**: Calls `POST /translate` (`indic-translate`) with `"native"` or `"codemix"` script and deterministic glossary handling.
6. **Speech Synthesis (`BodhanSpeakClient`)**: Calls `POST /v1/audio/speech` (`indic-speak`) using official per-language voices.
7. **Synchronization & Condensation Loop (`AudioSyncEngine` + `EnglishCondenser`)**: Checks whether synthesized audio fits within `[0.85x, 1.20x]` tempo bounds using FFmpeg `atempo`. If overlong (`> 1.20x`), the agent condenses the **English** source text (never rewriting Indic text directly), re-translates via `indic-translate`, and re-synthesizes via `indic-speak`.
8. **Back-Transcription & QA (`QualityAssuranceVerifier`)**: Back-transcribes fitted speech with `indic-transcribe`, applies Unicode `NFC` normalization, computes true Levenshtein distance and CER, and checks for silence or PCM16 clipping.
9. **Audio Mastering (`AudioMaster`)**: Ducks original background audio and normalizes the mixed master to `-16.0 LUFS` (`TP=-1.5`, `LRA=11`).
10. **Muxing, Subtitles & Reporting (`VideoEngine`, `SubtitleEngine`, `CostAndLatencyTracker`)**: Remuxes video (`-c:v copy`) with exact target duration (`±100 ms`), writes UTF-8 `.srt` and `.vtt` subtitles, and outputs `report.json`.

---

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full Mermaid component diagram and module breakdown:

- `bodhan_dub/cli.py` — CLI argument parser and entrypoint
- `bodhan_dub/config.py` — Environment configuration, 13 language mappings, official voice defaults
- `bodhan_dub/clients/` — `BodhanBaseClient`, `BodhanTranscribeClient`, `BodhanTranslateClient`, `BodhanSpeakClient`
- `bodhan_dub/core/` — `BodhanDubbingOrchestrator`, `VideoEngine`, `AudioSyncEngine`, `AudioMaster`, `SubtitleEngine`
- `bodhan_dub/agent/` — `PipelinePlanner`, `EnglishCondenser`, `QualityAssuranceVerifier`, `CostAndLatencyTracker`
- `bodhan_dub/models/` — Pydantic schemas (`DubSegment`, `DubbingReport`, `LanguageExecutionSummary`, `APIMetric`)

---

## Workflow

See [docs/workflow.md](docs/workflow.md) for the detailed stage-by-stage data flow, retry policies, and timing math.

### Retry, Concurrency, and Error Handling
- **HTTP Transport Retries**: `urllib3.util.Retry(total=3, backoff_factor=1.0, status_forcelist=[429, 502, 503, 504], respect_retry_after_header=True)` prevents infinite retry loops.
- **Bounded Agent Retries**: `PipelinePlanner` caps English condensation retries at `BODHAN_MAX_CONDENSATION_RETRIES` (default `2`).
- **Bounded Concurrency**: `BodhanDubbingOrchestrator` processes target languages using a `ThreadPoolExecutor` capped at `BODHAN_MAX_WORKERS` (default `2`) with thread-safe metric tracking.

---

## Output structure

```text
outputs/
├── transcript.en.json          # Timestamped English source segments
├── report.json                 # Master QA, duration drift, latency & status report
├── hi/
│   ├── video.hi.mp4            # Dubbed Hindi MP4 with embedded subtitle track
│   ├── subtitles.hi.srt        # UTF-8 SRT subtitles
│   ├── subtitles.hi.vtt        # UTF-8 WebVTT subtitles
│   └── segments.hi.json        # Per-segment metadata, tempo, CER, QA status
├── ta/
│   └── ...
└── bn/
    └── ...
```

---

## Quality assurance

- **Unicode Normalization**: `unicodedata.normalize("NFC", text)` before CER comparison.
- **Character Error Rate (CER)**: Computed using true Levenshtein edit distance (`Levenshtein(ref, hyp) / max(len(ref), 1)`).
- **Audio Defect Detection**: Scans PCM16 samples for silence (`max_amp < 150`) and clipping (`abs(sample) >= 32760`).
- **Synchronization Targets**: Segment start alignment within `±300 ms`; total video duration drift within `±100 ms`; `atempo` strictly bounded to `[0.85x, 1.20x]`.
- **Loudness Target**: `-16.0 LUFS ±1 dB` via FFmpeg `loudnorm`.

---

## Testing

Run the recipe's automated test suite (unit, integration, and acceptance tests):

```bash
cd examples/video_dubbing
pytest tests/ -v
```

Run the Cookbook recipe validator from the Cookbook root:

```bash
python scripts/validate_recipe.py examples/video_dubbing
```

---

## Limitations

- **Indic-Transcribe Timestamps**: `indic-transcribe` returns plain text without word-level timestamps; segment boundaries are derived via <= 25s audio windowing and sentence-aware splitting.
- **Indic-Speak Availability & Fallback**: If `indic-speak` returns a transient upstream `5xx` error after bounded retries, the client falls back to a synthetic PCM16 WAV generator so the pipeline completes without crashing; in that fallback state, back-transcription CER will be high (`~1.0`) and segments are flagged `MANUAL_REVIEW`.
- **Live API Measurements**: End-to-end live API latency, CER, and billing metrics for the updated `POST /translate` integration are pending live verification with an active `BODHAN_API_KEY`.

---

## Cost and runtime methodology

`CostAndLatencyTracker` records per-service call counts, total/average latency in seconds, and retry counts in `report.json` (`api_metrics`). Actual USD billing depends on your Bodhan plan (per-second audio for `indic-transcribe`, output tokens for `indic-translate`, per-character audio for `indic-speak`); therefore `cost_usd` is reported as `null` with `cost_notes` rather than fabricating unverified rates.
