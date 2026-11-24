# Technical guide: how the Indic quality harness works

This guide explains the harness from the inside: the data that flows through
it, what every check does and why it is built the way it is, how caching,
budgets and rate limits work, and how far each result can be trusted. It is
written for a developer who will extend or operate the harness.

Contents

1. [Mental model](#1-mental-model)
2. [Data formats](#2-data-formats)
3. [Pipeline walkthrough](#3-pipeline-walkthrough)
4. [The check contract: pass, fail, review](#4-the-check-contract-pass-fail-review)
5. [Deterministic translation checks](#5-deterministic-translation-checks)
6. [Reference-based and reference-free evaluation](#6-reference-based-and-reference-free-evaluation)
7. [Speech evaluation](#7-speech-evaluation)
8. [LLM-as-a-judge](#8-llm-as-a-judge)
9. [Error injection and calibration](#9-error-injection-and-calibration)
10. [Caching](#10-caching)
11. [Cost budget and rate limits](#11-cost-budget-and-rate-limits)
12. [Reports and baseline comparison](#12-reports-and-baseline-comparison)
13. [Module map](#13-module-map)
14. [Extending the harness](#14-extending-the-harness)
15. [Testing](#15-testing)
16. [Known limitations](#16-known-limitations)

---

## 1. Mental model

The harness answers one question per record: *is this Indic translation (and
its speech) faithful to the English source?* It answers with **evidence**,
not a single score, and it is explicit about how sure it is.

```
            English source ──────────────┐
                 │                        │
     (--generate)│ Indic-Translate        │
                 ▼                        │
          Indic translation ──────────────┼──► deterministic checks
                 │                        │       (numbers, units, dates,
                 │                        │        entities, Markdown, LaTeX,
                 │                        │        glossary, reference chrF)
                 │ Indic-Translate        │
   (--back-translate) ▼                   │
          English back-translation ───────┴──► round-trip checks
                 │
       (--speech)│ Indic-Speak  ──► WAV ──► audio checks
                 │                    │
                 │       Indic-Transcribe
                 │                    ▼
                 └────────────► transcript ──► speech checks
                 │
    (--llm-judge)│ local Ollama (judge only, never translates)
                 ▼
          review-only findings with quoted evidence
```

Three principles shape every design decision:

1. **Never use the system's own output as ground truth.** A generated
   translation is a *candidate*. Back-translation compares against the
   original English source. A reference check runs only on a
   `reference_translation` supplied by the dataset.
2. **Prove before failing.** A check fails only on evidence it can show (a
   number missing, a URL changed, a unit replaced by another unit). Anything a
   lexicon or heuristic cannot settle becomes `review`.
3. **Measure the detectors.** Calibration injects known errors and reports
   precision, recall and the false-alarm rate on clean text, separately for
   each detector family.

## 2. Data formats

### Dataset (`--dataset`, JSONL, one record per line)

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Unique id, used for caching speech and in reports |
| `language` | yes | Target language code (`hi`, `ta`, `bn`, ...) |
| `source_text` | yes | English source |
| `translation` | no | Indic translation to evaluate. If absent, pass `--generate` |
| `reference_translation` | no | An **independent** human reference. Enables `reference_chrf` |
| `glossary` | no | `{"source term": ["allowed", "target", "forms"]}` |

Glossary forms are matched as substrings of the translation. For
agglutinative languages list the **stem** (Tamil `டாஷ்போர்ட` matches
`டாஷ்போர்டைத்`), not one inflected form.

### Calibration set (`--calibration`, JSONL)

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Case id |
| `clean_translation` | yes | The translation before injection |
| `mutated_text` | yes | The translation after injection (identical for a clean case) |
| `injected_category` | yes | One of the labels in section 9, or `""` for a clean case |
| `source_text`, `language` | no | Enable reference-free and LLM-judge calibration |
| `injection` | no | Span, original and replacement, for auditing |

Build one with `python -m bodhan_eval.build_calibration` (section 9).

### Outputs (`--out`)

| File | Content |
|---|---|
| `summary.json` | Totals, per-category counts, every item, and a `run` block (config, cost, caches, rate limits, judge, speech and back-translation summaries) |
| `summary.md` | Human-readable report |
| `items.jsonl` | One line per record with all checks and stage outputs |
| `calibration.json` | Per-mode calibration metrics and per-case predictions |
| `baseline_comparison.json` | Regressions and fixes versus `--baseline` |
| `generated_dataset.jsonl` | With `--generate`: the dataset including generated translations |
| `translation_cache.json`, `speech_cache.json`, `llm_judge_cache.json`, `audio/` | Caches (location set by `--cache-dir`, default `--out`) |

## 3. Pipeline walkthrough

Entry point: `python -m bodhan_eval` → `__main__.main` parses flags into an
`EvalConfig` and calls `pipeline.run_evaluation`.

1. **Load** the dataset, filtered by `--langs`.
2. **Create clients lazily.** The Bodhan translate client is built only for
   `--generate` or `--back-translate`. The speech client only for `--speech`.
   The Ollama judge only for `--llm-judge`. A purely deterministic run needs
   no API key and no Ollama.
3. **Generate** (`--generate`): records with no `translation` are translated
   with Indic-Translate. Existing translations are never overwritten.
   Generated records get `translation_source: "bodhan_generated"` and are
   saved to `generated_dataset.jsonl`.
4. **Per record:**
   1. deterministic checks (always),
   2. back-translation (`--back-translate`),
   3. speech (`--speech`, only for the records chosen by `--speech-limit`),
   4. LLM judge (`--llm-judge`).

   An exception in any stage is caught and recorded in `item["errors"]` as
   `{stage, type, message}`, and the record continues to the next stage.
5. **Report**: `build_report` counts checks. The pipeline adds the `run`
   block. Then calibration, then baseline comparison, then files are written.

`--speech-limit N` picks N records round-robin across languages (N = number
of languages gives one record per language). It limits **only** speech:
translation checks still run on every record. The original code applied
the limit to the whole run, which hid translation results. That was a bug.

## 4. The check contract: pass, fail, review

Every check is a dict:

```json
{"category": "unit_mismatch", "status": "fail", "passed": false,
 "severity": "high", "evidence": {"source_units": ["km"], "translated_units": ["m"], ...}}
```

| status | passed | Counted as | Use when |
|---|---|---|---|
| `pass` | `true` | passed | Evidence found that the property holds |
| `fail` | `false` | failed | The check can **prove** an error |
| `review` | `null` | review | A signal that needs a human: transliteration, paraphrase, LLM finding, low similarity |

`report.build_report` treats `status: fail` (or, for legacy checks with no
status, `passed: false`) as failed, and `status: review` as review. Reviews
never inflate the failure count. They have their own table in the report
(`reviews_by_category`).

Optional checks (Markdown, LaTeX, dates, units, entities, glossary,
reference) are emitted **only when the source contains what they check**, so
check counts are not padded with trivial passes.

## 5. Deterministic translation checks

All live in `checks.py` and are pure functions. The lexicons they rely on
are in `lexicons.py`. All text is NFC-normalised and Unicode digits
(`१२३`, `১২৩`, `௧௨௩`) are converted to ASCII before comparison.

### Numbers: `number_mismatch`

* Extracts numbers including decimals and both Western (`10,000`) and Indian
  (`1,00,000`) digit grouping.
* Applies **scale words** after a number: `million`, `lakh`/`लाख`/`লক্ষ`/
  `லட்ச…`, `crore`/`करोड़`/`কোটি`/`கோடி`, `thousand`/`हज़ार`/`হাজার`/`ஆயிர…`.
  So "1 million" and "10 लाख" both become `1000000`.
* Compares **multisets**, not ordered lists. "8 out of 10" legitimately
  becomes "10 में से 8".
* A difference fully explained by a number written as a word ("10 AM" →
  "दस बजे") is `review`, not `fail`.

On the 100-record demo set the original order-sensitive check reported 5
failures. All 5 were false positives: 2 reorderings, 2 correct lakh
conversions, 1 spelled-out number. The new check reports 0 failures and 1
review (the spelled-out number).

### Dates and times: `date_time_mismatch`

* Clock times `H:MM` must appear in the translation.
* AM/PM, month names and weekdays are looked up in per-language lexicons.
  **A different value of the same kind is a failure** (6 PM → `सुबह`, i.e.
  morning; December → `नवंबर`). A value the lexicon cannot find is `review`.
* English aliases with capitals are case-sensitive, so the verb "may" is not
  read as the month May.

### Units: `unit_mismatch`

* Only units attached to a quantity in the source count ("2 kilometers",
  "per second"), so "wait a second" is ignored.
* Aliases are matched longest-first and consume the text they match, so
  `किलोमीटर` is km and not also metre.
* A different unit of the same dimension (km → m, MB → GB, minutes → hours)
  is a failure. A unit not found is review.
* English aliases are always accepted in the target too, because "MB" or
  "km" are often kept in Latin script.

### Entities: `entity_mismatch`

| Evidence | Result |
|---|---|
| E-mail or URL not copied verbatim | fail |
| Known place (Delhi, Mumbai, ...) replaced by another known place | fail |
| Latin-script name (Android, iOS, PostgreSQL) missing **and** an unexpected Latin name present | fail (substitution) |
| Latin-script name missing, nothing in its place | review (probably transliterated, e.g. `पीडीएफ़`) |

Code spans, math, URLs and Markdown syntax are blanked out before names are
extracted, so `\frac` or `pip install` are not reported as names.

### Terminology: `terminology_mismatch`

When a record has a `glossary`, each source term present in the source must
appear in one of its allowed target forms.

### Markdown: `formatting_mismatch` and `markdown_structure`

* `formatting_mismatch` (original check, kept): counts of `**`, `__`,
  ```` ``` ````, `$$`, `$`.
* `markdown_structure` (raw text, line breaks preserved): heading levels,
  bullet and numbered list items, blockquotes, table rows, **link URLs**,
  **inline code** and **fenced code blocks**. URLs and code must be
  byte-identical (high severity). Structure counts must match (medium).

### LaTeX: `latex_integrity`

Math segments (`$…$`, `$$…$$`, `\(…\)`, `\[…\]`) must be copied verbatim
(whitespace-normalised). Commands (`\frac`, `\sqrt`), environments and brace
balance are also compared.

## 6. Reference-based and reference-free evaluation

| | Needs | Check | What a low value means |
|---|---|---|---|
| Reference-based | `reference_translation` from the dataset | `reference_chrf`: chrF of translation vs reference | `review`: the translation is far from the reference. It might still be a valid paraphrase |
| Reference-free | nothing | all of section 5, plus back-translation below | see each check |

**chrF** (`metrics.py`) is the character n-gram F-score (n = 1..6, β = 2,
whitespace removed). It is used instead of BLEU because character n-grams are
robust to the rich morphology of Indic languages. The implementation is
dependency-free.

**The generated translation is never a reference.** `--generate` writes to
`translation`, never to `reference_translation`. If a reference is
byte-identical to the candidate, the check adds a note warning that the
reference may have come from the system under test.

### Back-translation (`--back-translate`, `round_trip.py`)

The Indic translation is translated back to English with Indic-Translate and
compared with the **original source**:

| Check | Status rules |
|---|---|
| `round_trip_number_mismatch` | Number check (source vs back-translation): fail / review / pass |
| `round_trip_similarity` | chrF(back, source) ≥ 0.5 **and** ≥ 60 % of source content words come back (light stemming) → pass, else review. Evidence lists the missing content words, a cheap omission signal |
| `round_trip_entity` | Names, e-mails and URLs from the source missing after the round trip → review |
| `round_trip_truncated` | The back-translation hit the length limit → review |

Everything except numbers is review because a round trip has *two* error
sources, and a correct translation can come back paraphrased.

## 7. Speech evaluation

`speech_service.SpeechEvaluator` runs Indic-Speak (TTS) on the translation,
saves `audio/<id>.wav`, runs Indic-Transcribe (STT, VAD on) and compares the
transcript with the text that was spoken.

| Check | Module | Meaning |
|---|---|---|
| `speech_text_comparison` | `speech_checks.evaluate_speech` | Token comparison after normalising spoken numbers ("ट्वेंटी फाइव" / "पच्चीस" / "दस" → digits). Exact → pass; repetition → fail; other differences → review |
| `speech_missing_content` | `evaluate_speech_coverage` | Ordered diff. Deleted tokens ≥ 25 % or a run of ≥ 2 deleted → fail; any deletion → review. Substitutions are not counted as missing: they are usually spelling or pronunciation variants |
| `speech_truncation` | `evaluate_speech_coverage` | A deletion that runs to the end of the text: ≥ 2 tokens or ≥ 20 % → fail |
| repetition | `detect_repetitions` | Repeated tokens and repeated bigrams in the transcript that are **not** already in the intended text |
| `audio_duration` | `audio_checks` | < 0.3 s → fail |
| `audio_duration_vs_text` | `audio_checks` | Seconds per intended token outside 0.15–1.5 → review (truncated or padded audio) |
| `audio_silence` | `audio_checks` | Share of 20 ms frames below −40 dBFS > 0.6 → review. Leading and trailing silence are reported too |
| `audio_clipping` | `audio_checks` | Samples ≥ 99 % of full scale > 1 % → review |
| `audio_sample_rate` | `audio_checks` | < 16 kHz → review |

The original sample-level silence ratio is still reported. It overstates
silence because every zero crossing inside speech counts as silent, so the
check uses the frame-level ratio.

**Voices.** By default each language gets its own male voice from the Bodhan
API reference (hi: Amit, ta: Arun, bn: Sourav, ...). The original code used
the Hindi voice "Amit" for every language. `--voice` overrides it.

## 8. LLM-as-a-judge

`llm_judge.py`, enabled with `--llm-judge`. Without the flag nothing touches
Ollama.

### What it does

The judge sends the English source and the translation to a local Ollama
model (`llama3.2:3b` by default) at **temperature 0** (and seed 0), with the
`JudgeResult` JSON schema passed as Ollama's `format` (structured output).
It returns findings:

```json
{"evidence_source": "translation", "category": "number_unit", "severity": "high",
 "span": "सुबह 8 बजे", "explanation": "...", "confidence": 0.95}
```

Categories: `omission`, `addition`, `mistranslation`, `number_unit`,
`term_entity`, `format`, `wrong_language_script`. Severity: `low`, `medium`,
`high`.

### Judge-only guarantee

* The prompt forbids rewriting or correcting.
* The Pydantic models use `extra="forbid"`, so a finding with a
  `correction` field is rejected.
* Any top-level field other than `findings` (for example a
  `corrected_translation`) is dropped and recorded as rejected. It never
  reaches a report.
* The harness never uses judge output as a translation or a reference.

### Validation (per finding)

`parse_judge_response` validates each finding independently, so one bad
finding never discards the good ones next to it:

1. **Schema**: Pydantic validation (category, severity, confidence 0..1,
   non-empty span). Failure → rejected as `schema`.
2. **Evidence side**: `omission` must quote the **source**. Every other
   category must quote the **translation**. Failure →
   `wrong_evidence_source`.
3. **Evidence exists**: the span must be a substring of that text after NFC
   normalisation and whitespace collapsing. Failure → `span_not_found_in_*`.
4. Duplicate (category, span) pairs are collapsed.

Only output that is not a JSON object with a `findings` list counts as
malformed as a whole (`status: error`).

### Failure handling

| Situation | Behaviour |
|---|---|
| Ollama not running, or model not pulled | `check_available()` at start-up disables the judge for the run with a warning. The report says `availability: unavailable` |
| Connection error or timeout mid-run | `status: unavailable`. The judge is disabled for the remaining records so the run does not wait for a timeout per record |
| Malformed output | `status: error`, recorded in `item.errors`. **Not cached**, so it is retried next run |
| `evaluate()` (original API) | Still raises `ValueError` (bad input or output) or `RuntimeError` (unavailable) |

### Caching

`llm_judge_cache.json` stores the **raw model output** under
`sha256(model, prompt_version, prompt_hash, source, translation, language,
decoding options)`. `prompt_hash` covers the system prompt, the user
template and the JSON schema, so editing any of them invalidates the cache
automatically. Validation runs again on every cache read, so improved
validators apply to cached results without re-inference. An identical
re-run performs **zero** inferences. The report shows
`inference_calls` and cache hits.

### Findings in reports

Each finding becomes a check `llm_<category>` with **status `review`**. LLM
findings are never counted as failures.

### How far to trust it

Schema validity says nothing about accuracy. On the live formatting demo
(30 real Bodhan translations), `llama3.2:3b`:

* **caught** the transliterated e-mail address in the Hindi translation
  (`बिलिंग@example.com`), quoting the exact span,
* **missed** the dropped `**bold**` in all three languages, which the
  deterministic Markdown check caught,
* produced findings on **20 of 30** items where no deterministic check
  failed, many of them obvious false positives (for example the Bengali
  word for "with" flagged as a term error).

The calibration section of the report gives its measured precision, recall
and clean-case false-alarm rate on injected errors. Read those numbers
before acting on a finding. Known limitations: a 3B model has weak
Tamil and Bengali understanding, over-reports `term_entity` for any
transliterated word, and is sensitive to few-shot examples. The prompt's
examples are deliberately *not* sentences from the test data, because when
the example equalled the test sentence the model returned no findings at
all.

## 9. Error injection and calibration

### Injectors (`injector.py`)

| Label | Injection | Example (hi) |
|---|---|---|
| `number_mismatch` | First number → 999 | 500 → 999 |
| `unit_mismatch` | Unit → another unit of the same dimension | किलोमीटर → मीटर |
| `omission` | Remove one token | |
| `addition` | Insert a meaning-bearing word ("maybe") | शायद |
| `repetition` | Duplicate one token | दूर दूर |
| `mistranslation` | Antonym swap (पहले ↔ बाद), else a content word → unrelated word. Never a unit or a place | |
| `entity_mismatch` | E-mail domain, Latin name (Android → Windows) or known place | |
| `formatting_mismatch` | Remove a `**`/`$`/backtick pair, or a Markdown link target | |

Every injection is deterministic for a given (text, language, seed) and
records its span, original and replacement.

### Building a set

```bash
python -m bodhan_eval.build_calibration \
  --dataset sample_data/demo_100.jsonl \
  --dataset sample_data/formatting_demo.generated.jsonl \
  --out sample_data/calibration.jsonl --per-category 9 --clean 15 --keep-existing
```

Records are shuffled with a fixed seed and visited round-robin by language,
so every category gets a language mix. Clean cases (no injection) measure
false alarms.

### Three modes

| Mode | Inputs | What it measures |
|---|---|---|
| `reference_based` | clean translation as reference vs mutated | Upper bound: the token diff sees every edit |
| `reference_free` | English source vs mutated (`run_deterministic_checks`) | What production finds without references |
| `llm_judge` | source vs mutated, judged by the LLM | The judge, in its own taxonomy |

Detector categories are mapped to labels (`reference_mismatch` →
`mistranslation`, `markdown_structure`/`latex_integrity` →
`formatting_mismatch`, ...) and, for the judge, labels are mapped to the
judge's coarser taxonomy (`unit_mismatch` → `number_unit`, `repetition` →
`addition`, ...).

Metrics per mode: TP/FP/FN, precision, recall, F1 per category and overall,
plus **clean-case false alarms**.

**Interpretation.** Synthetic errors are single, local, clean edits. Real
errors are subtler, so all calibration numbers are optimistic. Reference-free
deterministic checks are blind to omission, addition and mistranslation *by
design*: they cannot know meaning. That gap is what back-translation and the
LLM judge are for, and why their findings are review-only.

## 10. Caching

| Cache | Key | Value | Skipped when |
|---|---|---|---|
| Translation (`translation_cache.json`) | sha256(model, endpoint, text, target language, script) | translation, usage | Truncated output is not cached |
| Speech (`speech_cache.json` + `audio/`) | Per record id: sha256(TTS model, STT model, text, language, voice, exact `instructions` JSON, STT language, VAD) | transcript | Key differs, or the WAV file is missing |
| LLM judge (`llm_judge_cache.json`) | See section 8 | raw model output | Malformed output is not cached |

All caches are JSON written atomically (temp file + rename). A corrupt cache
file is moved to `*.corrupt` and the run continues with an empty cache.
`--cache-dir` lets several output directories share caches. The default is
`--out`, which keeps the original speech-cache location.

Changing the speech key (it now includes voice instructions and VAD)
invalidates speech entries written by the earlier version. They are
regenerated once.

## 11. Cost budget and rate limits

### `--max-cost-inr`

`cost.CostTracker` reserves an **estimate** before each paid call and refuses
the call if it would exceed the budget (`BudgetExceededError`, recorded per
item as `type: budget`). After the call it records the actual cost: output
tokens for translation, audio seconds for transcription. Cache hits are
free. `--max-cost-inr 0` therefore means "use caches only".

The per-unit rates come from environment variables (see `.env.example`).
**The defaults are placeholders, not Bodhan pricing.** The report says so
until you set them.

### Rate limits

Bodhan returns HTTP 429 when calls come too fast. A live run without pacing
got 429s after about 16 translation calls. `ratelimit.RateLimiter` wraps
every paid call:

* **pacing**: at least `--request-interval` seconds between calls (default 1),
* **bounded back-off** on 429: wait 10 s, 30 s, 60 s, then give up,
* **circuit breaker**: once a call has exhausted its back-off, later calls in
  the run fail immediately (recorded per item) instead of hammering the API.

Re-run later: everything that succeeded is cached.

## 12. Reports and baseline comparison

`summary.md` sections: overall summary, language breakdown, errors by
category, failed items with evidence, reviews by category, run
configuration, cost, caches, back-translation, speech, LLM judge, stage
errors, baseline comparison, calibration (overall, per mode, per category)
and limitations. API keys are never written: the `run.config` block lists
only CLI options.

`--baseline` takes a previous `summary.json` (or its directory).
`baseline.compare_to_baseline` reports:

* **regressions**: (item, category) pairs failing now but not in the baseline,
* **fixes**: the reverse,
* per-category deltas, plus new and missing items (not counted as
  regressions).

## 13. Module map

| Module | Responsibility |
|---|---|
| `__main__.py` | CLI flags → `EvalConfig` → `run_evaluation` |
| `pipeline.py` | Orchestration, per-stage error capture, run summaries |
| `dataset.py` | JSONL loading and validation |
| `normalize.py` | NFC, digit and whitespace normalisation |
| `lexicons.py` | Number words, scale words, units, months, weekdays, AM/PM, places, voices |
| `checks.py` | Deterministic checks and the check contract |
| `metrics.py` | chrF |
| `round_trip.py` | Back-translation checks |
| `bodhan.py` | Indic-Translate client |
| `translation_service.py` | Cached, budgeted, rate-limited translation |
| `speech.py` | Indic-Speak and Indic-Transcribe client |
| `speech_service.py` | Speech cache and speech evaluation |
| `speech_checks.py` | Transcript comparison, coverage, repetition |
| `audio_checks.py` | WAV inspection and audio checks |
| `llm_judge.py` | Ollama judge, validation, cache |
| `injector.py` | Labelled error injection |
| `calibration.py` | Calibration modes, metrics, set builder |
| `build_calibration.py` | CLI for building calibration sets |
| `cache.py`, `cost.py`, `ratelimit.py` | Infrastructure |
| `baseline.py` | Run-to-run comparison |
| `report.py` | `summary.json` and `summary.md` |

## 14. Extending the harness

* **A new language**: add aliases to the lexicons in `lexicons.py`
  (numbers, scale words, units, months, weekdays, AM/PM, places) and a voice
  in `DEFAULT_VOICES`. Without lexicon entries the checks fall back to
  `review` rather than failing.
* **A new check**: write a pure function returning `make_check(...)` (or
  `None` when not applicable), call it from `run_deterministic_checks`, map
  its category in `calibration.DETECTOR_TO_LABEL` if it detects an injected
  label, and add tests.
* **A new error type**: add an injector, register it in
  `SUPPORTED_CATEGORIES` and `inject_labelled_error`, and map it in
  `LABEL_TO_JUDGE`.
* **A different judge model**: `--judge-model qwen2.5:7b`. The cache key
  includes the model. Re-run calibration before trusting it.

## 15. Testing

```bash
python -m pytest -q                 # everything, offline (the live Ollama test skips itself)
python -m pytest -q -m integration  # only the live Ollama test
```

The tests never call Bodhan. The pipeline tests use fake translate, speech
and judge clients, and the judge tests use a fake Ollama HTTP session. The
one live test (`test_llm_judge_integration.py`) is skipped automatically
when Ollama or the model is not available.

## 16. Known limitations

* Lexicons are small and cover hi, ta and bn. Misses become `review`, but
  an unknown *rival* (for example a month spelling not in the list) cannot
  produce a failure.
* Number words are used only to downgrade failures to review. `एक`
  ("one", also "a") can mask a genuinely missing "1".
* LaTeX detection treats any `$…$` pair as math. Dollar amounts in a source
  could be misread.
* Reference-free deterministic checks cannot detect omission, addition or
  mistranslation. Use back-translation and the judge, and read them as
  review signals.
* Back-translation uses the same model family in both directions, so it can
  hide errors that the model makes consistently.
* Speech checks compare tokens. They cannot judge pronunciation, prosody or
  naturalness, and transcripts may spell loanwords differently.
* Audio thresholds (silence, clipping, seconds per token) are heuristics
  tuned on a handful of Indic-Speak outputs.
* Cost figures are estimates from configurable rates. The defaults are
  placeholders.
* Calibration uses synthetic single edits. Its numbers are an optimistic
  sanity check, not a measurement of real-world accuracy.
