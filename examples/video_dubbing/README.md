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
| `--glossary` | YAML of terms to keep consistent; `keep: true` means never translate (see [config/glossary.example.yaml](config/glossary.example.yaml)) |
| `--voices` | YAML of voices per language and an optional style (see [config/voices.example.yaml](config/voices.example.yaml)) |
| `--code-mixed` | code-mixed targets such as Hinglish (English technical terms inside Indian-language speech) |
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
3. **Repair the English (LLM).** Punctuation, numbers as digits and obvious mishearings are fixed ("Right it" → "Write it"), and sentence fragments are merged. Code validates every reply: ids, pauses, length, and word overlap with what was said. A failed reply is re-asked once with the errors, then falls back to the raw segments.
4. **Glossary (LLM)** of names, acronyms, technical terms and figure labels, keeping only terms that really occur in the text. Your `--glossary` wins.
5. **Voices and style.** Each unit's pitch picks the male or female voice (with hysteresis, so one speaker keeps one voice). The LLM picks the Indic-Speak style, for example "educational lecture".
6. **Translate (per language, in parallel).** Indic-Translate translates, then translates back to English. Code checks the script, the numbers (native digits normalised), and that every glossary term reappears in the back-translation. The LLM judges source against back-translation, in English. When the judge reads a real change of meaning (or a hard check fails), the unit gets alternatives (sentence by sentence, code-mixed), and the best one wins. Plain native translation is preferred whenever it is good.
7. **Speak and check by ear.** Indic-Speak voices requests of one or two sentences. Indic-Transcribe hears each clip back. A number-aware CER plus a loop detector flag repetitions and omissions; such a clip is re-spoken sentence by sentence, then fresh. Ordinary CER is reported too.
8. **Fit the time.** Each line uses its slot plus some of the silence after it, then a pitch-preserving stretch of at most 1.2×. If it is still too long, the LLM shortens the *English* (numbers and terms checked by code), which is re-translated and re-spoken.
9. **Mix.** Lines are placed without overlap. The original audio is kept (music and effects survive) and ducked by 26 dB only where a dub line replaces the speech. The dub speech is normalised to −16 LUFS. The result is muxed at the input's exact length, and SRT subtitles are written.

Every Bodhan and LLM call is cached by a hash of its exact input, so reruns are free and a crashed run resumes where it stopped.

## Where the LLM is used, and why

| Decision | Why an LLM | Fallback |
|---|---|---|
| Repair and regroup the English transcript | needs judgment about context and sentence boundaries | raw segments with basic cleanup |
| Build the glossary | needs domain judgment | `--glossary` only |
| Judge each translation via its back-translation | needs judgment about meaning; deterministic checks cover the facts | deterministic checks alone |
| Shorten English lines that run long | needs language judgment | stretch to 1.2×, report the overrun |
| Pick the speaking style | needs genre judgment | neutral voice |

Tested with **GLM 5.3 Flash** (`glm5.3-flash`, `LLM_REASONING_EFFORT=low`) on Sarvam's OpenAI-compatible API. Five models were compared on the real repair task: GLM 5.3 Flash was fastest and passed validation first time, and the guardrails handled weaker models (a retry for one, a safe fallback for another) with no code changes. The comparison is in [docs/DESIGN.md](docs/DESIGN.md#llm-choice-a-bake-off-on-the-real-task).

## Bodhan models used

All hosted, at `https://api.bodhan.ai`:

| Model | Use | Settings |
|---|---|---|
| `indic-transcribe` | source ASR (`language=en`) and back-transcription of every dubbed clip (`language=<target>`) | chunks of 15 s or less |
| `indic-translate` | translation, back-translation, code-mixed alternatives | `script=native` or `codemix`. The documented `POST /translate` returned 404 for our key, so the client falls back automatically to the documented chat transport (`/v1/chat/completions`, prompt `Translate to <Language>: …`) |
| `indic-speak` | speech, one or two sentences per request | per-language voices; style chosen per video |

## Results on the demo video

The demo is a Class 9 mathematics lesson (5 min 29 s), dubbed into Hindi, Tamil and Bengali with `glm5.3-flash` as the LLM. These numbers come straight from `report.json`:

| Target | Hindi | Tamil | Bengali |
|---|---|---|---|
| Sync: segments starting within ±300 ms | 14/14 (worst +80 ms) | 13/14 (one at +2.45 s, see below) | 14/14 (worst +80 ms) |
| Speed change within 0.85–1.2× | max 1.115×, 0 outside | max 1.2×, 0 outside | max 1.197×, 0 outside |
| Coverage | 14/14 dubbed | 14/14 dubbed | 14/14 dubbed |
| Back-transcription CER, number-aware (mean / max) | 0.007 / 0.028 | 0.013 / 0.051 | 0.019 / 0.070 |
| Back-transcription CER, plain (mean / max) | 0.173 / 0.742 | 0.229 / 0.750 | 0.269 / 0.889 |
| Output length vs input | 0 ms | 0 ms | 0 ms |
| Speech loudness (target −16 ±1 LUFS) | −16.0 | −16.1 | −16.1 |

