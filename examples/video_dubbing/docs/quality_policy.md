# Current correction — November 22

The user found a word split around a source pause in Hindi group two and explicitly authorized a generalized fix, including preventing an intact first word from being stranded before its sentence. This correction is complete.

The placement routine no longer uses waveform energy to choose cuts. It requires independently generated complete sentence/heading groups and validates both text boundaries and sample/file provenance. Production synthesis keeps complete phrases together at API request boundaries too. Failed phrase arrangements go through bounded English-only shortening and Bodhan regeneration, or fail explicitly. Newly generated audio cannot inherit old-waveform QA. Cached manifests containing unsafe splits are rejected.

Current corrected videos/subtitles: recipe `outputs/dubbed-demo-phrase-safe/`. Validated fit: `outputs/phrase-safe-final/fit_manifest.json`. Focused Hindi preview: `hi/segment-02-review.wav` under the output. All 36 groups fit, zero skipped; all three videos measure 329.267 seconds. Narration LUFS: Hindi -16.13, Tamil -16.12, Bengali -16.35. Nine affected groups regenerated. All repaired-group QA hashes match the actual new waveform. Hindi group two pauses after its second sentence and keeps the complete Apni notebook instruction after the gap.

448 recipe tests pass. Combined recovery/correction cost from linked journals is approximately INR 5.6174658333, within the original combined INR 10 cap. The local cost report covers prior experiments separately; no account balance was queried. Translation/naturalness and precise sync remain review work. Other original permissions remain: no Git mutation/push, publishing, email or browser control. The main command and saved-run renderer use this same generalized logic.

## Earlier evidence below (superseded where different)

# Current fit and output policy

Current status (November 21): the required CLI, measured fitting, loudness, subtitles and MP4 rendering are implemented. All 36 connected language/group cases fit with no skipped narration, no speech overlaps and speed at most 1.2×. Source sync is estimated; translation/naturalness fixes remain deferred. The current demo is `outputs/dubbed-demo-complete/`. Older sections below are historical evidence, not current authorization or status.

Only languages with every source group physically fitted are finalized. Successful independent languages survive another language failure. Word-level alignment, naturalness and translation correctness are not certified by a physical fit or low CER. Retry/continuation cost is summed from journals rather than stale manifest summaries.

## Historical design notes

# Dubbing quality policy

Agreed overall design. Media preparation and initial VAD are implemented; dubbing, timing recovery, and speech verification remain pending.

## Continuity

- [ ] Use speech detection to locate speech and pauses, not to recognize words or identify speakers.
- [ ] Keep transcription windows within 30 seconds. Preserve source offsets; do not claim word-level timestamps without evidence.
- [ ] Group connected English phrases into complete thoughts for translation and synthesis where model limits allow. Keep connecting phrases with the statement they introduce rather than synthesizing conjunctions alone.
- [ ] Preserve meaningful pauses; avoid inserting artificial gaps at every technical chunk boundary. Listen for abrupt restarts. Fades may smooth joins but cannot repair unnatural phrasing.

## Timing recovery, in order

- [ ] Preserve the original wording and natural pacing when speech fits; shorter speech need not fill every instant of its slot.
- [ ] Use available surrounding silence without colliding with the next segment or breaking meaningful timing.
- [ ] Apply only gentle pitch-preserving speed adjustment, within the assignment range of 0.85–1.2x. Choose a conservative operational threshold after listening tests; being within the range does not prove naturalness.
- [ ] Only if safe timing adjustments fail, condense English while preserving names, numbers, formulas, conditions, reasoning steps, and the teaching point. Bodhan must translate the revision and generate its speech; the LLM must never rewrite target-language text.
- [ ] Bound recovery attempts, record original/revised text and reasons, and report unresolved fit or quality-target misses. Never silently cut off spoken words.

## Verification

- [ ] Decode and measure audio; check duration, clipping, segment placement, and final output duration/loudness.
- [ ] Back-transcribe generated speech with Bodhan and report per-segment CER against the requested translation, with documented normalization.
- [ ] Flag suspected omissions/repetitions for bounded recovery; account for possible verifier errors rather than treating CER as proof of meaning or naturalness.
- [ ] Listen to representative clips for continuity, expressions, rushed delivery, and unnatural joins. Automated checks do not guarantee natural speech.
- [ ] Keep verification, recovery, costs, timing decisions, and incomplete results visible in the report. Do not claim a quality check passed before it was performed.

## Pending decisions

- Silero CPU VAD is installed and implemented; 44 tests passed with the positive speech fixture. Demo boundaries remain estimated, not fully reviewed by listening.
- VAD thresholds, grouping rules, naturalness/speed thresholds, QA thresholds, and exact retry limits still need testing and agreement.
- Background music treatment and speaker-aware voice assignment are separate concerns; no source-separation or diarization feature has been implemented.


## Measured implementation boundary

Source-supported overlap correction, explicit correction decisions, group word coverage, translation label/script/digit checks and speech format/duration checks are implemented and tested. Every accepted correction records Bodhan source evidence; no LLM knowledge is used to supply missing speech. These checks do not establish complete recognition or native-language fidelity.

Back-transcription CER, final audio fitting, ±300 ms start measurements, non-speech preservation in assembled tracks, −16 LUFS normalization and ±100 ms muxed duration remain pending. Current speech files are unverified/unfitted intermediates. Some grouping bounds overlap and must never be used as final SRT timestamps. Skip/failure decisions must remain unattended and reported rather than requiring an evaluator to correct text.
