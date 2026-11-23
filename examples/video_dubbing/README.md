# Video dubbing pipeline

> Dub an English video into Indian languages with Bodhan's Indic models, keeping every line in sync with the original speech.

**Bodhan models:** `indic-transcribe`, `indic-translate`, `indic-speak`
**Languages shown:** Hindi, Tamil, Bengali — `--langs` accepts any language in the Indic-Speak voice library
**Format:** CLI (`python -m bodhan_dub`)

## What it does

Takes one English video and produces, for each requested language, a dubbed MP4
and a subtitle file. Speech is transcribed with `indic-transcribe`, translated
with `indic-translate`, and spoken with `indic-speak`. Non-speech audio is kept,
translated speech is fitted into the original time slots, and every run writes a
machine-readable report with per-segment timings, retries, QA results, latency
and cost.

The 30-second version of the design: **transcript timestamps come from where we
cut the audio**, because the hosted ASR returns text only. Voice-activity
detection finds speech, chunks are capped at 28 seconds and cut on pauses, and
each chunk becomes a segment with a sample-accurate start and end.

> **Status.** Complete. One command turns an English video into a dubbed MP4 with
> subtitles, in one or more Indian languages, plus a run report. Measured on the
> demo lesson: 72/72 segments dubbed, **−16.07 LUFS**, **+0 ms** duration drift,
> Hindi at **₹1.97** for 5.5 minutes. See [Results](#results).
>
> Design notes: [architecture](docs/architecture.md) ·
> [workflow](docs/workflow.md) · [design notes](docs/design.md) ·
> [build plan](docs/plan.md)

## Setup

Requires Python 3.10+ and **ffmpeg** (with ffprobe) on the machine.

```bash
# macOS
brew install ffmpeg
# Debian / Ubuntu
sudo apt-get install -y ffmpeg

cd examples/video_dubbing
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env      # then paste your keys from https://console.bodhan.ai
```

`ffmpeg` is discovered on `PATH`, or from `FFMPEG_BIN` / `FFPROBE_BIN`, or from
the usual package-manager locations. No GPU and no model weights are needed —
the hosted API is enough.

Or use the container, which pins Python and installs ffmpeg:

```bash
docker build -t bodhan-dub .
docker run --rm -e BODHAN_API_KEY -v "$PWD/demo:/media" \
  bodhan-dub --input /media/clip.mp4 --langs hi,ta,bn --out /media/out
```

### Configuration

Keys are read from a `.env` file or the environment. The loader looks in three
places, in order: `BODHAN_DUB_ENV_FILE`, the nearest `.env`, then
`~/.config/bodhan/.env`.

> `make check` fails if a `.env` exists anywhere in this repository — that is the
> point of the check. Keep working keys at `~/.config/bodhan/.env`:

```bash
mkdir -p ~/.config/bodhan
cp .env.example ~/.config/bodhan/.env   # then edit it
```

| Variable | Purpose |
|---|---|
| `BODHAN_API_KEY` | one key used for every model (simplest) |
| `BODHAN_TRANSCRIBE_KEY` | key for `indic-transcribe` (`BODHAN_TRANSCRIBE_API_KEY` also accepted) |
| `BODHAN_TRANSLATE_KEY` | key for `indic-translate` (`BODHAN_TRANSLATE_API_KEY` also accepted) |
| `BODHAN_SPEAK_KEY` | key for `indic-speak` (`BODHAN_SPEAK_API_KEY` also accepted) |
| `BODHAN_BASE_URL` | defaults to `https://api.bodhan.ai` |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | OpenAI-compatible companion model, used only for English-side judgement |

Optional tuning: `BODHAN_DUB_CACHE`, `BODHAN_DUB_RPM`,
`BODHAN_DUB_MAX_INFLIGHT`, `BODHAN_DUB_MAX_ATTEMPTS`, `BODHAN_DUB_TIMEOUT_S`,
`BODHAN_DUB_LOG_LEVEL`.

`BODHAN_DUB_RPM` defaults to 120, the rate the console shows for
`indic-transcribe`. Set it to whatever your model's Rate Limits panel says —
this deployment does not return the `x-ratelimit-*` response headers, so the
value cannot be discovered automatically.

## Run

```bash
python -m bodhan_dub \
  --input path/to/video.mp4 \
  --langs hi,ta,bn \
  --out ./out
```

Useful flags: `--stage asr` stops after transcription, `--force` ignores cached
responses, `--glossary` and `--voices` take YAML overrides, `--code-mixed` keeps
English technical terms inside the dub.

### Output

```
out/
  report.json            # stage status, timings, retries, QA, cost, targets met or missed
  transcript.en.json     # English segments with start/end times
  run.log                # structured JSON log, one event per line
  hi/
    video.hi.mp4         # dubbed video, video stream copied, audio replaced
    subtitles.hi.srt
    segments.hi.json     # source, translation, TTS duration, speed factor, QA
    audio.hi.wav         # dub track alone, exactly the input duration
    bed.hi.wav           # original audio with speech gated out
    mix.hi.wav           # bed + dub, before loudness normalisation
    speech.hi.wav        # dub normalised to -16 LUFS
    audio/0000.wav ...   # per-segment clips, for inspection
  ta/ ...
```

## How it works

**Phase 1, transcription (done).**

1. `ffprobe` reads duration and streams; `ffmpeg` decodes to 16 kHz mono PCM once
   and caches it against the input's content hash.
2. Voice-activity detection finds speech with an adaptive noise floor and
   hysteresis, bridging pauses shorter than 300 ms so a sentence is not split.
3. Chunks are capped at 28 s and longer regions are split at their quietest
   internal point, never mid-word.
4. Each chunk is transcribed by `indic-transcribe`, in parallel, behind a
   content-addressed cache.
5. Text is normalised, degenerate responses are dropped, and any looping
   transcription is collapsed before it reaches a segment.

**Phase 2, translation and speech (done).**

1. Consecutive segments are grouped into a block of about 250 characters and sent
   to `indic-translate` with **one segment per line**.
2. The reply comes back as one translated line per segment, so each segment keeps
   its own text *and* benefits from the surrounding context. If the line count
   does not match, the translation is split proportionally instead and the
   segment records `alignment: "proportional"`.
3. Each line is spoken with the language's voice and the `educational lecture`
   style.
4. The result is back-transcribed with `indic-transcribe` and checked: character
   error rate, repeated phrases, truncation. A failure is retried, then split in
   half and rejoined, then accepted with `qa: fail` and a reason.
5. Leading silence is trimmed, then the clip is fitted into its slot with
   `atempo` (never past 1.20x), spilling into following silence if needed.
6. Clips are placed at their source start times on a silent track the length of
   the input.

**Phase 3, assembly (done).**

1. The original audio is decoded once and **gated**: every speech interval the
   VAD found is silenced with a 10 ms fade, so page turns, effects and music
   between sentences survive.
2. The dub is normalised to **−16 LUFS** with a two-pass `loudnorm`, the bed is
   mixed in 6 dB below it, and the mix is measured again.
3. The audio is muxed into the video with **`-c:v copy`** and `-t` set to the
   input duration, so the picture is untouched and the length cannot drift.
4. Subtitles are written as SRT at the same timings the dub was placed at, and
   muxed **into the MP4** as a real subtitle track tagged with the language code
   (`hin`, `tam`, `ben`), so players list it in their subtitle menu. `-c:v copy`
   still applies, so this costs nothing in quality. `--burn-subtitles` renders
   them into the picture instead, which needs an ffmpeg built with libass — the
   Dockerfile has one.
5. The finished MP4 is re-probed and its duration compared with the input.

## Design decisions worth knowing

- **Timestamps come from chunk boundaries, not the model.** The hosted
  `indic-transcribe` endpoint returns `{"text": ...}` and no word timings. The
  brief anticipates this. `tools/probe_api.py` re-checks the response shape if a
  timestamp-bearing `response_format` ever appears.
- **One ASR request per utterance.** Audio is billed by the second, so shorter
  requests cost the same in money and buy much better sync. The worker pool hides
  the extra latency.
- **Requests are paced *and* concurrency-gated.** The console advertises 120
  requests per minute per model, but a real run was rejected at roughly 26 per
  minute, which means the binding constraint is how many requests are in flight.
  So both dimensions adapt: request starts are spaced at half the advertised
  rate, and the number of simultaneous calls starts at two, drops a slot on every
  429 and recovers one every ten successes. Cooldowns are capped at 12 s — an
  escalating 60 s pause, once the rate has fallen, is what turned a three-minute
  run into 14.5 minutes.
- **Translation is aligned by line, not by arithmetic.** Splitting a translated
  block in proportion to duration looks reasonable and is wrong: Indic word order
  moves clauses across the seam, so a segment ends up saying the wrong half of a
  sentence. The model preserves paragraph structure, so each block is sent with
  one segment per line and comes back aligned. Proportional splitting remains as
  a labelled fallback.
- **The documented `/translate` route is not always deployed.** On this host it
  returns FastAPI's `{"detail":"Not Found"}` for every key. The client detects
  the 404 once and switches to `POST /v1/chat/completions` for the rest of the
  run, using the documented prompt (`Translate the following text into Hindi:`).
- **The bed is gated, not replaced.** Music under speech is lost with the speech;
  everything else survives. Source separation is the upgrade path and is named as
  such rather than silently skipped.
- **The LLM only shortens English.** It is called for one decision — a segment
  that still overruns at 1.20× — and its answer is rejected if it contains any
  non-Latin character, drops a number, or drops a glossary term. Without
  `LLM_*` credentials the step is skipped and the ladder ends at a logged `miss`.
- **Cache before you think.** Every model response is keyed by a SHA-256 of the
  exact request. A rerun, a retry, or a second language hitting the same audio
  costs nothing.
- **Fail honestly.** A missing key, a rejected key or an unbuilt stage produces a
  clear message, a partial report, and a non-zero exit code — never invented
  output.

## Limitations

- Music-only stretches can be mistaken for speech by an energy VAD; a
  speech/music classifier is deliberately out of scope here.
- A single-speaker recording is the model's training assumption. There is no
  diarization, so a multi-speaker video is narrated by **one consistent voice per
  language** rather than swapping voices per speaker. That is a deliberate
  trade-off: a narrator who changes mid-sentence is worse than one who never
  matches the original. Pitch clustering is a future option.
- When a translation still runs past its slot after 1.20x, it is placed anyway
  and logged as a `miss` with the overrun. Condensing the **English** with an LLM
  and re-translating is the intended fix and is not wired up yet, because it
  needs `LLM_*` credentials.
- `max_output_tokens` is unavailable on the chat transport, so translation length
  cannot be capped directly; only the speed/spill ladder and the optional LLM
  condensing step control it.
- Music that plays *under* speech is removed with the speech. Separating the two
  needs a source-separation model, which is an optional upgrade, not enabled by
  default.
- Runtime depends on the account's request quota. At the rate this deployment
  enforced during development, a three-language run is closer to the 30-minute
  target than the cost figures suggest.
- **Subtitles are an embedded track, not burned in.** VLC, IINA and mpv show
  them; QuickTime Player and browsers generally do not, because they do not
  render MP4 `mov_text`. Use `--burn-subtitles` (needs libass — the Dockerfile
  has it) if the captions must be visible in any player, and the sidecar `.srt`
  always works if your player prefers files.

## Tests

```bash
PATH=/opt/homebrew/bin:$PATH pytest -q
```

254 tests, no network needed. They cover VAD boundaries and tail padding, the
28 s chunk cap, quietest-point cutting, sample-exact slicing, block building and
line alignment, the duration ladder, digit-script-aware CER, TTS QA, the LLM
answer guard, pacing and adaptive concurrency, cache hits, cost arithmetic, voice
selection, the audio gate, loudness normalisation, muxing, subtitle timings, and
a 30-second end-to-end run of the real CLI — including one that transcribes,
translates, speaks, mixes, muxes and writes subtitles with all three models
stubbed. Set `BODHAN_TRANSCRIBE_KEY` and drop a clip at `sample_data/smoke.wav`
to also run the live contract test.

## Results

Demo lesson: *Class 9 Mathematics, Chapter 6, Period 1* — 5 min 29 s, one
speaker, English, 72 speech segments covering 68.9% of the file.

| Metric | Result | Target |
|---|---|---|
| Segments dubbed (Hindi) | 72 / 72 (100%) | every segment or a logged skip |
| Mean back-transcription CER | 0.071 | reported |
| Max time-stretch | 1.20× | 0.85–1.20× |
| Speed violations | 0 | 0 |
| Loudness | −16.07 LUFS | −16 ±1 |
| Duration drift | +0 ms | ±100 ms |
| Alignment | 11 blocks line-aligned, 2 proportional | — |
| Runtime, Hindi | ~14 min at 2 requests/segment (see note) | — |
| Cost, Hindi | ₹1.97 for 5.5 min | — |

Two notes on those numbers, because they are not all good news:

* **Runtime is dominated by the account's rate limit.** A full Hindi run makes
  ~144 model calls, and the deployed quota rejected runs at about 26 calls a
  minute — well under the advertised 120. The adaptive concurrency cap and 12 s
  cooldown cap in `ratelimit.py` exist because of that; the numbers above were
  measured before both landed.
* **Two blocks fell back to proportional splitting** rather than line alignment.
  The fallback is labelled per block and logged with expected vs received line
  counts.

## Media

Media files stay out of git. Everything below is linked, not committed.

**Input** — *NCERT Class 9 Mathematics, Chapter 6, Period 1* (English, 5 min 29 s).

**Outputs** — fill these in before submitting:

| Language | Dubbed video | Subtitles | Report |
|---|---|---|---|
| Hindi | `<link>` | `<link>` | `<link>` |
| Tamil | `<link>` | `<link>` | `<link>` |
| Bengali | `<link>` | `<link>` | `<link>` |

**Demo recording** — a walkthrough of five minutes or less: `<link>`

To reproduce locally, put any clip at `demo/input.mp4` (git-ignored) and run the
command from [Run](#run).

## Notes

Speech and narration are generated by Indic-Speak from a fixed voice library —
voices are not cloned from the original speaker. Output is AI-dubbed.

See [docs/plan.md](docs/plan.md) for the full build plan and the phase-by-phase
task list.
