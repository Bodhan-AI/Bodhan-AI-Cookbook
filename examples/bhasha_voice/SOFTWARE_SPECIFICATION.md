# BhashaVoice SDK — Software Specification

## 1. Document details

| Field | Value |
| --- | --- |
| Owner | Muhammad Umaid Rather |
| Version / updated | 0.4 / 2 November 2026 |
| Status | Implemented local v0; peer review and Cookbook PR pending |
| Cookbook location | `examples/bhasha_voice/` |
| Companion | [ARCHITECTURE.md](ARCHITECTURE.md) |

This replaces the October 16 proposal. Selection translation is now implemented;
voice comments/annotations are not part of the current release.

## 2. Summary

A reusable React package adds microphone dictation and selected-text translation
to a host editor. A self-contained Next.js example supplies the server routes.
The npm package is local and private; publication is not required for this recipe.

## 3. Problem and users

React developers need reusable recording, transcript review, cursor insertion and
translation controls. Visitors can speak in a supported language, correct AI output
and insert it; readers can translate a selected paragraph without an input field.
The host app owns its editor, insertion callback, authentication and persistence.

## 4. Goals, non-goals and V0 scope

Implemented: selectable Bodhan speech languages, selectable translation targets
and script variants, recording/cancellation, review/edit/copy/insert, five recent
inserts in memory, selection translation, guarded editable replacement and errors.
Excluded: comments, storage, streaming transcription, long-audio chunking, model
training, offline AI, speech synthesis, rich-editor adapters and npm publication.
Language support means routing/validation/UI coverage; it does not certify model
accuracy. See [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md).

## 5. Platform and form factor

Browser React components (`@bhashavoice/react`) with React/React DOM 19.3 peers.
Demo: Next.js 16.4.2, Node.js 22.20+, FFmpeg. Microphone needs localhost or HTTPS.
A plain React/TypeScript consumer is also maintained in the development repository.

## 6. User journeys and functional requirements

1. Choose a spoken language, allow microphone access and record up to 25 seconds.
2. Stop uploads audio; Cancel before Stop discards it without uploading.
3. Review, edit or copy the result. Insert calls the host callback at its saved
   selection; a rejected callback keeps the transcript available for retry.
4. Select up to 2,000 UTF-16 code units and activate the translation launcher.
   The host can configure `defaultTargetLanguage` (SDK default: English).
5. Change the target to translate again. The model infers the source language;
   the UI does not guess source from script. Original and result remain visible.
6. Replace only a still-valid editable selection. Intervening edits block replacement.
   Read-only text supports viewing and optional copying, never replacement.
7. Preserve retry backoff, cancellation and safe errors. No automatic paid retries.

## 7. Bodhan API usage

- `indic-transcribe`: multipart WAV and explicit language to
  `https://api.bodhan.ai/v1/audio/transcriptions`.
- `indic-translate`: existing `/v1/chat/completions` integration, one user message,
  `target_language_code`, no source field or generation controls. Accept exactly
  one nonempty completed choice; reject incomplete/truncated output.
- Host routes: `/api/bhasha/transcribe`, `/api/bhasha/translate`.
- Separate per-model language lists; see language support document and the
  [Bodhan API reference](https://console.bodhan.ai/api-docs/).

## 8. Non-functional requirements

Audio: <=25 seconds, <=5 MiB. FFmpeg output: mono 16 kHz PCM16 WAV.
Translation: <=2,000 UTF-16 code units; JSON body <=32 KiB.
Provider responses <=64 KiB; overall handler deadline 30 seconds; provider deadline
20 seconds. Two active requests per endpoint per server process. Text uses automatic
bidirectional layout. Cards fit narrow viewports and support reduced motion.

## 9. Cost and usage estimate

Bodhan calls use account credit. No fixed price is embedded in this specification;
use the current console pricing. Automated contract tests stub provider responses.
Live checks and demo calls spend credit and are recorded separately from unit tests.

## 10. Security, privacy and data handling

Server-only keys; no credentials in React props or public environment variables.
Temporary audio is deleted after processing. Text/history clear on refresh.
Audio and selected text are sent to the host backend and Bodhan on explicit action.
Cancellation cannot guarantee an accepted provider call is unbilled. Raw upstream
errors, keys, filenames, audio and transcript contents are not logged by handlers.

## 11. UX and design

Compact microphone expands into a transcript card. Language labels are readable
and selectors are bounded on small screens. Translation shows original/result,
script-specific targets and automatic text direction. Default translation target
is explicit and host-configurable. Demo disables translation Copy buttons.

## 12. Success criteria and demo plan

Every published code reaches the correct provider field in contract tests; invalid
capability combinations are rejected before a paid call. Live tests report per-code
outcomes separately. The 2 November recording shows English microphone dictation,
transcript review/edit/insertion, language selection, selected-text translation,
Urdu replacement and Hindi translation of a read-only paragraph. The delivered
44.8-second edit uses actual moving footage at 2940 × 1912 and 60 fps; see
[DEMO.md](DEMO.md). The user reported that the physical microphone works in that
setup. The official Cookbook Hindi sample also passed live transcription.
Other speech languages and browser/device combinations need separate live checks.
The silent recording and smoke checks do not establish human linguistic quality.

## 13. Milestones and timeline

Completed locally: package extraction, portable recipe, language expansion and
automated checks. Current delivery: updated docs, live smoke checks and demo.
Next: peer review, distribution license decision, deployment and Cookbook PR.

## 14. Risks, dependencies and open questions

Provider docs may change. Native-speaker quality review is outstanding. A public
demo needs authentication, shared quotas and production FFmpeg packaging.
Script variants are capability-specific; never reuse the speech allowlist as the
translation list. Framework-managed rich text needs a replacement adapter.

## 15. Open source and contribution

The package remains private/UNLICENSED. As of 2 November 2026, no BhashaVoice-specific
license decision was found in the reviewed Cookbook channel and maintainer
conversation. Keep this status unless the owner and maintainers explicitly
provide a distribution license. No publication or submitted PR is claimed. Submit the portable recipe
against Cookbook `main` after required review and validation. Keep secrets and
real user recordings out of the public contribution.

## 16. Review

Dhara/Ishvinder review pending. Review language/code coverage, fresh-clone setup,
actual provider behavior, native-script rendering, replacement safety and demo.
