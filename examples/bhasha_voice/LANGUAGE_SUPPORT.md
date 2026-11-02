# Language support and verification

Verification updated: 2 November 2026. Language registry sources checked on
29 October 2026: [Bodhan API reference](https://console.bodhan.ai/api-docs/)
and [Cookbook language rules](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/blob/main/scripts/bodhan_api_rules.json).
The implementation follows the enumerated codes, rather than headline counts.
The speech headline says 25 Indian languages + English but its current explicit
list and Cookbook rules contain 25 total codes. No undocumented code is invented.

## Capabilities

- Speech (25 codes): `as bhb bho bn brx doi en gu hi kn kok ks mai ml mni mr ne or pa sa sat sd ta te ur`.
- Translation (26 codes): `as bn brx doi en gu hi kn kok ks ks-Deva mai ml mni mni-Beng mr ne or pa sa sat sd sd-Arab ta te ur`.
- Bhili (`bhb`) and Bhojpuri (`bho`) are speech-only.
- Translation variants: Kashmiri Perso-Arabic `ks` / Devanagari `ks-Deva`;
  Manipuri Meitei `mni` / Bengali `mni-Beng`; Sindhi Devanagari `sd` /
  Perso-Arabic `sd-Arab`.
- Transcription and translation selectors/validation/types use separate lists.
- Target selection is explicit. The SDK defaults to English, and hosts may set
  `defaultTargetLanguage`. The Next.js demo explicitly starts with Hindi.
- Native text uses `dir="auto"`; selectors show language and variant names.
- This update does not add speech synthesis, transliteration or output-style controls.

## Verification

Automated: 162 tests pass, including every speech code through multipart upload,
FFmpeg conversion and mocked provider transport; every translation code through
the host handler and mocked provider transport; invalid combinations are rejected.
Package types include capability-specific positive/negative consumer cases.
These checks verify integration behavior, not model accuracy.

Live translation: all 26 codes returned nonempty, completed responses through the
existing chat transport using the synthetic sentence “Good morning. The meeting
starts at nine.” The initial pass hit a rate limit after 16 successes; checks
paused and the remaining targets passed after a manual cooldown.
No automatic paid retries were added to the app.

Live speech: the official Cookbook Hindi sample previously passed through the
real FFmpeg conversion and Bodhan transcription service. On 2 November 2026,
the user reported a working physical microphone and supplied a real screen
recording showing English recording, Stop, transcript review/edit and insertion.
The edited recording is described in [DEMO.md](DEMO.md). It has no audio track,
so it does not independently establish transcription accuracy against the speech.

Earlier attempts to create six local speech fixtures produced empty audio files
(78-byte WAV headers). Those invalid inputs were rejected with HTTP 415 and are
not evidence of model/language failures. Live speech checks beyond the Hindi
sample and demonstrated English flow, broader browser/device microphone coverage
and native-speaker quality review remain outstanding.

## Reproduce

`npm test`, `npm run lint`, `npm run build`, and `npm run check:cookbook`.
The development repository also provides `npm run check:sdk` for an isolated
package install with TypeScript checks. Test scripts stub Bodhan by default.
Live checks explicitly use server keys and account credit; do not treat mocked
provider results as evidence of linguistic quality or current provider availability.
