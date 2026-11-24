# Design notes

## Principles
1. **Ordered stages.** The current implementation back-translates before deterministic checks,
   then invokes the judge. Speech synthesis and ASR run last and are triaged and budgeted.
2. **Checks propose, the combiner decides.** No check raises a flag. Each emits `Evidence` with a
   `strength` and a `noisy` flag; the combiner is the single place that turns evidence into a
   flag, an *uncertain*, or nothing.
3. **Separate system errors from checker errors.** Back-translation, ASR, chrF and the judge are
   noisy. A noisy signal alone never flags; it needs a second independent noisy check or the judge
   with a verbatim-verified quote. Otherwise the item is *uncertain* and goes to the review queue.
4. **Generalize, don't special-case.** Languages come from `config/languages.yaml` and scripts are
   detected by Unicode block, so unseen languages work. Thresholds come from calibration, never
   from `if lang == "hi"`.
5. **Deterministic and cached.** Every model output is cached by sha256 of its exact input; the
   report is byte-identical on a cached rerun and costs nothing. Timestamps live only in
   `meta.json`, never in the scored outputs.

## Normalization (normalize.py)
NFC → strip zero-width → map every Indic/Arabic digit to ASCII (`unicodedata.decimal`) → unify
punctuation (danda→`.`, curly quotes, dashes) → collapse whitespace. Each normalized character
carries the `[start, end)` of the raw characters it came from, so flags report spans in **original
coordinates**. `norm_for_asr` additionally lowercases Latin and drops punctuation for CER/WER.

## The checks
See [architecture.md](architecture.md) for the one-liners. Key points:
- **T2 number_unit** extracts numbers with units and compares multisets by value, with unit
  tolerance so a unit written as a word doesn't cause a false alarm. Missing/changed numbers are
  *strong* evidence with the exact digit span.
- **T3 format** checks structural parity (headings, lists, tables, fences, links, math delimiters)
  and that **math bodies are byte-identical** (math must never be translated). It also checks the
  translation's own brace/`$`/environment balance.
- **T6 term_entity** confirms acronyms/alphanumeric terms survive verbatim; proper nouns are
  treated gently (they transliterate) and only flagged when the back-translation confirms absence.

## The judge (judge.py, rubric.md)
A **blind** pass: it sees source + translation (+ reference) but **not** the deterministic
findings, to avoid anchoring. The rubric forces a clause-by-clause alignment **before** the
verdict, to counter fluency bias. The harness:
- validates every quote is a **verbatim substring** and computes the offset itself (LLM offsets are
  never trusted); non-verbatim quotes are demoted to unlocated, lower-weight evidence;
- runs at temperature 0 with a seed and caches by prompt + repeat index;
- measures consistency with `--judge-repeats N` (verdict and category agreement per language) and
  routes disagreements to the review queue;
- is provider-agnostic: it tries JSON mode and falls back to text parsing with light JSON repair.

The judge may **read** Indic text but never writes or corrects it (enforced by the rubric and by
discarding any non-verbatim quote).

## Combiner (combiner.py)
Per category: strong deterministic → flag (conf 0.95); medium deterministic → flag (0.78); two or
more independent noisy checks → flag (0.62); one noisy + judge-with-verbatim-quote → flag (0.72);
judge alone with a verified quote may flag the semantic categories deterministic checks can't see
(omission/addition/mistranslation/term_entity), scaled by the per-language judge reliability.
Everything else with a signal becomes *uncertain*. Severity is the max of contributing evidence;
spans prefer the strongest evidence. Judge-vs-check disagreement is counted explicitly.

## Calibration (calibrate.py)
Labels (in the dataset and/or `--calibration labels.jsonl`) drive precision, recall, F1,
localization overlap and category accuracy per category and language. Thresholds are fit by a
greedy grid search that **re-runs only the cheap combiner** over already-collected evidence
(no new model calls), maximizing overall F1 under a ≤10% clean-item false-alarm constraint.
Low-support cells (<5 labels) are marked. Only `judge_confidence_cut` is fitted by recombination;
thresholds used when producing evidence cannot be fitted this way. Missing labels mean unknown;
an explicit empty list means reviewed clean. Label spans use their declared space (source by default).
Fitted thresholds are
written to `<out>/thresholds.fitted.json`; pass them back with `--thresholds` to apply — the
committed defaults are never tuned to a dataset, which keeps reruns deterministic.

## Speech (speech.py)
Signal checks (duration vs chars-per-second band, silence runs, clipping) are free. ASR back-
transcription is compared to the spoken text with our own CER/WER. ASR is noisy, so a high CER only
becomes evidence when it exceeds the per-language baseline + margin **and** the edit operations are
structured (a contiguous tail/internal deletion, or a repeated n-gram), or a signal check agrees.

## Cost & reliability
Rates live in `config/bodhan_api.yaml`. Budget is reserved before each paid call; when the cap
would be crossed the call is skipped. Translation reservations use a requested output-token cap;
actual provider billing, failed requests and unmetered judge calls can differ from these estimates.
API calls
go through a concurrency limiter and retry with exponential backoff + jitter, honoring
`Retry-After`. The **logical** cost (cache-independent) is what the report shows; **actual** spend
(0 on a cached rerun) is in `meta.json`.

## Bodhan API assumptions
The client uses `/v1/chat/completions`, `/v1/audio/speech` and `/v1/audio/transcriptions`. The demo
was translated live and one Hindi speech synthesis and transcription run succeeded. Longer audio is
split at fixed time boundaries; long speech input is split at text boundaries. See the README.
