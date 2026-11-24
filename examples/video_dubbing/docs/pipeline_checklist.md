# Running pipeline checklist

Current implementation evidence, November 22. Earlier experimental counts and incomplete draft videos are superseded by the current demo in `outputs/dubbed-demo-translation-fixed/`.

- [x] Validate video streams/duration, output paths and production target codes.
- [x] Extract timeline-aligned mono PCM; preserve the original video.
- [x] Local CPU VAD and bounded overlapping ASR windows, at most fifteen seconds in the full CLI.
- [x] Bodhan source ASR, request pacing, retry/cooldown limits and successful-response caches.
- [x] Source-validated English reconciliation, correction audit and connected thought grouping.
- [x] Bodhan translations, stable unit IDs, configurable voices and glossary hints.
- [x] Bounded Bodhan speech pieces, raw-audio validation and back-transcription CER.
- [x] Physical fitting: safe silence handling, bounded neighbouring free time, at most 1.2× tempo.
- [x] Last-resort English condensation, guarded facts/labels/numbers, Bodhan retranslation/regeneration.
- [x] Continuations reuse successful raw speech and retain the combined spending ceiling.
- [x] Complete-phrase placement: no energy-based waveform cuts, no detached first words before pauses; regenerate and recheck affected audio.
- [x] Strict fit validation: complete groups, every fitted sample once, no speech overlaps or protected-pause violations.
- [x] −16 LUFS normalization, proportional phrase subtitles, unchanged-duration video mux.
- [x] Silent source gaps: the original mixed track is never copied into the dub, even after a false VAD non-speech label.
- [x] Main assignment command, partial-language failure isolation, structured reports and events.
- [x] Thirty-second smoke tests with real media tools and mocked hosted transports.
- [x] Actual saved-run demo: Hindi/Tamil/Bengali, twelve groups each, zero skipped groups, all videos 329.267 seconds.
- [x] Code walkthrough and architecture/workflow/design documentation updated.
- [x] Repair the eleven selected translation/numerical/terminology and missing-instruction findings; regenerate and check changed audio. Latest manifest: `outputs/translation-quality/fit_manifest.revision-008.json`, no unresolved selected groups.
- [x] Reference-backed optional glossary, contextual explicit numbers, pre-speech meaning gate and cumulative budgeted quality-repair resume.
- [ ] Independent native-speaker review of terminology, pronunciation and naturalness; automated checks are not certification.
- [ ] Review natural phrasing and source sync; ±300 ms timing has not been established.
- [ ] Live-validate optional code-mixed/glossary route and unseen production languages as budget/time permits.
- [x] Docker image build, installed-wheel CLI, offline CPU VAD, dependency checks, real demo preparation and all 483 tests verified on Linux ARM64.
- [ ] Verify a literal fresh clone of the final submitted commit; Linux AMD64 is untested.
- [ ] Complete public output hosting and the required short demonstration recording.
- [ ] Complete user-approved Git, PR and email submission steps.

Checks: recipe tests, dependency consistency and recipe validator pass. Root lint/rule checks pass. Cookbook `make check` fails on the existing AndroidSpeak example outside this recipe; it was left unchanged.

Advanced separation, speaker diarization, OCR and the final web UI remain deferred extras. No recording/browser control or publishing was performed. Fit recovery and its final content check cost approximately ₹5.62, within the original combined ₹10 recovery authorization. Costs are estimates from journals, not a live account balance.

Translation-quality evidence: 483 tests passed. The selected repair pass used approximately ₹4.61 of its approved ₹6 cumulative cap. The original fit authorization and its ₹5.62 remain separate. No Git, recording, browser-control or publishing action was performed.
