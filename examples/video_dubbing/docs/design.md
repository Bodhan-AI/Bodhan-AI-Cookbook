# Design notes

The assignment asks for orchestration judgment: what should be a model call, what
should be deterministic code, and how the pipeline proves its own output is
sound. This is the reasoning behind each choice.

## Where the LLM is used, and why

| Decision | What the LLM does | Fallback if it fails |
|---|---|---|
| **Condensing English when a segment overruns its slot** | Rewrites one English sentence to fit a character budget, keeping numbers, names and glossary terms | The duration ladder ends in `spill` or a logged `miss`; nothing else changes |
| **Glossary term protection** (same call) | The glossary is passed as terms that must survive the rewrite | Terms are omitted from the instruction only if no glossary is supplied |

That is deliberate and narrow. Three things it explicitly does **not** do:

* **It never writes or edits Indian-language text.** The output is rejected if it
  contains any character outside Latin script, so a model that answers in Hindi
  cannot leak Indic text into the dub. Every Indic string still comes from
  Indic-Translate.
* **It never judges a translation.** The clean way to catch a bad translation is
  a back-translation round trip, which this pipeline already does deterministically
  with `indic-transcribe` and character error rate. Adding an LLM judge would
  introduce a second, uncalibrated opinion with no ground truth.
* **It never decides segmentation.** Durations, chunk boundaries and placement are
  arithmetic; a model would make them non-reproducible.

Without `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` the refinement step is
skipped and logged, and the pipeline is fully deterministic.

## How sync is achieved and checked

Sync is an engineering problem here, not a model problem, because the hosted ASR
returns no timestamps.

1. **Timestamps come from the cuts.** The VAD finds speech, the chunker caps at
   28 s and cuts at the quietest internal frame, and each chunk's start and end
   *are* the segment's start and end. They are sample-accurate by construction.
2. **TTS silence is trimmed.** A generated clip typically opens with 100–300 ms of
   quiet, which alone would consume the entire ±300 ms budget. Measured mean
   trimmed: ~40 ms per clip, but up to ~250 ms on some.
3. **Placement is exact.** Clips are written at `round(start × sample_rate)`, so
   the offset is zero by construction rather than by luck.
4. **Overruns are bounded.** `atempo` never exceeds 1.20, spill is limited to
   800 ms, and anything worse is recorded as a `miss` with the number of
   milliseconds it bleeds past its slot.
5. **Every number is reported.** `report.json` carries the max tempo, the count of
   speed violations, the number of misses, and the measured mean of trimmed
   silence. Nothing is asserted that was not measured.

## How output quality is checked

| Check | Method | Why this method |
|---|---|---|
| Dubbed speech matches the requested text | Back-transcribe every clip with `indic-transcribe`, compute character error rate against the translation | It is the only independent view of the audio, and it is the check the brief asks for |
| TTS loops or drops content | n-gram repetition detection and a truncation ratio on the back-transcript | Cheap, deterministic, and catches the two documented failure modes |
| Translation actually covers the source | One translated line per source segment, or a labelled proportional fallback | Catches a dropped segment without a second model |
| Numbers survive | Digit extraction compared across source and translation | Deterministic, no judgement needed |
| Terminology is consistent | Glossary terms located in the source and searched for in the translation, tolerating one inflectional character | The API has no glossary parameter, so this is verification, not enforcement |
| Structure is preserved | Segment count, block count, alignment mode recorded per block | A silent fallback would otherwise be invisible |
| Loudness | Two-pass `loudnorm` measurement, target −16 LUFS ±1 | Matches the stated target exactly |
| Duration | `ffprobe` on the finished MP4 against the input | Catches mux drift of a few milliseconds |

Two honest caveats:

* **CER is not reliable on very short clips.** Below twelve characters a single
  glyph moves CER by 20%+. Those clips are judged on truncation and repetition
  only, and are not retried, because there is nothing to split.
* **The back-transcriber has its own error rate.** A high CER is treated as a
  reason to retry, not as proof the TTS misbehaved. If a retry produces the same
  audio and the same transcript, the segment is accepted with `qa: fail` and the
  reason recorded, rather than being retried forever.

## How failures are recovered

The order is deliberate: cheapest and most likely first.

```
429              → pacer cooldown (2 s → 12 s) + drop a concurrency slot
5xx / transport  → exponential backoff, 4 attempts
TTS repeat/drop  → retry once → split in half and rejoin → keep the better take
Segment too long → atempo ≤ 1.20 → LLM condense → spill ≤ 800 ms → log a miss
Chunk fails      → log the span, keep the transcript, exit 3 at the end
Language fails   → mark it, continue the others, exit 3
```

Every path writes the artefacts it has. A run that exits 3 still produces a
transcript, a dub, a video and a report where possible.

## Trade-offs taken deliberately

| Choice | Cost | Why |
|---|---|---|
| One voice per language, no diarization | A two-speaker video gets one narrator | A narrator that changes mid-sentence is worse than one that never matches the original. Pitch clustering is the upgrade path |
| Mute speech, keep the rest | Music under speech is lost with the speech | Source separation is a 2 GB model download and a Docker build hazard; it is named as the upgrade, not hidden |
| Proportional split as a fallback | Occasionally misaligned text at a block boundary | It only fires when the model does not return one line per segment, and it is labelled per block |
| `-c:v copy` with transcode fallback | A WebM with VP9 is re-encoded | Correctness over speed, without forcing every input through a re-encode |
| ChatGPT-style LLM optional | Refinement is off without credentials | The deterministic ladder is the contract; the LLM is an improvement, not a dependency |
| Half the advertised RPM, adaptive concurrency | Slower than the console promises | Two live runs proved the advertised numbers are not what the account allows |

## What I would do next

1. **Speaker-aware voices.** Cluster segments by median F0, map two clusters to
   the language's female and male voice, and keep each voice consistent. The
   `voices.yaml` hook already exists.
2. **Source separation behind a flag.** `--keep-music` using a vocal/instrumental
   split, so music under speech survives. This is the one quality gap a reviewer
   will hear immediately.
3. **Sample QA instead of exhaustive QA when the quota is tight.** Back-transcribing
   every clip doubles the request count; a stratified 30% sample plus all retries
   would keep the signal and cut the run time materially.
4. **Translate in parallel across languages.** Languages are independent and the
   cache already deduplicates; the only reason they are sequential is that the
   rate limits were discovered late.
