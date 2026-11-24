# Design notes: video dubbing pipeline

This explains how `bodhan_dub` turns one English video into dubbed videos, how it checks its own work, and why each decision sits with plain code or with the LLM.

## Ground rules this design keeps

- **All speech recognition, translation and speech synthesis go to Bodhan:** `indic-transcribe`, `indic-translate` and `indic-speak`. No other ASR, MT or TTS is used anywhere, not even for alignment or fallback. Timing comes from voice-activity detection (Silero VAD), which the brief allows.
- **The LLM only reads and writes English.** It repairs the English transcript, builds the glossary, shortens English lines that run long, picks a speaking style and judges translations *through their English back-translation*. Every Indian-language word comes from Indic-Translate.
- **The LLM proposes, code decides.** Every LLM answer is validated by deterministic code. If it fails validation, the LLM is re-asked once with the exact errors, and if it fails again a deterministic fallback runs. An LLM outage degrades quality; it never stops a run.

## Architecture

```mermaid
flowchart LR
    subgraph Input
        V[video file<br/>MP4/MOV/MKV/WebM]
    end
    subgraph Local["Local, deterministic"]
        FF[ffmpeg / ffprobe<br/>decode, stretch, mux]
        VAD[Silero VAD<br/>speech regions]
        CH[chunker<br/>cut only at pauses]
        TC[text checks<br/>script, numbers, terms]
        PITCH[pitch clusters<br/>speakers + voices]
        MIX[mixer<br/>ducking, −16 LUFS, SRT]
        CACHE[(disk cache<br/>every model call)]
    end
    subgraph Bodhan["Bodhan hosted APIs"]
        ASR[Indic-Transcribe]
        MT[Indic-Translate]
        TTS[Indic-Speak]
    end
    subgraph Brain["LLM (any OpenAI-compatible)"]
        LLM[English-only:<br/>repair, glossary, judge,<br/>condense, style]
    end
    V --> FF --> VAD --> PITCH --> CH --> ASR
    ASR --> LLM
    LLM --> MT
    MT -->|back-translation| TC
    TC --> LLM
    MT --> TTS --> ASR
    PITCH -.->|voice per speaker| TTS
    TTS --> MIX --> FF
    ASR & MT & TTS & LLM -.-> CACHE
```

| Component | File | One line |
|---|---|---|
| CLI | `cli.py` | the exact required command; exit 0 / 1 (a language failed) / 2 (bad input or setup) |
| Orchestrator | `pipeline.py` | shared stages once, then one isolated thread per language; writes `report.json` |
| Bodhan client | `bodhan.py` | retries with backoff, a shared rate-limit cooldown, a concurrency cap, cost metering per model and language, caching, fail-fast on spent credit |
| LLM client | `llm.py` | JSON replies, optional settings dropped if rejected, cut-off replies fail fast, cached, failures become one `LLMError` |
| Cache | `cache.py` | content-addressed disk cache for every model call (reruns are free and resumable) |
| Media and audio | `media.py`, `audio.py` | ffprobe/ffmpeg ingest, trim, pitch-preserving stretch, EBU R128 loudness |
| VAD | `vad.py` | Silero frame scores → speech regions; energy fallback |
| Speakers and voices | `voices.py` | pitch clusters → speakers, one consistent voice each; flexible `--voices` override |
| Chunker | `chunking.py` | regions → chunks of 15 s or less, cut at the longest pause and at every speaker turn |
| Transcribe | `transcribe.py` | Indic-Transcribe per chunk; an empty transcript is a logged skip |
| Repair | `repair.py` | LLM fixes punctuation, numbers and mishearings and re-joins split sentences; validator, retry, fallback; unnecessary merges split back at sentence ends |
| Glossary | `glossary.py` | LLM term list, checked for literal presence; flexible user YAML wins |
| Romanized phrases | `romanized.py` | LLM English gloss for romanized Indian-language phrases, checked; used for other languages only |
| Text checks | `textcheck.py` | script share, numbers (native digits normalised), glossary terms, native digits for speech |
| Translate | `translate.py` | translate, back-translate, check, judge, try alternatives, pick the best; majority term rendering |
| Synthesis | `synth.py` | TTS of 1–2 sentences per request, re-takes, timing fit, judged condensing |
| Speech QA | `ttsqa.py` | number-aware CER, loop detector, omission/repetition classification |
| Mix | `mix.py` | placement without overlap, −40 dB ducking under dub lines, bleed check, −16 LUFS, mux, subtitles |
| Report | `report.py` | every quality target scored, with the reason for each miss; per-segment drift estimate |

