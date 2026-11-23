# Architecture

The harness is a staged pipeline: cheap deterministic checks first, the LLM judge next, and the
paid speech checks last and only where they pay off. Every check emits typed `Evidence`; a single
**combiner** turns evidence into flags or marks an item *uncertain*. A disk **cache** and an INR
**cost ledger** wrap every model call.

```mermaid
flowchart TB
    subgraph Inputs
      DS[(JSONL dataset)]
      CFG[config/<br/>languages.yaml<br/>bodhan_api.yaml<br/>thresholds.json]
    end

    DS --> LOAD[Stage 0: load + validate<br/>bad lines logged & skipped]
    LOAD --> NORM[normalize.py<br/>NFC, digits→ASCII, offset map]

    subgraph Deterministic checks (free)
      T1[T1 script/language]
      T2[T2 number/unit]
      T3[T3 markdown/LaTeX]
      T4[T4 length ratio]
      T5[T5 reference chrF++]
      T6[T6 term/entity]
      T7[T7 sentence coverage]
    end
    NORM --> T1 & T2 & T3 & T4 & T5 & T6 & T7

    NORM --> RT[Round-trip<br/>Indic-Translate → English<br/>NOISY]
    NORM --> JUDGE[LLM judge<br/>blind, rubric, verbatim quotes<br/>NOISY]

    T1 & T2 & T3 & T4 & T5 & T6 & T7 & RT & JUDGE --> EV{{Evidence bus}}

    EV --> TRIAGE[Triage<br/>suspicious + stratified sample]
    TRIAGE -->|selected| SPEECH
    subgraph SPEECH[Speech checks]
      SIG[Signal: duration/silence/clipping<br/>free, ffmpeg/soundfile]
      ASR[Indic-Transcribe back-transcription<br/>CER/WER, repetition, omission<br/>PAID, NOISY]
    end
    SPEECH --> EV

    EV --> COMB[Combiner<br/>strong det → flag alone;<br/>noisy → needs corroboration;<br/>else → uncertain]
    COMB --> CAL[Calibration vs labels<br/>precision/recall, fit thresholds]
    COMB --> REP[Reports<br/>summary.md/json, items.jsonl,<br/>calibration.json, diff.md]

    subgraph Cross-cutting
      CACHE[(sha256 cache<br/>byte-identical reruns)]
      COST[Cost ledger INR<br/>hard cap, reserve-before-spend]
      LOG[Structured JSONL logs]
    end
    RT -.-> CACHE
    JUDGE -.-> CACHE
    ASR -.-> CACHE
    RT -.-> COST
    ASR -.-> COST
    CFG --> NORM
```

## One line per check
- **T1 script_language** — fraction of characters in the expected script; catches wrong script and untranslated output. *Strong, deterministic.*
- **T2 number_unit** — numbers compared as multisets after digit normalization (`२५`==`25`); exact digit span. *Strong, deterministic.*
- **T3 format** — Markdown/table/LaTeX structural parity and math-body equality. *Strong, deterministic.*
- **T4 length_ratio** — target/source length z-score; weak support for omission/addition. *Weak, noisy.*
- **T5 reference_chrf** — chrF++ vs a reference when present, with localization. *Medium, noisy.*
- **T6 term_entity** — acronyms/terms preserved and rendered consistently. *Medium.*
- **T7 sentence_coverage** — sentence alignment for omission/addition localization. *Weak/medium, noisy.*
- **Round-trip** — back-translation chrF++/overlap vs source. *Medium, noisy — never flags alone.*
- **Judge** — blind rubric pass; categorized, localized, verbatim-quoted errors. *Medium, noisy.*
- **S1–S7 speech** — duration/silence/clipping (free) and ASR CER/WER + repetition/omission (paid, noisy).

## Why the combiner is the core
Back-translation, ASR, chrF and the judge all make their own mistakes. The combiner encodes the
one rule that separates *system* errors from *checker* errors: strong deterministic evidence may
flag alone, but a noisy signal must be corroborated (by a second independent noisy check or by the
judge with a verbatim-verified quote) before it becomes a flag. A lone noisy signal makes the item
*uncertain* and sends it to the review queue instead of raising a false alarm.
