# Design notes

## Principles

1. **Bodhan models do all language work.** Transcription, translation and speech are only ever called through `bodhan.py`. The LLM sees and returns English only; output containing Indic script is rejected in `llm.py`.
2. **Every LLM decision has a deterministic fallback**, so the pipeline runs without an LLM and a bad LLM reply never breaks a run.
3. **Check, don't trust.** Each dubbed line is transcribed back and judged before it is used.
4. **Degrade, don't stop.** A failing line, language or API call is logged and reported; everything else continues.
5. **Cache every paid call**, so reruns and UI edits cost only what changed.

## Flow chart with retries, checks and decisions

```mermaid
flowchart TD
  S([Start]) --> P{Input valid?<br/>has audio}
  P -- no --> F1[Write report with error, exit 2]
  P -- yes --> VAD[VAD spans, drop flat energy]
  VAD --> CH[Chunks <= 25 s cut at pauses]
  CH --> T[Indic-Transcribe each chunk]
  T --> H{Empty or implausible text?}
  H -- yes --> SK[Skip, log reason: never invent narration]
  H -- no --> GR[LLM groups sentences]
  GR --> V{Valid partition?}
  V -- no --> HG[Heuristic grouping]
  V -- yes --> GL
  HG --> GL[Glossary + speakers] --> LANG

  subgraph LANG[Per language, parallel, isolated failures]
    TR[Indic-Translate with glossary] --> TT[Indic-Speak in short pieces]
    TT --> FIT{Fit ladder}
    FIT -- fits / borrow pause / start early / stretch to 1.2x --> BK
    FIT -- still too long --> CO{LLM available<br/>and < 2 rounds?}
    CO -- yes --> SH[LLM shortens English] --> TR
    CO -- no --> BK
    BK[Back-transcribe the dub] --> QA{CER, repeat, omission}
    QA -- bad and < 2 retries --> RT[Re-voice, finer pieces] --> FIT
    QA -- ok --> OK[Keep]
    QA -- bad, retries used --> DG[Keep best, mark degraded]
  end
  OK & DG --> MIX[Gate English speech, add dub, -16 LUFS]
  MIX --> OUT[Mux MP4, SRT, segments.json]
  OUT --> RP[report.json: targets, misses and why, cost]
```

## Where each hard part is handled

| Hard part | Approach | Code |
|---|---|---|
| Duration drift | A ladder, cheapest and most natural first: fit, borrow the pause after, start up to 250 ms early, stretch at most 1.2x, then shorten the English and re-translate. Never more than 1.2x. A miss is reported with its reason. | `timing.py`, `pipeline.LangRun.segment` |
| Window limits | Chunks cut at pauses, at most 25 s, never inside speech unless unavoidable (flagged). Sentences are rebuilt from chunks before translation. TTS gets one sentence at a time. | `segmentation.py`, `textutil.split_for_tts` |
| Context and terminology | Whole sentences are translated, not fragments. Glossary from you and the LLM; your renderings are substituted before translation; pinned terms and numbers are checked after and flagged. | `llm.build_glossary`, `LangRun.translate` |
| Model misbehaviour | Back-transcribe every dub. Repeats, missing tails and large mismatches trigger a retry with finer text pieces; best attempt kept; optional LLM judge for borderline lines. | `qa.py`, retry loop |
| Speakers | No diarization available, so segments are grouped by median pitch into at most two speakers, each mapped to the language's own male or female voice, consistent for the whole video. Overridable. | `audio.assign_speakers`, `pick_voice` |
| Silence and music | Adaptive VAD relative to the clip; flat-energy spans rejected; empty or implausible transcripts skipped with a reason; the dub is only ever placed at a transcribed sentence. The original background is kept wherever nobody speaks. | `segmentation.energy_spans`, `audio.render_mix` |

## Where the LLM decides, and the fallback

| Decision | Input | Output | Fallback |
|---|---|---|---|
| Group fragments into sentences | numbered English fragments with times | partition of indices | punctuation and pause heuristic; invalid partitions rejected |
| Glossary | English transcript | up to 40 terms that occur in it | empty glossary (user terms still apply) |
| Shorten English | one sentence, target ratio, protected terms | shorter English | cap speed at 1.2x, mark degraded, report |
| Judge a borderline dub | English, translation, back-transcript | ok or not | trust the error-rate metrics |

## Sync and quality checks

- Placement: each dubbed line starts at its source start, or at most 250 ms earlier when it needs room. Offsets are reported per line; the target is within 300 ms.
- Overrun: a clip may not run into the next sentence; it is trimmed at the next start and noted.
- Length: the output is muxed with `-t <input duration>` and measured afterwards (target within 100 ms).
- Loudness: speech is measured and normalized to -16 LUFS, with a peak ceiling of about -1 dBFS; the measured value is reported.
- Fidelity: character error rate of the back-transcript against the translation, per segment and mean.

## Failure recovery

| Failure | Recovery |
|---|---|
| 429 / 5xx / timeout | exponential backoff honoring `Retry-After`, up to 5 attempts, concurrency-limited; counted in the report |
| Translation truncated | split the sentence and translate the halves; never cached |
| Repeated / dropped speech | re-voice with finer pieces, keep the best attempt |
| One segment fails | other segments continue; it is listed as failed with the error |
| One language fails | other languages continue; exit code 1; report and partial outputs written |
| Wrong key / endpoint (401, 403, 404) | preflight probe of translate and speak before any transcription spend; fails in seconds with a plain-English hint, exit code 2 |
| Source analysis fails (no audio, bad file) | report with the error, exit code 2 |
| Re-run | all paid calls are cached; unchanged lines cost nothing |