## Workflow

```mermaid
flowchart TD
    A[probe + extract 16 kHz audio] --> B[Silero VAD → speech regions]
    B --> P[pitch clusters → who speaks each region]
    P --> C[pack chunks ≤15 s, cut at the longest pause and at every speaker turn<br/>nonstop speech: forced cut at quietest frame]
    C --> D[Indic-Transcribe each chunk]
    D --> D1{empty?}
    D1 -- yes --> SK1[skip: no speech recognised]
    D1 -- no --> E[LLM repair: punctuation, digits,<br/>mishearings, merge fragments]
    E --> E1{validator: ids, gaps ≤1.5 s,<br/>units ≤25 s, word overlap ≥0.7}
    E1 -- fail --> E2[re-ask once with the errors] --> E3{valid?}
    E3 -- no --> E4[fallback: raw segments + basic cleanup]
    E1 -- ok --> SP
    E3 -- yes --> SP
    SP[split merges at segment boundaries that are sentence ends] --> F
    E4 --> F[LLM glossary, filtered to terms really in the text]
    F --> RG[LLM: English gloss for romanized Indian-language phrases, checked]
    RG --> G[voice per speaker · LLM picks a style]
    G --> L{{for each language, in parallel and isolated}}
    L --> T1[Indic-Translate unit] --> T2[back-translate to English]
    T2 --> T3{checks: script, numbers,<br/>glossary terms in back-translation}
    T3 --> T4[LLM judge: source vs back-translation]
    T4 --> T5{problem?}
    T5 -- yes --> T6[alternatives: per-sentence, code-mixed<br/>checked + judged] --> T7[pick best by checks, then verdict]
    T5 -- no --> H
    T7 --> H[glossary terms: follow each term's majority rendering, one pass]
    H --> S1[Indic-Speak 1–2 sentences per request, digits in native script]
    S1 --> S2[back-transcribe → CER, length ratio]
    S2 --> S3{repetition / omission?}
    S3 -- yes, takes left --> S1
    S3 -- no --> S4{fits slot + following silence?}
    S4 -- yes --> M
    S4 -- within 1.2× --> S5[time-stretch] --> M
    S4 -- longer --> S6[LLM shortens English, code checks numbers + terms<br/>→ re-translate → re-speak, up to 3 rounds, each asking for less] --> S5
    M[place lines, never overlapping] --> N[duck original only under dub lines · dub at −16 LUFS · mux]
    N --> O[video.lang.mp4 · subtitles.lang.srt · segments.lang.json]
    O --> R[report.json: every target, every miss explained]
```

## Where the LLM decides, and the fallback for each

| Decision point | What the LLM decides | How code checks it | If it fails |
|---|---|---|---|
| Transcript repair | punctuation, numbers as digits, obvious mishearings, which fragments form one sentence, what is not narration | ids exact and in order, no merge across pauses over 1.5 s, units of 25 s or less, word overlap with the raw ASR of at least 0.7 | re-ask once with the error list, then raw segments with basic cleanup |
| Glossary | which names, acronyms, technical terms and figure labels must stay consistent | each term must literally occur in the transcript | user glossary only |
| Romanized phrases | which words are romanized Indian-language speech (not names, foods, festivals or loanwords), their English meaning, and their language | numbers and glossary terms kept; ≥50% of the line's words unchanged; length plausible | the line keeps its original wording |
| Speaking style | the Indic-Speak style matching the genre (a lesson → "educational lecture") | must be one of the 14 documented styles | neutral voice |
| Translation judge | per unit, whether the back-translation still means the source (ok / minor / major) | verdicts must reference known ids; deterministic checks run regardless | deterministic checks alone decide |
| Condensing | a shorter English wording of a line that runs long | shorter by at least 3%, same numbers, same glossary terms; then re-translated and re-checked | gentle stretch up to 1.2×, then the overrun is reported |

