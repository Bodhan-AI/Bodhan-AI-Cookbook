# System Architecture: `bodhan_dub`

This document contains the five architectural specifications governing the `bodhan_dub` automated video dubbing pipeline.

---

## 1. System Architecture

```mermaid
flowchart TB
  CLI["CLI entry<br/>python -m bodhan_dub"]

  subgraph CORE["Control plane"]
    CFG["Settings<br/>env vars, flags, language registry"]
    PRE["Preflight validator<br/>ffprobe, ffmpeg, keys, langs, disk, cost estimate"]
    ORCH["Orchestrator<br/>asyncio stage DAG, per-language isolation"]
    BUD["Budget guard<br/>cost ledger, max-cost-inr"]
    MAN["Run manifest<br/>stage checkpoints, resume"]
    LOG["Structured logs JSONL<br/>run_id, lang, segment_id, stage"]
    POL["Policy guard<br/>host allowlist, English-only LLM I/O,<br/>forbidden-library test"]
  end

  subgraph SHARED["Shared stages: run once"]
    ING["Media ingest<br/>probe, extract 16k mono wav, keep full-quality audio"]
    VAD["VAD and chunk planner<br/>silero-vad with ffmpeg silencedetect fallback,<br/>cuts at pauses, window max 29s"]
    ASR["ASR stage<br/>Indic-Transcribe per chunk, overlap dedupe"]
    SEG["Segmenter<br/>sentence merge or split, slot and slack calculation"]
    GLO["Glossary builder<br/>entities, acronyms, numbers, terms"]
  end

  subgraph PERLANG["Per-language stages: parallel and isolated"]
    TRN["Translation stage<br/>protect spans, translate, structure checks"]
    TTS["TTS and fit controller<br/>predict, synthesize, stretch or condense"]
    QA["Speech QA<br/>back-transcribe, CER, repeat, omission, silence, clipping"]
    SCH["Timeline scheduler<br/>sync offsets, slack borrowing"]
    MIX["Mixer<br/>mute speech regions, speech stem, loudness, mux"]
    SUB["Subtitle writer<br/>SRT and VTT, grapheme-safe wrapping"]
  end

  subgraph GW["API gateway layer"]
    CACHE[("Content-addressed cache<br/>sha256 of model, params, input")]
    RES["Resilience<br/>retry with jitter, 429 Retry-After, timeouts,<br/>semaphores, circuit breaker"]
  end

  subgraph EXT["External services"]
    BT["Indic-Transcribe"]
    BTR["Indic-Translate"]
    BSP["Indic-Speak"]
    LLM["OpenAI-compatible LLM<br/>English text only"]
  end

  subgraph OUT["Outputs"]
    O1["transcript.en.json"]
    O2["lang/video.lang.mp4"]
    O3["lang/subtitles.lang.srt"]
    O4["lang/segments.lang.json"]
    O5["report.json"]
  end

  CLI --> CFG --> PRE --> ORCH
  ORCH --> ING --> VAD --> ASR --> SEG --> GLO
  GLO -->|fork per language| TRN
  TRN --> TTS --> QA --> SCH --> MIX --> SUB
  QA -->|retry plan| TTS
  ORCH -.-> BUD
  ORCH -.-> MAN
  ORCH -.-> LOG
  ASR --> RES
  SEG --> RES
  GLO --> RES
  TRN --> RES
  TTS --> RES
  QA --> RES
  RES <--> CACHE
  RES --> BT
  RES --> BTR
  RES --> BSP
  RES --> LLM
  POL -.->|enforces| RES
  BUD -.->|gates| RES
  SEG --> O1
  MIX --> O2
  SUB --> O3
  TTS --> O4
  ORCH --> O5
```

---

## 2. End-to-End Pipeline with Decision Points

