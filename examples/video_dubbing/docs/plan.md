# Build plan

English video → dubbed MP4 + subtitles in each requested Indian language.

Three Bodhan models, used in this order:

| Stage | Model | Endpoint | Why this model |
|---|---|---|---|
| 1. Transcribe | `indic-transcribe` | `POST /v1/audio/transcriptions` | the only permitted ASR |
| 2. Translate | `indic-translate` | `POST /translate` | the only permitted MT |
| 3. Speak | `indic-speak` | `POST /v1/audio/speech` | the only permitted TTS |

A companion LLM (`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`) is the *brain*: it
plans, judges and rewrites the **English** side only. It never writes or edits
Indian-language text. Every Indic string in an output traces back to a Bodhan model.

---

## The three phases

```
Phase 1  TRANSCRIBE          Phase 2  TRANSLATE + SPEAK          Phase 3  ASSEMBLE + SHIP
───────────────────          ─────────────────────────           ────────────────────────
video ──► 16 kHz mono        segments ──► merge ──► translate     audio bed + mix
      ──► VAD regions              ──► duration budget          ──► loudnorm −16 LUFS
      ──► sentence-safe chunks     ──► TTS per segment          ──► mux (-c:v copy)
      ──► indic-transcribe         ──► back-transcribe QA       ──► SRT + report.json
      ──► transcript.en.json       ──► time-fit [0.85, 1.20]    ──► parallel langs, exit codes
```

Each phase is independently runnable and independently testable. A phase only
depends on the artifacts of the phase before it, which is also what makes runs
resumable: every artifact is keyed by its input hash.

---

## Phase 1 — Transcribe

**Goal:** `out/transcript.en.json` with accurate segment timestamps, for any
MP4 / MOV / MKV / WebM between 1 and 15 minutes.

**The key design decision.** The hosted `indic-transcribe` endpoint returns only
`{"text": ...}` — there are no word or segment timestamps (the Cookbook's own
speech-to-text tutorial shows the same shape). The assignment FAQ anticipates
this: *"Work from Indic-Transcribe output, voice-activity detection and chunk
boundaries."*

So timestamps come from **where we cut the audio**, not from the model:

1. Decode to 16 kHz mono PCM.
2. Detect speech with an energy VAD (adaptive noise floor + hysteresis).
3. Merge regions separated by < 300 ms, drop regions < 200 ms of real speech.
4. Split anything over 28 s at its quietest internal point — never mid-word.
5. Transcribe each chunk on its own, in parallel.

Every segment therefore carries a start and an end that are sample-accurate to
the cut, which is comfortably inside the ±300 ms sync target. The cost is one
request per utterance instead of one per 30 s window; audio is billed by the
second either way, so this is free in money and only costs latency, which the
worker pool hides.

**Todo**

- [x] Scaffold the recipe folder, pinned requirements, `.env.example`, `.gitignore`
- [x] `media.py` — ffmpeg/ffprobe discovery, probe, video → 16 kHz mono WAV, WAV read/write/slice
- [x] `vad.py` — frame energy, adaptive threshold, hysteresis, merge, pad
- [x] `chunker.py` — sentence-safe chunk planning under a 28 s cap
- [x] `client.py` — Bodhan HTTP client: retry/backoff, 429 + `Retry-After`, concurrency cap
- [x] `ratelimit.py` — adaptive concurrency (AIMD): starts at 1, halves on a 429, grows on success
- [x] Interrupt handling — Ctrl+C releases workers and still writes a partial transcript
- [x] `cache.py` — content-addressed cache, so a rerun never pays twice
- [x] `cost.py` — rupee meter per stage
- [x] `log.py` — structured JSON logs per stage and per segment
- [x] `asr.py` — orchestrate extract → VAD → chunks → transcribe → `transcript.en.json`
- [x] `cli.py` — the required interface, plus `--stage`
- [x] `tools/probe_api.py` — dump the raw ASR response, to confirm whether a
      timestamp-bearing `response_format` exists
- [x] Tests: VAD boundaries, chunk cap, WAV slice exactness, text normalisation,
      retry, cache-hit, cost maths, limiter behaviour
- [x] Run against the real demo video with a live key

**What the live run proved**

- The hosted ASR returns `text` and nothing else. Across 39 cached responses the
  only key present was `text`, so VAD-derived boundaries are not a fallback —
  they are the only source of timing available.
- A 5.5 minute lesson becomes 72 chunks. The console advertises **RPM 120** for
  `indic-transcribe`, which is a requests-per-minute quota over a rolling
  60-second window — not a concurrency cap. Firing 72 requests in five seconds
  is ~860 rpm, so the server rejected almost everything. Worse, the first
  implementation waited a fixed 1.5 s on a 429, which can never outlast a 60 s
  window: the retries themselves kept the window saturated. The run only
  recovered 60 s later, when the original burst aged out.
