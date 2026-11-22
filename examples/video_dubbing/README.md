# Video dubbing

> Dub an English video into Indian languages: synced AI speech, subtitles, and a QA report that explains every miss.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak
**Languages shown:** Hindi, Tamil, Bengali (any of the 22 Indic-Speak languages via `--langs`)
**Format:** CLI (`python -m bodhan_dub`)

## What it does

Give it one English video (MP4, MOV, MKV or WebM, 1–15 minutes, one or more speakers). For every requested language it writes a dubbed MP4, a subtitle file and a per-segment JSON trail, plus one `report.json` that scores the run against the quality targets: sync, stretch, coverage, back-transcription CER, length and loudness.

All speech recognition, translation and speech synthesis run on Bodhan's Indic models. A general-purpose LLM is the orchestrator's "brain", and **it only ever reads and writes English**. Every Indian-language word comes from Indic-Translate. Output is labelled **AI-dubbed** (in the MP4 metadata and the subtitles); the voices come from Indic-Speak's fixed library and do not imitate the speaker.

Design notes, the architecture diagram and the workflow flow chart are in **[docs/DESIGN.md](docs/DESIGN.md)**.

## Setup

System dependency: **ffmpeg** with ffprobe on `PATH` (`brew install ffmpeg`, `apt-get install ffmpeg`). Python 3.12+.

```bash
cd examples/video_dubbing
uv sync                      # or: python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -e .
source .venv/bin/activate
cp .env.example .env         # fill in the Bodhan keys and an LLM endpoint
python scripts/smoke_api.py  # one tiny live call per API, well under ₹0.10
```

Or with Docker (`docker build -t bodhan-dub .`, usage at the top of the [Dockerfile](Dockerfile)).

| Variable | Purpose |
|---|---|
| `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY` | per-model Bodhan keys (any left empty falls back to `BODHAN_API_KEY`, which the Cookbook template declares) |
| `BODHAN_BASE_URL` | defaults to `https://api.bodhan.ai` (a trailing `/v1` is accepted) |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | any OpenAI-compatible chat endpoint |
| `LLM_REASONING_EFFORT`, `LLM_MAX_TOKENS` | optional; sent only when set and dropped automatically if the provider rejects them |

## Run

```bash
python -m bodhan_dub --input path/to/video.mp4 --langs hi,ta,bn --out ./out \
  [--glossary config/glossary.example.yaml] [--voices config/voices.example.yaml] [--code-mixed]
```

| Flag | Meaning |
|---|---|
| `--glossary` | YAML of terms to keep consistent: a `terms:` list, a bare list, or `term: value` pairs. `keep: true` means never translate; expected renderings (`linear pair: {hi: …}`) are checked and reported, never inserted. Unusable entries become warnings in `report.json` (see [config/glossary.example.yaml](config/glossary.example.yaml)) |
| `--voices` | YAML of voices per language: a name, a list, `{male, female}`, or `default:` for every language, plus an optional `style`. Names are case-insensitive; unusable entries become warnings (see [config/voices.example.yaml](config/voices.example.yaml)) |
| `--code-mixed` | code-mixed targets such as Hinglish (English technical terms inside Indian-language speech) |
| `--ascii-digits` | send digits to Indic-Speak as written; it then reads numbers in English (by default they are sent in native digits and spoken in the language) |
| `--max-parallel` | concurrent Bodhan calls (default 4) |
| `--cache-dir` | model-call cache, reusable across runs (default `<out>/.cache`) |

**Exit codes:** `0` every language dubbed, `1` at least one language failed (the others still finish, and partial outputs and the report are written), `2` bad input or setup.

**Logs:** readable lines on stderr; one JSON object per event in `out/run.log`, with `stage`, `lang` and `unit` fields, so any line of the dub can be traced (`jq 'select(.unit=="u0007")' out/run.log`).

### Outputs