```mermaid
flowchart TD
  A(["Start: CLI args"]) --> B{"Preflight OK?<br/>file, streams, duration 1 to 15 min,<br/>ffmpeg, keys, langs valid"}
  B -->|no| X1(["Exit 2: clear error, no work done"])
  B -->|yes| C["Cost estimate vs max-cost-inr"]
  C --> C2{"Estimate within budget?"}
  C2 -->|no| X2(["Exit 2: show estimate, suggest fewer langs"])
  C2 -->|yes| D["Ingest: extract 16k mono wav,<br/>keep original audio for mixing"]
  D --> E["VAD: speech regions and pauses"]
  E --> E2{"Any speech found?"}
  E2 -->|no| E3["Passthrough: copy video, status ok_no_speech,<br/>empty transcript, report"]
  E2 -->|yes| F["Plan chunks: hard max 29s,<br/>cut at pause nearest target"]
  F --> G["ASR each chunk in parallel with Indic-Transcribe"]
  G --> G2{"Chunk failed or empty<br/>while VAD says speech?"}
  G2 -->|yes| G3["Retry, then split chunk in half and retry, max 2"]
  G3 --> G
  G2 -->|no| H["Merge chunks, dedupe overlap,<br/>hallucination filter vs VAD"]
  H --> I["Segmenter: sentences, TTS-safe length,<br/>slot and slack per segment"]
  I --> I2{"LLM refinement valid JSON<br/>and English text unchanged?"}
  I2 -->|no| I3["Use deterministic segmentation"]
  I2 -->|yes| J
  I3 --> J["Glossary: user file plus LLM extraction,<br/>validated against transcript"]
  J --> K["Write transcript.en.json"]
  K --> L["Fork per language, isolated tasks"]
  L --> M["Translate segments with protected spans"]
  M --> M2{"Checks pass?<br/>numbers, terms, script, length ratio, placeholders"}
  M2 -->|no| M3["Retry, then LLM paraphrases English source,<br/>re-translate, max 3"]
  M3 --> M
  M2 -->|yes| N["Back-translate and LLM judge in English"]
  N --> O["Per-segment TTS fit and QA loop, see diagram 3"]
  O --> P["Timeline scheduler: offsets within 300 ms, no overlaps"]
  P --> Q["Mix: mute speech regions, place segments,<br/>normalize to -16 LUFS, mux"]
  Q --> R{"Output verification<br/>duration within 100 ms, loudness, streams"}
  R -->|fail| R2["Repair: re-pad or trim, re-mux, max 2"]
  R2 --> R
  R -->|pass| S["Subtitles SRT and VTT"]
  S --> T["segments.lang.json and language status"]
  T --> U["Join languages, write report.json"]
  U --> V{"Any language failed?"}
  V -->|yes| X3(["Exit 1: partial outputs kept"])
  V -->|no| X4(["Exit 0"])
```

---

## 3. Per-Segment Duration Fit and QA Loop

```mermaid
flowchart TD
  A["Segment: text, slot duration, slack before and after"] --> B["Predict TTS duration<br/>chars divided by calibrated chars per second"]
  B --> C{"Predicted ratio above 1.3?"}
  C -->|yes| D["LLM condenses English with char budget"]
  D --> E{"Numbers, names, glossary terms preserved<br/>and output is English only?"}
  E -->|no| F["Discard condensed text, keep original"]
  E -->|yes| G["Re-translate with Indic-Translate"]
  C -->|no| H
  F --> H["Synthesize with Indic-Speak<br/>split at punctuation if text exceeds safe length"]
  G --> H
  H --> I["Measure duration, silence, clipping,<br/>trim edge silence"]
  I --> J["Back-transcribe with Indic-Transcribe"]
  J --> K["Compute CER, repeated n-grams, tail omission,<br/>duration vs prediction"]
  K --> L{"QA pass?<br/>CER under threshold, no repeat, no omission"}
  L -->|no| M{"Attempts left? max 3"}
  M -->|yes| N["Retry planner: same text, split text,<br/>paraphrase source, condense"]
  N --> H
  M -->|no| O["Keep best candidate by CER"]
  O --> O2{"Best candidate usable?"}
  O2 -->|yes| P2["Flag degraded"]
  O2 -->|no| P3["Skip segment, keep original audio, log reason"]
  L -->|yes| P{"Fit ratio against slot plus slack"}
  P2 --> P
  P -->|"at most 1.0"| Q["Keep natural speed, pad with silence"]
  P -->|"1.0 to 1.2"| R["Time stretch with atempo or rubberband"]
  P -->|"above 1.2"| S{"Condense attempts left? max 2"}
  S -->|yes| D
  S -->|no| T["Borrow slack, stretch to 1.2 max, flag overrun"]
  Q --> Z["Placed on timeline"]
  R --> Z
  T --> Z
  P3 --> Z2["Original audio kept for this slot"]
```