- The fix is to pace *request starts* and to back off exponentially. Both are in
  `ratelimit.py`; the concurrency semaphore is now only an upper bound on work in
  flight.
- **The advertised number is an upper bound, not a target.** Starting at 85% of
  120 still produced a burst of 429s — sixteen uncached calls in eight seconds,
  about 102/min, was enough to trip it. The working rate now starts at **50%**
  of the ceiling.
- **A 429 must not consume a chunk's error budget.** The second full-length run
  lost two chunks because a busy minute exhausted their four attempts. Throttling
  is now counted separately (up to eight waits) and waited out by the shared
  pacer, so a saturated window delays work instead of destroying it.
- **Recovery is slow on purpose.** Resetting the backoff on a single success made
  the pacer oscillate: climb to the ceiling, burst, collapse to 18 rpm, wait 25 s,
  climb again. The rate now recovers by 2 rpm every 30 successes.
- **A partial transcript must not throw away the dub.** Two missing chunks out of
  72 aborted the entire run before any speech was generated. The pipeline now
  continues with the segments it has, produces the artefacts, and exits non-zero
  at the end.
- **The real limit is a concurrency cap, not a rate cap.** A full run was rejected
  at roughly **26 calls per minute** — far under the advertised 120 — which means
  the working constraint is how many requests are *in flight*, not how many start
  per minute. Concurrency is now adaptive too: start at 2, drop a slot on every
  rejection, add one back every 10 successes.
- **Escalating cooldowns are dangerous once the rate is low.** With the rate
  pegged at 10 rpm, a 60-second cooldown fired thirteen times and turned a
  three-minute job into 14.5 minutes. Cooldowns are now capped at 12 seconds and
  the rate floor is 30 rpm; the concurrency gate does the protecting instead.
- **CER is meaningless on very short clips.** Five of 72 segments "failed" QA and
  were retried four times each — every one of them under twelve characters, where
  a single glyph moves CER by 20%+. Short clips are now judged on truncation and
  repetition only, and are not retried at all: there is nothing to split.
- The advertised `x-ratelimit-*` response headers were **not present** on this
  deployment, so the RPM is taken from the console and exposed as
  `BODHAN_DUB_RPM` rather than discovered at runtime.
- A partial run must still produce artifacts. An interrupt now writes the
  transcript for whatever finished and marks the rest `interrupted`.
- **Segmentation is a transcription-quality problem, not only a sync problem.**
  With a 300 ms bridge the word "collinear" was cut at a ~400 ms gap before its
  final "r", and the chunk came back as "Collinea" — a word that would then be
  translated wrongly. Raising the bridge to 450 ms produced "First word
  collinear" on the same audio. Fewer, larger segments also mean fewer one-word
  fragments for the translator.
- ASR errors still reach the dub: on the same slice "collinear" was heard as
  "collected". Translation will faithfully carry that error, so Phase 2 reports
  what it is working from rather than pretending the source is clean.

**Exit criteria:** `python -m bodhan_dub --input demo.mp4 --langs hi --out out --stage asr`
writes `transcript.en.json`, `out.report.json`, and a log, with no network call
repeated on a second run.

---

## Phase 2 — Translate and speak

**Goal:** for one language, audio that starts within ±300 ms of every source
segment, at a natural speed, containing what the translation says.

**Todo**

- [x] `translate.py` — batch adjacent segments into one block so each request has
      context, then send it with **one line per segment**
- [x] line-aligned translation: the model preserves line structure, so the reply
      comes back as one translated line per segment — per-segment text *and*
      block context, with no time redistribution at all
- [x] proportional split as the fallback when the line count does not match,
      recorded as `alignment: "proportional"` so the degradation is visible
- [x] `glossary.py` — load `glossary.yaml`; verify one source term maps to one
      target rendering; report inconsistencies (Indic-Translate has no
      glossary-input parameter, so this is verify-not-force)
- [x] `duration.py` — predict TTS length from a per-language characters-per-second
      rate, calibrated from the clips the run has already synthesised
- [x] duration ladder: `atempo` ≤ 1.20 → spill into following silence ≤ 800 ms →
      accept and log a sync miss
- [ ] LLM condenses the **English** when a segment still overruns (needs
      `LLM_*`; without it the ladder stops honestly at a logged miss)
- [x] `speak.py` — `POST /v1/audio/speech`; voice from `voices.yaml`, else the
      language's default; `style="educational lecture"`; one call per segment