- **Plain vs number-aware CER.** Plain CER is high mostly because Indic-Speak reads digits in English and code-mixed English words come back in Indic script. The number-aware CER, which treats those as wildcards, is the one QA acts on. Both are reported per segment.
- **The one sync miss is a line that cannot fit.** In Tamil, the source sentence about which angles form a linear pair carries 13 glossary terms and figure labels in about 21 s (AOC, COB, OA, OB, line AB, BOD, COE, EOB, …), and Tamil reads each label letter by letter. Condensing cut the English from 47 to 37 words. Two further rounds were rejected because any shorter wording would drop a required term. The line therefore runs at 1.2× and overruns, the next line starts 2.45 s late, and `report.json` says exactly that.
- **QA caught real Indic-Speak failures during development:** a three-times loop (`…के बीच होता है और एक रिजू कोण…` repeated) and a dropped clause. Both were re-spoken and recovered. The translation judge caught Hindi rendering "vertically opposite angles" as "right angle", which was recovered with a code-mixed translation.

**A language not in the demo, and `--code-mixed`** (live, on a 30 s cut of the lesson):

| Run | Result |
|---|---|
| `--langs te` (Telugu) | ok, no misses; CER 0.0, every clip accepted first take; sync 0 ms; length 0 ms; −16.0 LUFS; 28 s; ₹0.22 |
| `--langs hi --code-mixed` | ok, no misses; natural Hinglish ("दो angles adjacent होते हैं जब उनके पास एक ही vertex होता है…"); CER 0.014; ₹0.23 |
| `pytest -m live` (Hindi, the CLI end to end) | passed, 72 s |

## Cost and runtime

Per minute of input video, per language, on hosted APIs (from the metered usage in `report.json`):

| | per video-minute per language |
|---|---|
| Indic-Speak (dub speech, including retakes) | about ₹0.33 |
| Indic-Transcribe (back-transcription QA) | about ₹0.07 |
| Indic-Translate (translation + back-translation + alternatives) | under ₹0.01 |
| Indic-Transcribe (source, shared by all languages) | about ₹0.07 per video-minute, once |
| **Total** | **about ₹0.40, plus ₹0.07 once** |

- **Runtime:** the 5.5-minute demo in 3 languages took **24.8 minutes**, inside the brief's 30-minute target. That run had a warm cache: source transcription, the Hindi translation and most Hindi speech were cached from earlier runs. A fully cold run has not been measured; expect it to take longer, since the extra work is rate-limited translate and speak calls. Most of that is waiting on the keys' rate limits: about 16 translate and 8 speak requests a minute (`report.json` → `runtime_check.rate_limit_wait_s`). A rerun from the cache takes minutes and costs ₹0.
- **LLM:** a few calls per video plus one judge call per language (the provider's cost; token counts are in `report.json`).

## Tests

```bash
uv run pytest                 # unit tests + an offline end-to-end run on a generated 30 s clip
uv run ruff check .
BODHAN_DUB_SMOKE_VIDEO=path/to/english.mp4 uv run pytest -m live   # live end-to-end on a 30 s cut (about ₹1)
```

The segmentation and timing logic has property tests over 200 random speech layouts: chunks stay under 15 s, cuts land in pauses unless forced, all speech is covered exactly once, and the result is deterministic. Other tests cover placement without overlap, stretch limits, ducking, subtitle timing, loudness, QA on real failure shapes (a loop, a dropped clause, numbers spoken in English), translation recovery and per-language failure isolation.

## Limitations

- **Rate limits dominate runtime.** On our keys, Indic-Translate allowed about 16 requests a minute and Indic-Speak about 8, and there are no rate-limit headers. The client learns the limits from 429s (honouring `Retry-After`) with a cooldown shared across threads. Most of the wall-clock time goes to waiting on these limits.
- **Numbers are spoken in English.** Indic-Speak reads digits in English even inside Hindi ("180" → "one hundred and eighty"). The pipeline keeps digits in translations (so subtitles and number checks stay exact), and QA treats spoken numbers as wildcards rather than errors.
- **Mixed register when recovering.** A unit whose native translation changed a technical term may be recovered with a code-mixed translation (English term kept), so it can differ in register from its neighbours. Use `--code-mixed` for a uniformly code-mixed dub.
- **The judge sees meaning only through a back-translation.** A slip that back-translates cleanly can pass. In the demo, Hindi rendered "Period one" (a class period) as अवधि एक (a span of time), and it was not caught.
- **Out of credit fails fast.** When a key's credit is spent, Bodhan answers `429 ExceededBudget`. The client treats that as permanent (no retries) and says so. Affected units are skipped with that reason, a language with nothing dubbed is marked `failed`, and a rerun after topping up reuses every cached result.
- **No diarization.** Speakers are told apart only by pitch (male/female). Two speakers with similar pitch share a voice.
- **The original voice is ducked, not removed.** No source separation, so a faint English voice stays under the dub.
- **Python 3.12+** (numpy 2.5). `silero-vad-lite` has wheels only for macOS, x86-64 Linux and Windows; on other platforms (Linux ARM) it is skipped by a platform marker, and the energy VAD runs instead (recorded in `transcript.en.json`). The Docker image is amd64.

## AI assistance

Built with Claude Code (Anthropic) as a pair programmer: architecture discussion, code and tests, live API probing, and the LLM bake-off. Every design decision was reviewed and every line can be explained.