```
out/
  report.json            # per-language status, stage timings, retries, QA vs targets, misses, cost
  transcript.en.json     # English dub units with start/end (+ raw ASR segments, for audit)
  glossary.en.json       # terms kept consistent, with their source (LLM or user)
  run.log                # structured JSON logs
  hi/
    video.hi.mp4         # dubbed video (video stream copied; AAC audio tagged as AI dub)
    subtitles.hi.srt     # one cue per spoken sentence, timed from the real audio
    segments.hi.json     # per unit: source, translation candidates, takes, CER, tempo, sync
  ta/ ...
```

## How it works

1. **Find speech.** Silero VAD (via the torch-free `silero-vad-lite`) marks speech regions, which are packed into chunks of at most 15 s that are cut only at pauses. *Indic-Transcribe returns no timestamps, so a segment's timing is its VAD boundary.*
2. **Transcribe** each chunk with Indic-Transcribe. An empty result (music or noise) is skipped and logged, never narrated.
3. **Repair the English (LLM).** Punctuation, numbers as digits and obvious mishearings are fixed ("Right it" → "Write it"), and sentence fragments are merged. Code validates every reply: ids, pauses, length, and word overlap with what was said. A failed reply is re-asked once with the errors, then falls back to the raw segments. Then code splits any merge that wasn't needed: where a segment boundary falls at a sentence end, the unit is split, so every line keeps its own start time.
4. **Glossary (LLM)** of names, acronyms, technical terms and figure labels, keeping only terms that really occur in the text. Your `--glossary` wins.
5. **Romanized phrases (LLM).** Indian English often mixes in romanized Hindi or other languages ("it is ekdum mast, chalo shuru karte hain"). Indic-Translate renders those naturally into their own language, but other languages would only get the Hindi words spelled out in their own script. So the LLM writes an English *gloss* of just those phrases. Code checks it (numbers, terms, ≥50% of the words unchanged). The original wording is translated for the phrases' own language (and Urdu, for Hindi), and the gloss for every other language. Both are kept in `transcript.en.json`.
6. **Speakers, voices and style.** Each speech region's pitch is clustered: clearly separate clusters are different speakers. A speaker turn always ends a chunk, and the LLM may not merge across speakers. Each speaker keeps one voice, male or female by relative pitch, or two voices of one gender for two similar speakers. The LLM picks the Indic-Speak style, for example "educational lecture".
7. **Translate (per language, in parallel).** Indic-Translate translates, then translates back to English. Code checks the script, the numbers (native digits normalised), and that every glossary term reappears in the back-translation. The LLM judges source against back-translation, in English. When the judge reads a real change of meaning (or a hard check fails), the unit gets alternatives (sentence by sentence, code-mixed), and the best one wins. Plain native translation is preferred whenever it is good. Finally, glossary terms are pushed towards **one rendering per language**: where most units keep a term in English, the rest switch to their checked code-mixed version. Where most translate it, the native form is kept (no cascade into heavy code-mixing). Terms still rendered two ways are listed in `report.json` (`terms.still_mixed`).
8. **Speak and check by ear.** Indic-Speak voices requests of one or two sentences. Indic-Transcribe hears each clip back. A number-aware CER plus a loop detector flag repetitions and omissions; such a clip is re-spoken sentence by sentence, then fresh. Ordinary CER is reported too.
9. **Fit the time.** Each line uses its slot plus some of the silence after it, then a pitch-preserving stretch of at most 1.2×. If it is still too long, the LLM shortens the *English* (numbers and terms checked by code), which is re-translated and re-spoken.
10. **Mix.** Lines are placed without overlap. The original audio is kept (music and effects survive) and ducked by 40 dB only where a dub line replaces the speech. The dub speech is normalised to −16 LUFS. The result is muxed at the input's exact length, and SRT subtitles are written.

Every Bodhan and LLM call is cached by a hash of its exact input, so reruns are free and a crashed run resumes where it stopped.