## Web studio

A thin FastAPI app plus one static page. It uploads a file, starts `run_pipeline`, polls `run.log.jsonl` for progress, and serves the segments, report and media the pipeline wrote. Edits are saved as `out/<lang>/overrides.json` (English, target text, voice, style, speed) and the language is re-run; the cache makes this cost only the changed line. Overrides are whitelisted and speed is clamped to 0.85-1.2x on the server.

## On-screen text (Indic-OCR)

| Question | Decision |
|---|---|
| Which frames? | One per interval (default 2 s, at most 40), taken mid-interval. A frame within 8 of 256 average-hash bits of the previous one reuses its result, so static slides cost one call. Identical images are also answered by the on-disk cache. |
| Which text? | Blocks with real text and confidence of at least 0.4. Skipped and counted: footers and page numbers, pictures and logos, formulas, tables, text already in an Indian script, fewer than two letters, longer than 400 characters. |
| How long is it shown? | The same text (similarity 0.85 or more, box overlap 0.3 or more) on consecutive frames is one item: start is the beginning of its first interval, end the end of its last. At most 40 items per video; the longest-lived and largest are kept. |
| Who writes the Indian-language text? | Only Indic-Translate (glossary substitution and number checks apply). The LLM is not involved. A hand-typed text from the editor wins. |
| How is it drawn? | A PNG per item: a box in the median colour of the original text box, the translation in a script-matched Noto font through Raqm, or through HarfBuzz (`uharfbuzz`) where Pillow lacks Raqm (correct conjuncts either way), size from the box area and character count, shrunk before the box grows, kept inside the frame. ffmpeg overlays each PNG with `enable='between(t,start,end)'`. |
| Failure | No key, an API error on every frame, or an ffmpeg overlay error never fails the dub: it is logged, listed in `report.json` and the video is muxed without overlays. One failing frame or text is skipped. |
| Resumable | `work/onscreen.json` is keyed by video hash, interval and frame limit; edits in the web studio change only overrides (`o<id>` keys), so a rerun reuses everything. |

## Per-sentence voice controls

| Control | Behaviour |
|---|---|
| Style / emotion | Sent as `instructions: {"lang", "style"}` to Indic-Speak. `Neutral` is stored as `none` so it can override a project-wide style. The style used is recorded per sentence and shown as a badge. |
| Edits | An edit is merged into the sentence's existing overrides, so a style-only change keeps hand-typed text and speed. `unset` removes a field; new English without typed text drops the old typed text so it is re-translated. |
| Hear this voice and style | One cached Indic-Speak call, no pipeline run, for comparing voices and styles. |
| Use my own voice | Recorded in the browser or uploaded, converted to mono 24 kHz WAV (0.3-60 s, 25 MB, not silent), stored under `out/<lang>/custom/`. The pipeline uses it instead of Indic-Speak for that sentence, with the same fit ladder (speed at most 1.2x), no QA retry loop. Removing it restores the AI voice. |

## What I would do next

Calibrate QA thresholds on real runs; add source separation to keep music under speech; speaker diarization from an allowed source; Indic-OCR for on-screen text; reading-speed subtitle splitting; per-job isolation and auth for the web studio.


## Loudness and LLM rate limits (found in a real run)

- **Loudness.** TTS output is peaky, so applying the gain for -16 LUFS and then scaling the whole track down to keep peaks under -1 dBFS landed a 20 s clip at -20 LUFS (target -16 +/- 1). The mix now applies the gain, runs a look-ahead limiter (`alimiter`, no auto level), measures again and corrects the gain for what the limiter removed, up to four passes. A test with a high crest-factor signal reaches -16.1 LUFS where the old method gave -27.
- **LLM 429s.** The first full run showed `llm_failed ... 429` for sentence grouping, the glossary and most condense/judge decisions, so those decisions silently used their fallbacks. LLM calls now go through a concurrency cap (`LLM_CONCURRENCY`) and retry on 429/5xx, waiting for `Retry-After` or backing off exponentially (`LLM_MAX_ATTEMPTS`, `LLM_BACKOFF`), and only then fall back. The fallback is still there; it is the last resort, not the first reaction.
- **Latency numbers.** `seconds` in `api_call` logs and in the cost meter is now the call itself; time queued behind a rate limit is logged separately as `queued_s`.
- **A dead LLM must not slow the run down.** In the second real run every LLM call still got HTTP 429 after five retries (the log showed only `Error code: 429`, with no body, so a rate limit could not be told from an exhausted quota). Waiting 62 s per decision, many times over, added minutes and gained nothing. The LLM wrapper now logs what the provider actually said, and a circuit breaker (`LLM_BREAKER`, default 3) switches the LLM off for the rest of the run after that many calls in a row exhaust their retries. Every decision then uses its documented fallback at once, and `report.json` records `llm.switched_off`, `llm.failed_calls` and `llm.last_error`, so a run that did not use the LLM says so instead of looking like one that did.