- [x] leading-silence trimming — TTS opens with 100-300 ms of quiet, which alone
      would consume the sync budget
- [x] `qa.py` — back-transcribe every clip: CER after Unicode/digit normalisation,
      repeated-n-gram detection, truncation ratio
- [x] recovery ladder: retry once → split at a sentence boundary and concatenate →
      keep the better take and mark `qa: fail` with a reason
- [x] `timeline.py` — place clips on a silent timeline at source start times
- [x] `segments.<lang>.json` — source, translation, durations, speed factor, QA
- [x] `--code-mixed` — maps to Indic-Translate's `codemix` script
- [x] parallel segment synthesis, bounded by each model's pacer
- [ ] speaker clustering — a single consistent voice per language for now, which
      is the honest choice without diarization

**What the live run proved**

- **Line-aligned translation is much better than proportional splitting.** With
  proportional splits, Hindi word order put "कोण।" (belonging to the previous
  sentence) at the start of the next segment, and shifted a whole clause forward.
  Sending the block with one line per segment returned one correct line per
  segment, every time, on real audio.
- **`POST /translate` is not deployed on this host.** It returns FastAPI's
  `{"detail":"Not Found"}` for every key. `POST /v1/chat/completions` with the
  documented prompt (`Translate the following text into Hindi:`) works, and the
  client falls back to it automatically after a 404.
- **The target must be named, not coded.** The model is prompted with a language
  name; sending `hi` as a field is not the documented contract.
- **Per-model keys are enforced by the gateway**, which confirms the
  per-model-key design: a transcribe key used on `indic-translate` returns 403
  naming the one model it may access.
- **Parallel synthesis matters.** Seven segments went from 64 s to 10.8 s once
  segments were processed concurrently, and cost fell from ₹0.31 to ₹0.19
  because aligned translations are shorter at the seams.
- The model renders "straight angle" as "समकोण" (right angle). A wrong but
  fluent term is exactly what the glossary check exists to catch.

**Exit criteria:** Hindi output plays in sync over the demo clip, every segment has
a CER and a speed factor in the JSON, and an injected TTS loop is caught and fixed
without human intervention.

---

## Phase 3 — Assemble and ship

**Goal:** a valid MP4 of exactly the input duration, plus subtitles, plus a report
an engineer can audit.

**Todo**

- [x] `bed.py` — mute exactly the VAD speech intervals (10 ms fades), keep music
      and effects
- [ ] optional `--keep-music` source separation — deliberately out of scope, named
      as the upgrade path in the limitations
- [x] `mix.py` — mix with the bed 6 dB down, two-pass `loudnorm` to −16 LUFS ±1,
      measured before and after
- [x] `mux.py` — `-c:v copy` with a transcode fallback, `-t` to the input
      duration, then re-probe and compare (±100 ms)
- [x] `subtitles.py` — SRT per language, two-line wrapping, 0.8 s minimum cue,
      no overlap
- [x] `report.json` — per-language status, duration and loudness checks,
      per-segment sync, retries, CER, latency, cost, and every target's real value
- [ ] parallel languages — they run sequentially; the cache already deduplicates,
      and the rate limits were discovered late
- [x] LLM hardening — one decision, Latin-only output guard, number and glossary
      guards, deterministic fallback
- [x] Dockerfile + one-command setup
- [x] `docs/architecture.md`, `docs/workflow.md` (Mermaid), `docs/design.md`
- [x] README with measured results, limitations and a cost table
- [ ] Record the ≤5 min demo; open the PR

**What the live run proved**

- **Loudness lands on target.** Two-pass `loudnorm` measured −16.07 LUFS on real
  speech, inside the ±1 target, while the naive mix was 12 dB off.
- **`-c:v copy` holds the length.** The finished MP4 matched the input container
  to 0 ms, so the mux adds no drift of its own.
- **The input's container duration is not its audio duration.** The demo file is
  329.267 s as a container but 327.267 s of audio. Using the audio length would
  have trimmed two seconds off the picture, so the timeline is built to the
  container duration and the transcribe statistics are reported separately.

**Exit criteria:** the required command, unmodified, dubs a video it has never seen
into a language that was never demoed.

---

## Engineering rules held throughout

- **Cache first.** Content-addressed on the exact request. A rerun costs ₹0.
- **Never fabricate.** A stage that cannot run reports `failed` and says why.
- **Bound everything.** Max attempts, max in-flight requests, max backoff.
- **Every claim traceable.** Each number in `report.json` comes from a log line.
- **No demo special-casing.** Any behaviour keyed to a specific file, speaker or
  sentence is a bug.