## How sync and quality are checked

- **Merges are kept minimal.** The LLM may merge segments only to re-join a sentence split across them. Code splits any other merge back wherever a segment boundary coincides with a sentence end of the cleaned text (matched by word counts), so lines are timed from their own segment starts. Remaining drift inside merged lines is estimated per source segment in `segment_sync_estimated_ms`.
- **Timing source.** Indic-Transcribe returns no timestamps, so a segment's start is the VAD onset of its chunk. Chunks never cut through speech except where speech is nonstop for over 15 s, which is flagged `forced_cut`.
- **Duration drift**, in order: (1) use the line's own slot plus part of the silence after it (at most 1.5× the slot plus 1 s, so a line never drifts off its picture); (2) time-stretch up to 1.2×, pitch-preserving `atempo`; (3) the LLM shortens the *English*, which is re-translated and re-spoken, in up to three rounds. Each round starts from the best English so far and asks for less: it recomputes the target from the measured speech, and goes 15% further after a round that didn't help. Lines never overlap: if one still runs long, the next starts after it, and that shift is the reported sync error.
- **Translation fidelity**, without the LLM ever reading or writing Indian-language text: back-translate with Indic-Translate, then check numbers (native digits normalised), script share, and whether every glossary term in the source reappears in the back-translation. Back-translations paraphrase ("common arm" → "common side"), so a missing term alone does not trigger recovery. **The LLM judge decides:** alternatives are tried on a hard failure, a lost number, a changed `keep` term, or a `major` verdict. The term check decides alone only if the judge is unavailable. In testing this caught Hindi rendering "vertically opposite angles" as "right angle" and "reflex angle" as "angle of reflection", while ignoring harmless paraphrases.
- **Model misbehaviour** (Indic-Speak repeating or dropping words): every TTS clip (one or two sentences) is back-transcribed with Indic-Transcribe and judged by `ttsqa.py`:
  - a **number-aware CER**: digit runs and Latin words are bounded wildcards, because a number is written as digits but spoken as words;
  - a **loop detector**: the text's own words said more often than written;
  - a **length check** for omissions.

  Seen live: a three-times loop and a dropped clause, both caught. A *repetition* or *omission* is re-spoken sentence by sentence, then once fresh; a plain *mismatch* is flagged but not retaken, because it is more often back-transcription noise. Ordinary CER is reported next to the number-aware one.