---

## 4. Segment Status Lifecycle

```mermaid
stateDiagram-v2
  [*] --> pending
  pending --> translated
  translated --> synthesized
  synthesized --> verified
  verified --> placed
  synthesized --> retrying: QA fail
  retrying --> synthesized
  retrying --> degraded: attempts exhausted, best candidate usable
  retrying --> skipped: attempts exhausted, unusable
  degraded --> placed
  placed --> [*]
  skipped --> [*]
```

---

## 5. Sequence for One Segment

```mermaid
sequenceDiagram
  autonumber
  participant O as Orchestrator
  participant G as Gateway
  participant C as Cache
  participant TR as Indic-Translate
  participant L as LLM
  participant SP as Indic-Speak
  participant TS as Indic-Transcribe

  O->>G: translate(segment, lang)
  G->>C: lookup(key)
  alt cache hit
    C-->>G: cached result
  else cache miss
    G->>TR: request with retry and backoff
    TR-->>G: translation
    G->>C: store(key, result, cost)
  end
  G-->>O: translation plus cost
  O->>O: structural checks
  opt checks failed
    O->>L: paraphrase English source
    L-->>O: JSON with edited English
    O->>G: translate(paraphrase)
  end
  O->>G: speak(text, voice)
  G->>SP: request
  SP-->>G: audio file reference
  G-->>O: audio path plus cost
  O->>G: transcribe(audio)
  G->>TS: request
  TS-->>G: text
  G-->>O: transcript
  O->>O: CER, repeat, omission, fit decision
  alt QA fail and attempts left
    O->>O: retry planner chooses action
  else pass
    O->>O: commit segment to timeline
  end
```

---

## 6. Architectural Invariants and Resilience Guarantees

1. **Per-(Service, Language) Fault Isolation:**
   - Circuit breakers are partitioned as `(service, language)` tuples in `CircuitBreakerRegistry`.
   - A degradation or failure in one language's TTS (e.g. `("speak", "ta")`) trips open only that isolated circuit breaker; concurrent operations in Hindi (`("speak", "hi")`) or shared ASR (`("transcribe", "en")`) remain unimpeded.
   - Semaphore acquisition is strictly scoped around network I/O and released prior to exponential backoff sleeps to eliminate connection pool starvation.

2. **Strict LLM Boundary and Adversarial Prompt Containment:**
   - The LLM strictly processes and produces English text.
   - Transcript inputs passed to the LLM are encapsulated in `<data>...</data>` envelopes to prevent prompt injection from untrusted transcript text.
   - A Unicode character-range policy guard rejects any LLM response containing non-English / non-Latin characters (Devanagari, Bengali, Tamil, Arabic, etc.).
   - Paraphrasing and condensation enforce strict preservation of numbers, proper names, and glossary terms.

3. **Timeline Drift Prevention and Zero Audio Truncation:**
   - Timeline scheduling computes segment placement using forward-looking gap evaluation within a strict ±300ms synchronization budget.
   - Any localized delay or stretch does not cascade onto downstream segments; later segments start at their own source timestamps unless unavoidable overlap occurs.
   - Speech is never silently clipped; media-boundary overruns trigger early shifts or `truncation_risk` warnings while preserving speech content.

4. **Dynamic Speech Rate Calibration:**
   - Characters-per-second (CPS) estimation is initialized seedlessly at 12.0 chars/sec and dynamically updated per voice via Exponential Weighted Moving Average (EWMA, $\alpha=0.3$) and running medians, guarded by `asyncio.Lock` for thread-safe concurrent synthesis.

5. **Honest Multi-Language Status and Exit Semantics:**
   - Status reporting explicitly differentiates `ok`, `ok_no_speech`, `degraded`, and `failed`.
   - Budget exhaustion (`BudgetExceededError`) or circuit trips immediately cease work, flush partial artifacts (`segments.json`, subtitles, `report.json`), and exit cleanly with spec-compliant codes (exit code 0 if at least one language succeeded, exit code 1 if all languages failed).
