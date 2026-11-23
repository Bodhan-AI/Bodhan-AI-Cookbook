# Quality targets and engineering audit

Audited against the Video Dubbing Pipeline assignment on November 22. This distinguishes measured results from implementation and unverified targets. None of these statements promises a perfect result for unseen videos. The assignment treats the quality figures as targets and explicitly requires misses/reasons in `report.json`.

## Every quality target

| Assignment target | Current evidence | Status / remaining action |
| --- | --- | --- |
| Every dubbed segment starts within ±300 ms of its source | Source positions come from transcript-window references and VAD. The strict fit check validates placement against those estimated slots. | **Unverified.** Matching an estimated slot is not independent verification of the spoken source boundary. Do not claim ±300 ms from the fit validator. |
| Time stretch remains between 0.85× and 1.2× | Final group speeds range from 1.0× to 1.2×. Strict validation also checks independent phrase pieces. | **Pass on current demo.** A candidate exceeding the bound must recover or fail explicitly. |
| All source speech dubbed or skipped with a reason | 36/36 recognized source groups rendered: 12 in each language; no skips. Manifest validates group coverage and sample ownership. | **Pass on current recognized transcript.** This cannot independently prove that ASR omitted no source words. |
| Back-transcribe speech using Indic-Transcribe and report per-segment CER | All 36 groups have CER. Changed repair waveforms have hash-bound recognition evidence. There are 12 recognition warnings across the final groups. | **Reporting requirement met.** High CER is a warning, not proof of bad speech; no threshold or native quality certification is invented. |
| Video duration within ±100 ms | Each output is 329.267 seconds, about 0.333 ms from the source. Original video stream copied. | **Pass on all three outputs.** |
| Speech normalized to −16 LUFS ±1 | Hindi −16.11; Tamil −16.15; Bengali −16.28 LUFS. | **Pass on all three narration tracks.** |
| About 5-minute input, 3 languages, hosted completion under 30 minutes | Final saved-audio render took 18.4 seconds and made no new hosted calls. Hosted work and fitting finished across saved continuations. | **Unverified.** This offline figure is not a fresh full hosted benchmark. A separately authorized, timed complete run is needed to verify the target. |

The current top-level `report.json` now includes all seven entries under `quality_targets`, with evidence, allowed bounds and unknown/miss reasons. `execution_scope` distinguishes saved-audio rendering from the full hosted pipeline. `runtime_under_30_minutes` is null when the reference hosted benchmark is not established. This makes a fast offline render impossible to misrepresent as that benchmark.

## Every engineering expectation

| Assignment expectation | Evidence | Status / remaining action |
| --- | --- | --- |
| One-command clean-clone setup, pinned dependencies, system tools documented; Docker recommended | `scripts/setup.sh`, pinned `requirements.txt`, `pyproject.toml`, README, Dockerfile. Local environment, dependency consistency and recipe validation pass. | **Docker verified on Linux ARM64.** The isolated image builds, all 507 tests pass inside it, installed-wheel CLI/VAD/media checks pass offline and the real demo prepares successfully. See `docker_validation.md`. Linux AMD64 and a literal fresh final-commit clone remain untested. |
| Failure in one language does not stop the others; nonzero exit, partial outputs/report | Real-FFmpeg 30-second integration test injects a Bengali translation failure, retains Hindi video/subtitles and checks nonzero exit and failed-language report. Native back-translation failures also have language-isolation unit coverage. | **Tested with mocked hosted responses.** Shared input/ASR/config/budget failures can stop the whole job; that is distinct from an isolated target-language failure. |
| Bounded retries/backoff and API concurrency | ASR and TTS clients, translation client, request pacing/cooldown, per-call timeout, cache journals and conservative reservations. Main command uses one ASR request / speech worker at a time; translation is bounded and sequential. Tests cover pacing, unknown outcomes and budget refusal. | **Implemented and tested.** An uncertain paid request is not automatically repeated. Do not claim unlimited retry availability or a rate-limit-free provider. |
| Structured stage and segment logs, traceable line decisions | Provider request/response journals, source references, corrected text, source/translation revisions, fit decisions, QA and render `_render/events.jsonl`. New full CLI executions also persist cumulative stage status/elapsed time in top-level `events.jsonl`, matching structured stdout. | **Implemented and integration-tested.** Trace files contain text, decisions and measured artifacts; no keys/authorization headers are logged. Older runs retain their original available logs rather than fabricating missing historical events. |
| Unit tests for segmentation/timing; end-to-end 30-second smoke | Boundary ownership/reconstruction, VAD regions, overlap reconciliation, source timing, whole-phrase/no-word-cut placement, speeds, pauses, samples, subtitles, budget safety and actual FFmpeg media processing. `tests/test_pipeline.py` exercises a 30-second input with mocked hosted transports and actual outputs. | **Tested.** The smoke is explicitly mocked, not a live provider benchmark. Full latest test result is recorded in README/submission evidence. |

## Close-out order

1. Docker image build, package/FFmpeg checks, CLI and offline smoke are complete on Linux ARM64. Review a fresh clone of the final submitted commit before submission.
2. Defer the fresh hosted benchmark until after submission. Preserve the current reports and distinguish their cached stages from the saved render.
3. Inspect source/dub starts independently where sync matters. Keep the ±300 ms status unverified unless evidence establishes it; do not add a prohibited speech/alignment provider.
4. Review native pronunciation/naturalness and remaining CER warnings as time permits; do not let literal script differences masquerade as proven audio errors.
5. Record the required full-run demonstration, publish approved outputs/recording without sign-in, and finish README/PR links and the exact evaluated commit/email.

No new paid API calls, public publishing or Git mutation was required for this audit. Optional UI/OCR/diarization work stays behind submission-critical work.