- **Silence and music.** Non-speech stays non-speech: nothing is dubbed outside VAD speech regions, empty transcripts are skipped, and the LLM can mark lyrics or noise as `drop`. The original audio bed is kept and **ducked only where a dub line replaces the original speech**, so music, dropped vocals and any line that could not be dubbed keep their original audio instead of leaving silent holes.
- **Numbers in speech.** Indic-Speak reads ASCII digits in English but native digits in the target language (verified live in all 22 languages), so `textcheck.native_digits` rewrites digit runs in the target script in the text sent to TTS only. Subtitles keep Indic-Translate's text, and `--ascii-digits` opts out.
- **Loudness.** The dub speech track is normalised to −16 LUFS (EBU R128, measured with ffmpeg's `ebur128`) before mixing.
- **Length.** The mix is built at exactly the input duration, and the video stream is copied, not re-encoded.

## Failure handling

- **Per-call:** retries with exponential backoff and jitter on 429, 5xx and network errors. A 429 sets a cooldown for that model shared by every thread (it honours `Retry-After`), so threads don't burn retries together. Client errors (4xx) fail fast.
- **Per-chunk, per-unit, per-sentence:** a failure is recorded as a `skipped` or `flagged` item with its reason, and the run goes on. A language where *every* unit failed (a bad key, an outage) is marked `failed`, never `ok`.
- **Per-language:** each language runs inside an isolation boundary. A crash marks that language `failed` in `report.json`, still writes its partial `segments.<lang>.json`, and the CLI exits 1 after the others finish.
- **Reruns:** every Bodhan and LLM call is cached by a hash of its exact input, so a rerun after a crash pays only for what is missing.

## Speakers and voices

There is no diarization model, and Indic-Transcribe has no speaker labels, so speakers are told apart by **pitch**: autocorrelation over voiced frames, numpy only (`voices.py`).

1. **Every VAD speech region gets a median pitch.** A two-cluster split on log pitch counts as two speakers only if the clusters are clearly separate: centroids at least 15% apart, well separated relative to their spread, and each holding at least 10% of the speech. Otherwise there is one speaker.
2. **A speaker turn always ends a chunk**, and the repair validator rejects any LLM merge across speakers. So every unit has exactly one speaker.
3. **Voices:**
   - Clusters 1.25× or more apart are treated as a man and a woman: male voice for the lower, female for the higher. This is relative, not a fixed cut-off; Indic-Speak's own male voice speaks near 180 Hz.
   - Closer clusters on the same side of 165 Hz get two voices of that gender where the language has them (Hindi has Kavya and Suhani), otherwise the other gender's voice, so the two speakers always sound different.
   - A single speaker gets the voice for their pitch.
   - `--voices` overrides all of this.

Tested on a generated two-speaker video with a music bed (`scripts/make_test_video.py`): the speakers clustered at 167 Hz and 219 Hz and kept separate male and female voices in Kannada. **Limits:**
- Two speakers with similar pitch share a voice.
- A turn with no pause (under 0.3 s) cannot be detected, so that line keeps the previous speaker's voice.
- Strongly tonal background music can bias the pitch estimate.

The output is labelled AI-dubbed in the MP4 metadata and in the subtitles, and no voice imitates the original speaker.

## LLM choice: a bake-off on the real task

The reviewers run their own LLM, so the guardrails, not the model, have to carry quality. To check that, five models on Sarvam's OpenAI-compatible API were given the same repair and glossary task (the demo's 25 raw ASR segments), each on its best reasoning setting:

| Model | Accepted | Units | Mean fidelity | Repair time | Notes |
|---|---|---|---|---|---|
| glm5.3-flash, `low` | first try | 14 | 0.966 | 1.6 s | **used for the demo**: fastest, follows every rule |
| gemma4 (no `reasoning_effort`) | first try | 18 | 0.974 | 3.4 s | merges less; one small added word |
| glm5.3, `low` | after 1 retry | 14 | 0.963 | 7.3 s | made a 30.8 s unit; the retry-with-errors loop fixed it |
| deepseekv4.1-flash, `none` | first try | 25 | 0.964 | 5.1 s | never merges, so no sentence repair |
| sarvam-105b, `none` | fallback | 25 | — | 6.3 s | merged across a 1.9 s pause and made a 48 s unit, twice |

- Two models (sarvam-105b, deepseek) reasoned without stopping at `low` effort and needed `reasoning_effort=none`. gemma4 misbehaves if `reasoning_effort` is sent at all. Hence these settings are optional env variables, sent only when set, and dropped automatically if the provider rejects them.
- An Indic-specialised model has no advantage here: by rule, the LLM never writes Indian-language text.
- Batching was checked live with no `LLM_MAX_TOKENS` (the provider default cap): 40 segments repaired in 2 batches, and 40 judge items in 3 calls, all accepted. A reply cut off at the token limit makes repair split the batch at its widest pause, rather than fall back.
