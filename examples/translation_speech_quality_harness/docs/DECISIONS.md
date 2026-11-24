# Decision log

Each entry gives the decision, why, and the alternative that was rejected.

### D1. Signals, not verdicts; a weighted noisy-OR makes the decision
**Why:** checkers disagree and some are noisy. Keeping each check's output as a scored suspicion with evidence lets independent
evidence accumulate, lets strong deterministic checks act alone, and keeps noisy ones from acting alone. Calibration has one
threshold per category to tune.
**Rejected:** a rule cascade ("if numbers mismatch and the judge agrees, then…"). It is brittle, hard to calibrate, and grows with every check.

### D2. The judge answers with token indices
**Why:** the LLM must never write Indian-language text. Quoting spans would be writing it, and fuzzy-matching a quoted span back to the
text is lossy. Numbered tokens give exact offsets and make the rule enforceable; `sanitize()` strips anything non-Latin the LLM writes.
**Rejected:** "quote the erroneous span"; character offsets from the LLM (LLMs count characters badly, especially in Indic scripts).

### D3. Back-translation and ASR never flag alone
**Why:** both are themselves model outputs with their own errors (paraphrase; numbers as words, split compounds). They corroborate,
excuse, or inform the judge.
**Rejected:** a threshold on back-translation similarity or on raw WER/CER. Both conflate checker noise with system errors and vary by language.

### D4. ASR errors are separated from TTS errors by structure plus duration
**Why:** ASR noise is scattered substitutions; TTS failures are contiguous runs (deletions, duplicated insertions) that also change the
duration. Wildcards absorb numbers spoken as words, and fuzzy substitution absorbs near-misses.
**Rejected:** forced alignment (MFA or similar) for timestamps. It would add a non-Bodhan acoustic model, which the rules forbid,
and ASR returns no timestamps.

### D5. Baselines come from the dataset, not constants
**Why:** length ratio and speech rate differ by language and voice. A median/MAD over the current dataset (falling back to the pooled
baseline when a language has fewer than 8 items) works for a language never seen before, without code changes.
**Rejected:** a per-language constants table, which is unknown for unseen languages and is exactly "tuned to the demo".

### D6. Language-specific knowledge is data
**Why:** `langs.py` (script, voices, function-word fingerprints for same-script languages) and `taxonomy.py` (label aliases) are tables.
A new language or label spelling is a row, not a code change. Unknown languages degrade gracefully: script checks are skipped and
everything else runs.

### D7. The verifier runs only near the threshold
**Why:** an LLM second look is worth its cost only where it can change the decision. Confident flags, clear non-flags, and clusters
already proved by a confident deterministic check are not re-checked.
**Rejected:** judging everything twice. That doubles cost, and agreement on easy items says little.

### D8. Speech synthesis is triaged; transcription is not
**Why:** TTS costs ₹6 per 10k characters, while ASR costs ₹0.10 per minute. Supplied audio is always transcribed; missing audio is
synthesized only with `--generate`, skipping wrong-language and unspeakable items, in a reproducible hash order balanced across
languages, until the budget is reached.
**Rejected:** LLM-driven triage. It adds cost and nondeterminism to a decision a rule makes well.

### D9. Report cost is attributed, not spent
**Why:** "a rerun from cache produces an identical report". Attributed cost (what the outputs cost) is deterministic; actual spend is in
`run_meta.json`.

### D10. Translation goes through `/v1/chat/completions`
**Why:** the documented `POST /translate` returned 404 during development (19–20 Nov 2026). Chat completions with
`model="indic-translate"` and `target_language` works. The client falls back to `/translate` if chat completions ever returns 404.
Generation is capped with `max_output_tokens`, because a few long Markdown inputs otherwise ran until timeout.
Without `target_language`, the model picks a language itself (Telugu twice in the spike), so it is always sent.

### D11. The speech API's content-type is not trusted
**Why:** `/v1/audio/speech` returns RIFF/WAV bytes labelled `audio/mpeg`. The client sniffs the bytes, and anything that isn't WAV
goes through ffmpeg.

### D14. The judge sees the verbatim translation plus a token view
**Why:** with only the numbered-token view (D2), 27% of clean items were flagged, mostly "missing `=`/`%`/decimal point/table
structure". All of these were artefacts of tokenization, which drops symbols. Showing the verbatim text for judging, and the tokens
only for pointing, brought clean-item flags from 27% down to under 10%, several of which then turned out to be real Bodhan errors. Lesson: before tuning thresholds, read the false positives. A judge's errors are often caused by
its input format.

### D12. Thresholds: shipped defaults plus re-tuning when labels exist
**Why:** the hidden set comes with a small labelled subset. Re-tuning only categories with ≥6 labels, and keeping defaults otherwise,
protects against overfitting tiny samples. Grouped cross-validation reports what to expect on unseen items.

### D13. Injected errors are programmatic or English-side
**Why:** this is a ground rule, and it also keeps labels exact (we know the span because we made the change).
Mistranslation/term errors use English word swaps plus re-translation, so the error is a real model rendering of a wrong meaning.

### D15. Threshold tuning picks the middle of the best-F1 plateau, and the grid stops at 0.90
**Why:** on well-separated data, every threshold from 0.30 to 0.95 gave F1 = 1.0 for numbers, and "ties go to the highest" chose 0.95.
The digit-change signal for a corrupted number scored 0.92 (the back-translation had silently "corrected" `০০০০০০` back to
`300,000`), so the error was missed. The max-margin choice (the middle of the plateau) generalizes better, and capping the grid at 0.90
means calibration can never silence a deterministic check.

### D16. A diff uses one measuring instrument
**Why:** comparing two system versions must not also compare two calibrations. With `--baseline`, the baseline's tuned thresholds are
reused and frozen, and the verifier's band is anchored to fixed centres, so the same clusters are re-verified and hit the same cache.
Result: zero flag changes on unchanged items between v1 and v2.

### D17. On provider quota exhaustion, switch to cache-only instead of stopping the judge
**Why:** a daily quota (Groq: 200k tokens/day) will not recover within a run. Retrying wastes minutes per item, and skipping the judge
entirely would throw away verdicts that are already cached and free. The first quota error flips the LLM client to cache-only:
hits are still used, misses are noted per item, and everything else continues.

### D18. The false-alarm budget skips categories that are already at the cap
**Why:** the loop that raises thresholds until clean-item false alarms are ≤10% stopped as soon as the worst category hit the 0.90
cap, leaving 13% false alarms. It now raises the worst *raisable* category, and records when the remaining false alarms come only from
capped (deterministic or very confident) evidence. Reading those remaining "false alarms" showed most were real Indic-Translate errors,
so they went into `data/labels_review.jsonl` through the review queue rather than being tuned away.
