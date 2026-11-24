# Design notes

The harness keeps detection, judging and evaluation separate. Labels are removed before processing an item. Only human labels, supplied in the dataset or through the calibration file, enter threshold selection. Explicitly programmatic labels never fit thresholds. The included injected labels are used after detection to measure the controlled test set.

## Checks and evidence

| Check | Evidence | Handling uncertainty |
|---|---|---|
| Numeric preservation | Digit-normalized quantities and exact original spans | Missing digits are candidates because the number might be spelled out |
| Formatting | Code contents, math spans, link destinations, table column counts, fence counts | Format differences remain inspectable in the item report |
| Script coverage | Fraction of alphabetic characters in the expected script | Script never establishes language identity |
| Entity preservation | Caller-provided glossary and permitted translations | No guessed transliterations |
| Reference metrics | chrF++ and BLEU against supplied reference | No hardcoded threshold on a single reference |
| Audio quality | RMS, peak, clipping fraction, silence fraction and longest silence | Weak acoustic evidence alone remains uncertain |
| Speech coverage | Character error rate and alignment against Bodhan ASR | Semantic judgment must account for ASR noise |
| LLM judge | Exact quoted field span, English rationale, category and severity | Invalid evidence is rejected, checker-uncertain findings stay in review |

## Judge and routing

The judge receives English source, target output and any checker evidence in one JSON data object. The rubric is a separate system message. The judge must produce JSON and may only copy non-English spans verbatim. It cannot fix outputs or call a tool. Each finding is checked against the actual source field before it enters the report. Absent or repeated ambiguous evidence is rejected.

The source/translation pair and explicit glossary are sufficient for the basic semantic judgment. Back-translation supplies another observation. Audio, where supplied or selected for generation, is measured and transcribed. The default speech sample is independent of labels and stable by item-ID hash. Repeated judgments use separate cache identities so that cache hits cannot masquerade as agreement between two independent calls.

An unchanged copy of a sufficiently long Latin-script English source is evidence of untranslated output for an Indic-script request. A low expected-script ratio alone remains uncertain. When an output exactly matches its normalized reference, contradictory translation findings remain visible in review instead of automatically overruling the reference. The reference-free evaluation disables this arbitration by removing references.

Some judge responses quote missing source text but label its field as translation. The harness repairs only this narrow omission case when the quote occurs exactly once in the source and nowhere in the target. It records the repair in the evidence. All other absent or ambiguous quotes remain rejected. This does not invent or fuzzy-match a span.

## Calibration

Human-labelled items are split by a hash of `group_id`, falling back to item ID. This keeps a source and its injected variants together. Two-thirds of groups are available for threshold selection and one-third for held-out measurement. For each category with at least two positive and two negative training examples, the harness selects a confidence threshold maximizing F2, breaking ties in favor of higher precision and then a higher threshold. Unsupported categories retain the documented 0.9 review policy.

Precision and recall are null where their denominators are zero. The report separately names human annotation counts, injected labels, clean-control assumptions, source-field spans and approximate omission locations. The demo dataset has no newly human-labelled quality subset, so it demonstrates the calibration interface and reports that limitation.

## Models, cache and budget

Every speech, transcription or translation operation uses Bodhan. Translation uses the verified `/v1/chat/completions` transport with `target_language_code`. Speech uses `/v1/audio/speech` with a named voice and `instructions` containing the language. Transcription uses multipart `/v1/audio/transcriptions` and splits audio below 30 seconds. The general-purpose judge uses the caller's OpenAI-compatible endpoint.

Cache identities include exact text or an audio content hash, endpoint, model, language and generation configuration. A per-identity lock prevents two threads submitting the same request. Successful responses are written atomically. A shared semaphore bounds concurrent API requests across all items.

Before each request, the ledger reserves an upper cost using the request's output cap or measurable input length. Successful requests settle against token usage when supplied. Server failures and rate limits retry at most three times, charging a conservative reservation for every attempt. A network timeout retains its reservation and stops that operation without issuing a possible duplicate. Missing keys, failed calls and incomplete translations produce partial item results. One failed item does not stop other rows.

The cache is local to one machine. Cross-process simultaneous writers are not supported. Use one harness process per cache directory. Run-level accounting does not replace the provider's account-level billing controls.

## Reporting and review

The reports include supported flags and a separate review queue. They retain exact evidence so that a reviewer can inspect disagreements instead of relying on an aggregate score. The HTML dashboard uses escaped text. Baseline comparisons join rows by stable ID and count added and resolved categories. Changes in findings should be reviewed before concluding that model quality improved or worsened.

Format and script findings currently span the whole output, while numeric and judge findings use narrower spans. Overlap metrics therefore overstate useful localization for those broad checks. Omission injection labels use approximate source locations. Speech metrics have no seeded speech-error positives in the shipped dataset, so speech precision and recall are unmeasured.

`execution.json` and `trace.jsonl` contain invocation-specific cost and latency. Assessment files contain stable judgments, metrics and evidence. A successful cache replay can therefore produce identical assessments while reporting zero new model cost.
