# Multilingual demo

Updated: 2 November 2026.

## Delivered walkthrough

`bhashavoice-demo-full-resolution.mp4` is a 44.8-second silent edit of the
user's actual screen recording, exported at its native 2940 × 1912 resolution
and 60 fps. Moving video is used throughout. Cuts remove idle time, discarded
attempts and switches away from the app; focused zooms and short labels guide
attention to the real controls and results.

The recording shows:

1. English microphone recording, Stop and the returned transcript.
2. Reviewing the sentence “Let us meet tomorrow at ten in the morning”, editing
   “ten” to “10”, and inserting it into the editor.
3. Opening the spoken-language selector to show the available choices.
4. Translating the selected sentence, changing targets, and replacing it with
   the Urdu result. Hindi and Tamil results are also visible in the edit.
5. Selecting text in the read-only English paragraph and viewing its Hindi
   translation. The final editor contains Urdu and Hindi text.

This recording replaces the earlier 21-second screenshot walkthrough. It uses
the actual app interactions and returned results; no interface or provider
responses were reconstructed for the edit. Its waits are shortened, so it is
not an end-to-end latency benchmark. The screen recording contains no audio
track and does not support an independent comparison of speech with transcript.

The MP4 and editable HyperFrames project are delivered separately from the
source repository. Attach the recording to the PR or a maintainer-approved demo
location, then add the hosted link to the recipe README. A public demo URL has
not yet been added; do not put machine-specific preview paths in this recipe.

## Microphone and language verification

On 2 November 2026, the user reported that the physical microphone works and
provided the recording above showing the English record/transcribe/edit/insert
flow. This verifies that demonstrated local setup; other browser/device
combinations still need separate checks.

The earlier live checks recorded completed responses for all 26 translation
targets and successful transcription of the official Cookbook Hindi sample.
The new recording adds an English microphone demonstration. It does not establish
live speech coverage for every selectable language or native-speaker quality.
See [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md) for the verification boundaries.

## Reproducible walkthrough

1. Follow README setup with Node 22.20+, FFmpeg and server-side Bodhan keys.
2. Start the app, choose English beside the microphone, record a short synthetic
   sentence and press Stop.
3. Review the returned transcript, edit if needed and Insert. Check the text at
   the saved cursor. If the provider rejects the request, retain the error as
   the actual outcome.
4. Open the spoken-language selector to inspect the supported choices.
5. Select the inserted sentence, activate the translation launcher, and choose
   Hindi, Tamil or Urdu. Show each completed result before changing targets.
6. Replace an editable selection with the Urdu result and check surrounding text.
7. Select part of the read-only paragraph and show its Hindi translation. That
   paragraph supports translation viewing, not replacement.

Use synthetic demo text and keep keys, environment files and account pages
outside the capture. The model infers source language. Document additional
speech-language tests separately from selector coverage and automated tests.