## Versatility test beyond the demo

`scripts/make_test_video.py` generates an original 81 s test video with Indic-Speak's English voices (about ₹0.44). It has a man and a woman talking about compound interest with numbers, a music bed, music-only gaps, and a 35 s stretch of nonstop speech. Dubbed into **Kannada** (not in the demo):

- The speakers clustered at 167 Hz and 219 Hz and kept separate voices (Adarsh and Deepika).
- Nothing was dubbed over the music-only gaps, and the music played on under the dub.
- 7/7 units dubbed; CER 0.024; length 0 ms off; −16.0 LUFS; ₹1.50.
- Two sync misses inside the nonstop stretch (+0.55 s and +1.5 s): there is no silence there to absorb Kannada's longer speech, and the report explains them.

## Stretch goals

| Stretch goal | Status |
|---|---|
| Speaker-aware voice assignment | done: pitch-cluster speakers, one consistent voice each, two same-gender voices where available |
| Resumable runs with per-stage caching, parallel languages | done: every model call cached by exact input; languages run in parallel, isolated |
| Code-mixed mode | done: `--code-mixed` (Indic-Translate's code-mixed target) |
| Translated on-screen text (Indic-OCR) | not done |
| Thin API or web UI | not done |

## Where the LLM is used, and why

| Decision | Why an LLM | Fallback |
|---|---|---|
| Repair and regroup the English transcript | needs judgment about context and sentence boundaries | raw segments with basic cleanup |
| Build the glossary | needs domain judgment | `--glossary` only |
| Judge each translation via its back-translation | needs judgment about meaning; deterministic checks cover the facts | deterministic checks alone |
| Shorten English lines that run long | needs language judgment | stretch to 1.2×, report the overrun |
| Pick the speaking style | needs genre judgment | neutral voice |
| Gloss romanized Indian-language phrases into English | needs to tell "ekdum mast" from names and loanwords | lines keep their original wording |

Tested with **GLM 5.3 Flash** (`glm5.3-flash`, `LLM_REASONING_EFFORT=low`) on Sarvam's OpenAI-compatible API. Five models were compared on the real repair task: GLM 5.3 Flash was fastest and passed validation first time, and the guardrails handled weaker models (a retry for one, a safe fallback for another) with no code changes. The comparison is in [docs/DESIGN.md](docs/DESIGN.md#llm-choice-a-bake-off-on-the-real-task).

## Bodhan models used

All hosted, at `https://api.bodhan.ai`:

| Model | Use | Settings |
|---|---|---|
| `indic-transcribe` | source ASR (`language=en`) and back-transcription of every dubbed clip (`language=<target>`) | chunks of 15 s or less |
| `indic-translate` | translation, back-translation, code-mixed alternatives | `script=native` or `codemix`. The documented `POST /translate` returned 404 for our key, so the client falls back automatically to the documented chat transport (`/v1/chat/completions`, prompt `Translate to <Language>: …`) |
| `indic-speak` | speech, one or two sentences per request | per-language voices; style chosen per video |

## Results on the demo video

The demo is a Class 9 mathematics lesson (5 min 29 s), dubbed into Hindi, Tamil and Bengali with the final code and `glm5.3-flash` as the LLM. These numbers come straight from `report.json`:

| Target | Hindi | Tamil | Bengali |
|---|---|---|---|
| Line starts within ±300 ms | 23/23 (worst +80 ms) | 21/23 (worst +3676 ms) | 23/23 (worst +80 ms) |
| Source segments within ±300 ms (estimated) | 24/25 | 22/25 | 24/25 |
| Speed change within 0.85–1.2× | max 1.2×, 0 outside | max 1.2×, 0 outside | max 1.17×, 0 outside |
| Coverage | 23/23 dubbed | 23/23 dubbed | 23/23 dubbed |
| Back-transcription CER, number-aware / plain (mean) | 0.015 / 0.226 | 0.014 / 0.218 | 0.011 / 0.391 |
| Output length vs input | 0 ms | 0 ms | 0 ms |
| Speech loudness (target −16 ±1 LUFS) | -16.0 | -16.0 | -16.1 |

- **What the misses are.**
  - Tamil has one dense sentence: 13 glossary terms and figure labels, which Tamil reads letter by letter. It cannot fit its slot even after condensing (one condensed version was rejected by the judge for changing "vertically opposite"), so it runs at 1.2×, and the next two lines start late (+0.6 s, +3.7 s).
  - The one estimated drift per language in Hindi and Bengali is inside a merged line that re-joins a sentence split across segments.
  - `report.json` explains each miss.
- **Plain vs number-aware CER.** Plain CER counts code-mixed English words (which come back in Indic script) and written digits against spoken number words as errors. The number-aware CER, which treats those as wildcards, is the one QA acts on. Both are reported per segment.
- **Code-mixing kept low.** 4 of 23 Hindi units and 3 of 23 Tamil units use code-mixed recovery; Bengali uses more (10 of 23), because Bengali most often kept these geometry terms in English.
- **QA caught real Indic-Speak failures during development:** a three-times loop (`…के बीच होता है और एक रिजू कोण…` repeated) and a dropped clause. Both were re-spoken and recovered. The translation judge caught Hindi rendering "vertically opposite angles" as "right angle".

**A language not in the demo, and `--code-mixed`** (live, on a 30 s cut of the lesson):

| Run | Result |
|---|---|
| `--langs te` (Telugu) | ok, no misses; CER 0.0, every clip accepted first take; sync 0 ms; length 0 ms; −16.0 LUFS; 28 s; ₹0.22 |
| Same, **inside the Docker image** | ok, no misses; 21.6 s; ₹0.22 |
| `--langs hi --code-mixed` | ok, no misses; natural Hinglish ("दो angles adjacent होते हैं जब उनके पास एक ही vertex होता है…"); CER 0.014; ₹0.23 |
| `pytest -m live` (Hindi, the CLI end to end) | passed, 72 s |

## Cost and runtime

From the cold-cache run of the demo (5.5 min, 3 languages, empty cache):

- **Bodhan cost:** ₹8.34 in total, so about **₹0.51 per minute of video per language**. That includes QA back-transcription, retakes, alternatives and condensing, plus shared source transcription (about ₹0.07 per video-minute, once).
  - **Indic-Speak** is about 80% of it (₹6 per 10,000 characters).
  - **Indic-Transcribe** (source and QA) is about 15–20%.
  - **Indic-Translate** is well under 1%.

  `report.json` breaks cost down per language (`languages.<lang>.cost_inr`) and per model.
- **Runtime: 24.0 minutes, inside the brief's 30-minute target.** Most of it is waiting on the keys' rate limits: about 16 translate and 8 speak requests a minute (`runtime_check.rate_limit_wait_s`). A rerun from the cache takes a few minutes and costs about ₹0.
- **LLM:** a handful of calls per video plus a few judge calls per language. The cost depends on the provider; token counts are in `report.json`.

## Tests

```bash
uv run pytest                 # unit tests + an offline end-to-end run on a generated 30 s clip
uv run ruff check .
BODHAN_DUB_SMOKE_VIDEO=path/to/english.mp4 uv run pytest -m live   # live end-to-end on a 30 s cut (about ₹1)
```

The segmentation and timing logic has property tests over 200 random speech layouts: chunks stay under 15 s, cuts land in pauses unless forced, all speech is covered exactly once, and the result is deterministic. Other tests cover placement without overlap, stretch limits, ducking, subtitle timing, loudness, QA on real failure shapes (a loop, a dropped clause, numbers spoken in English), translation recovery and per-language failure isolation.

## Limitations

- **Rate limits dominate runtime.** On our keys, Indic-Translate allowed about 16 requests a minute and Indic-Speak about 8, and there are no rate-limit headers. The client learns the limits from 429s (honouring `Retry-After`) with a cooldown shared across threads. Most of the wall-clock time goes to waiting on these limits.
- **Numbers in speech.** Indic-Speak reads ASCII digits in English even inside Hindi ("180" → "one hundred and eighty") but native digits in the language itself (१८० → एक सौ अस्सी), which was verified live for all 22 target languages, including years, decimals, percentages and thousands. So the text *sent to Indic-Speak* has its digits rewritten in the target script by plain code (never the LLM). Subtitles and checks keep Indic-Translate's exact text, the spoken form is recorded as `tts_text` in `segments.<lang>.json`, and `--ascii-digits` turns this off. Digits that touch Latin letters ("H2O", "COVID-19") are left alone. Years are read in full ("one thousand nine hundred forty-seven"), not the spoken "nineteen forty-seven".
- **Mixed register when recovering.** A unit whose native translation changed a technical term may be recovered with a code-mixed translation (English term kept), so it can differ in register from its neighbours. Use `--code-mixed` for a uniformly code-mixed dub.
- **The judge sees meaning only through a back-translation.** A slip that back-translates cleanly can pass. In the demo, Hindi rendered "Period one" (a class period) as अवधि एक (a span of time), and it was not caught.
- **Out of credit fails fast.** When a key's credit is spent, Bodhan answers `429 ExceededBudget`. The client treats that as permanent (no retries) and says so. Affected units are skipped with that reason, a language with nothing dubbed is marked `failed`, and a rerun after topping up reuses every cached result.
- **No diarization.** Speakers are told apart only by pitch. Two speakers with similar pitch share a voice, and a turn with no pause (under 0.3 s) keeps the previous speaker's voice.
- **Terms can still be rendered two ways.** When a native translation of a term was judged wrong in a minority of units, those units keep English and the rest stay native; the term is listed in `terms.still_mixed`. Forcing uniformity would mean code-mixing the majority. Also, Two different native words for one term (both correct in meaning) can't be told apart without reading the language, which the LLM may not do here.
- **Sync inside merged lines is estimated.** Without word timestamps, drift of later segments inside a merged line is estimated from even pacing (`segment_sync_estimated_ms`).
- **The original voice is ducked, not removed.** No source separation, so a faint English voice stays under the dub.
- **Python 3.12+** (numpy 2.5). `silero-vad-lite` has wheels only for macOS, x86-64 Linux and Windows; on other platforms (Linux ARM) it is skipped by a platform marker, and the energy VAD runs instead (recorded in `transcript.en.json`). The Docker image is amd64.

## AI assistance

I built this recipe with **Claude Code** (Anthropic's coding agent, Claude Opus) as a pair programmer, working through the build one stage at a time.

- **What I directed:** the choice of assignment and the day-by-day plan; a rules check against the brief and CONTRIBUTING before each stage; and the trade-off decisions. Examples: whether LLM transcript repair is within the rules, comparing five LLMs and switching to GLM 5.3 Flash, sending native digits to Indic-Speak so numbers are spoken in the target language, and stopping a run to re-plan when the API's behaviour didn't match its docs.
- **What Claude Code did:** wrote most of the code and the 330+ tests; probed the Bodhan APIs live to find how they actually behave (the `/translate` 404 and chat-transport fallback, rate limits without headers, the `ExceededBudget` 429, digits read in English); ran the demo, the non-demo-language runs and the clean-install checks; and drafted the documentation.
- **Review:** an independent reviewer agent checked the whole design against the brief twice, and its findings were fixed: judge-gated recovery, consistent term rendering, failing honestly when nothing could be dubbed, speaker clustering, and flexible config files.

I reviewed each module and the reasoning behind it, and I'm ready to walk through any part of it in the debrief.
