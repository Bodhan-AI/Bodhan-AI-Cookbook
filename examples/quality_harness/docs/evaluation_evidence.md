# Evaluation evidence and remaining gaps

These are small development experiments, not results on the evaluators' hidden dataset.

- The demo contains 108 Bodhan-generated translations in Hindi, Tamil and Bengali. The injector
  labelled 54 perturbed translations. The remaining translations have not been independently
  human-reviewed and are not ground-truth clean examples.
- A translation-only run on all 108 items, with judge and speech disabled, recorded 0.83 detection
  recall on the 54 injected items. False-alarm rate is unavailable without reviewed clean items.
- A judge-enabled run on 48 selected demo items, including 24 injected cases, detected all 24
  injected cases. The selected sample, limited error diversity and lack of human-reviewed clean
  cases mean this does not establish generalization or a false-alarm rate. See
  [the report](../sample_report_judge/summary.md) and [the exact items](../data/judge_benchmark.jsonl).
- A subsequent judge-enabled run on all 108 demo items detected 53 of the 54 injected cases.
  The [full report](../sample_report_judge_full/summary.md) does not include speech, and its
  false-alarm rate remains unavailable. It reused 48 cached judge results from the subset run;
  60 additional judge results were obtained. This is not an independent hidden-set estimate.
- A [combined run](../sample_report_combined/summary.md) scored all 108 translation items and
  selected 70 for speech checks. All 70 reached transcription-based checks without reported
  speech errors or budget skips. It used cached judge and some cached Bodhan outputs, took about
  507 seconds, and recorded ₹3.440338 of Bodhan spend in that run. Its 54 injected labels are
  translation errors; this run alone does not measure speech-error recall or false alarms.
- Two judge passes on six items agreed on verdict and category in that small sample. This is not
  enough evidence of reliable judge behavior across languages or unseen datasets.
- Speech synthesis and transcription completed for six Hindi, Tamil and Bengali items and four
  additional Marathi items. These are integration checks, not validated speech-quality scores.
- Three artificial audio faults (truncation, repetition, long internal silence) were tested with
  real transcription. Initially all three were uncertain and none became a final flag. In each
  case the recognizer returned a degenerate character loop. A fallback that transcribes two audio
  segments recovered usable transcripts; in the cached rerun one case was flagged and the other two
  remained uncertain with the correct category as a signal. These three cases were used during
  development, so this is not an independent estimate of speech recall. Compare
  [before](../sample_report_speech_before/summary.md) and
  [after](../sample_report_speech_faults/summary.md). Generated audio and model caches are excluded.
- Three additional Marathi speech faults were tested after the fallback was added. No case became
  a final flag, although all three had an uncertain signal in the correct category. This is a very
  small [independent check](../sample_report_unseen_speech/summary.md), but it shows the speech
  combiner remained too conservative. Subsequent development added an exact audio-loop signal
  and a second, segmented transcription for omission corroboration. On the same three Marathi
  cases, [two became flags and one remained uncertain](../sample_report_unseen_speech_after/summary.md);
  this is not an independent benchmark. The two omission transcripts use the same model and may
  share recognition errors. Exact audio-loop matching does not detect all repeated speech.
- A cached re-evaluation of 70 selected generated speech outputs produced no final speech flags
  after the updated checks. Those outputs are not human-reviewed, so this cannot be interpreted as
  a validated zero false-alarm rate. See [the report](../sample_report_combined_updated/summary.md).
- A fresh Windows Python 3.11 environment installed the pinned dependencies and passed the recipe
  tests and lint. Recipe validation passed in a clean staged copy against Cookbook commit
  `ae51852960911cc78bd71401842308ecc5217510`. Cookbook-wide tests had one remaining failure in the
  pre-existing `examples/AndroidSpeak` recipe; this recipe was not changed. On Windows the root
  test also requires a Unix-compatible `cp` on PATH. A Linux-container `make check` is unverified.

Before submission, obtain candidate/human-reviewed clean and error labels, measure false alarms,
improve and independently test speech-error detection and assess judge consistency on a broader
sample. The judge emits token usage in structured logs. Current code can include estimated judge
charges in the shared cap when both INR-per-million token rates are supplied; otherwise judge
charges remain unmetered. Provider bills can differ, particularly on retries or missing usage.
