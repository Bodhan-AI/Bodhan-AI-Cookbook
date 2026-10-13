# Saathi (AndroidSpeak) — Software Specification

> **Sample document.** This is a worked example of the Bodhan AI Cookbook software
> specification template, written after the fact for
> [`examples/AndroidSpeak`](../../../examples/AndroidSpeak/) as merged in PR #6 (commit
> `ae51852`). It describes the app as built. Where the code falls short of what a spec would
> normally require, the gap is recorded in Sections 10, 14 and 16 instead of being hidden. Use
> it to see the level of detail reviewers expect; the companion design is in
> [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Document details

| Field | Value |
| --- | --- |
| App name | Saathi (folder `examples/AndroidSpeak`) |
| One-line description | A two-way voice translator for Android: speak in your language, the phone says it in the local one, and back again |
| Authors | App: Rudra Murthy (@rudra431). This spec: Bodhan AI Cookbook maintainers |
| Bodhan point of contact | @Bodhan-AI/cookbook-maintainers |
| Recipe proposal issue | None. The app arrived directly as PR #6; a new project should open a proposal first |
| Status | Sample (retrospective) |
| Version | 1.0 |
| Last updated | 13 Oct 2026 |
| Reviewers | Filled in at the review meeting |
| Architecture document | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Designs | None; screens are described in Section 11 |
| Code | [`examples/AndroidSpeak`](../../../examples/AndroidSpeak/), PR #6 |

### Change history

| Version | Date | Author | What changed |
| --- | --- | --- | --- |
| 1.0 | 13 Oct 2026 | Cookbook maintainers | Written from the merged code as a sample |

## 2. Summary

- **What it is:** An Android app that lets two people who share no language hold a spoken conversation through one phone.
- **Who it is for:** A visitor in Chennai who speaks English or Hindi, talking to Tamil-speaking shopkeepers, auto drivers and passers-by.
- **Bodhan models used:** `indic-transcribe`, `indic-translate`, `indic-speak`.
- **Platform:** Native Android app (Kotlin, Jetpack Compose), Android 8.0 and later.
- **What V0 proves:** A visitor and a Tamil speaker can complete a short exchange, such as asking a price or directions, using only the app.

## 3. Problem and users

**Problem statement.** People who move to or visit a city where they do not speak the local
language struggle with everyday exchanges: buying something, taking an auto, asking the way.
Both sides end up pointing and guessing, or waiting for a bystander to translate. The visitor
needs to be understood *and* to understand the reply, in real time, on the phone they already
carry.

**Evidence.** Not recorded with the contribution. A real proposal should cite a handful of
conversations with visitors and with the locals they talk to.

### Users

| Persona | Who they are | Languages and script | Device and connectivity | What they need from the app |
| --- | --- | --- | --- | --- |
| Visitor (primary) | Adult new to Chennai: student, worker, tourist | Speaks English or Hindi; may type in Roman script | Own Android phone, mobile data | To say something and have it spoken aloud in Tamil, then hear the reply in their language |
| Local (secondary) | Shopkeeper, auto driver, passer-by | Speaks Tamil; never touches the settings | Uses the visitor's phone for a few seconds | To hear the visitor in Tamil and answer by speaking |

### Context to address

- **Language and script:** Speech in both directions. Typed input is a fallback for the visitor. Any pair of the 13 languages in the app can be chosen, not just English and Tamil.
- **Literacy and reading level:** The local user never needs to read: every translation is spoken aloud.
- **Devices:** Android 8.0 (API 26) and later, with a microphone.
- **Connectivity:** Every step is an API call. With no connection the app cannot translate.
- **Age:** The app targets adults. A bystander being recorded could be a minor; see Section 10.

### Existing alternatives

| Alternative | What it does well | Where it falls short for these users |
| --- | --- | --- |
| General-purpose phone translators with a conversation mode | Mature apps, many languages | To be tested: quality on Indian accents and code-mixed speech |
| A bystander who speaks both languages | Understands context and tone | Not always there; slow; awkward to ask |
| Pointing and gestures | Works anywhere | Fails for anything beyond prices and directions |

## 4. Goals, non-goals and V0 scope

### Goals

1. G1: A visitor and a Tamil speaker complete a short spoken exchange using only the app.
2. G2: The same flow works for any pair of the 13 supported languages.
3. G3: Other developers can read the code to learn how to call `indic-transcribe`, `indic-translate` and `indic-speak` from native Android.

### In V0

- Two microphone buttons, one per language: tap to start, tap again to stop (G1)
- Transcribe, then translate, then speak, for each turn (G1)
- Translations play automatically, with a replay button on every turn (G1)
- A running conversation log showing original and translated text (G1)
- A "Type instead" fallback for noisy places (G1)
- Language pickers for both sides, with a swap button (G2)
- A Settings screen holding one Bodhan key per model (G3)

### Deferred to V1 or later

- Saving conversation history between sessions
- A 30-second recording cap, with long speech split into several requests
- A voice chosen to match each target language
- A backend proxy, so end users do not need their own Bodhan keys
- A designed app icon

### Non-goals

- Not a replacement for a professional interpreter in medical, legal or police situations.
- Not a chat assistant: the app translates what people say and generates no replies of its own.
- No offline mode.

## 5. Platform and form factor

| Question | Answer |
| --- | --- |
| Primary platform | Native Android app |
| Why this platform for these users | The visitor already carries an Android phone, and the phone is easy to hold up or hand to the other person |
| Supported targets | Android 8.0 (API 26) and later; targets Android 15 (API 35) |
| Backend needed? | No, for V0. Each user enters their own Bodhan keys (Section 10). This is a deviation from the cookbook rule for mobile recipes and needs maintainer approval |
| How reviewers will run V0 | Open the folder in Android Studio and run it on a device or emulator with a microphone. No prebuilt APK |
| Offline behaviour | None. The app shows an error if a call fails |
| Where the code lives | `examples/AndroidSpeak/` in the Bodhan AI Cookbook |

## 6. User journeys and functional requirements

### Core journeys

J1 — The visitor asks a shopkeeper a question:

1. The visitor sets My language to English and Local language to Tamil (the defaults).
2. The visitor taps the English mic, speaks, and taps again to stop.
3. The app transcribes the English, translates it to Tamil, speaks the Tamil aloud and adds both lines to the log.
4. The shopkeeper taps the Tamil mic and answers. The app runs the same steps in reverse and plays the English.

J2 — In a noisy place, the visitor types a sentence instead; the app translates and speaks it.

J3 — On first launch the app opens Settings; the visitor pastes three Bodhan keys and saves them.

### Functional requirements

The last column records whether the merged code meets each requirement, which a forward-looking spec would leave out.

| ID | The app shall… | Journey | Priority | Acceptance criterion | Built in V0 |
| --- | --- | --- | --- | --- | --- |
| FR-1 | Record speech from the microphone: tap to start, tap again to stop | J1 | Must | Given mic permission, a tap starts recording and the button shows a stop state; a second tap ends it | Yes |
| FR-2 | Transcribe each recording in the speaker's selected language | J1 | Must | Given a clear 5-second English clip, the English transcript appears in the log | Yes |
| FR-3 | Translate the transcript into the other side's language | J1, J2 | Must | Given an English sentence and Tamil as the target, Tamil text appears under it | Yes |
| FR-4 | Speak the translation, playing it automatically when that setting is on (default on) | J1, J2 | Must | The Tamil audio plays within the wait target in Section 8 | Yes (wait not yet measured) |
| FR-5 | Show each turn as a bubble with original text, translation and a replay button | J1 | Must | Replay plays the same audio again | Yes |
| FR-6 | Translate and speak typed text | J2 | Should | Typing "How much is this?" with English as the speaking side plays Tamil audio | Yes |
| FR-7 | Let the user pick and swap both languages, and remember the choice | J1 | Should | After a restart the same pair is selected | Yes |
| FR-8 | Store one Bodhan key per model securely, and open Settings on first launch | J3 | Must | Keys never appear in plain text on disk or in logs | Yes |
| FR-9 | Show a plain-language message when any step fails | J1 | Must | A wrong key shows "Check that your API key is correct."; a rate limit says to wait and try again | Partly: rate limits show "Server said: 429." |
| FR-10 | Tell the user when microphone permission is denied, and how to grant it | J1 | Must | Denying permission shows an explanation with a link to settings | No: denial is silent |
| FR-11 | Keep each recording within the 30-second limit of `indic-transcribe` | J1 | Must | A 45-second answer is either capped with a warning or split into two requests | No |

## 7. Bodhan API usage

### Usage

| Model | Used for | What the app sends | What it uses from the response | Languages | Calls per voice turn | Acceptable wait |
| --- | --- | --- | --- | --- | --- | --- |
| `indic-transcribe` | FR-2 | `POST /v1/audio/transcriptions`, multipart: a 16 kHz mono 16-bit WAV, `model`, `language`, `vad=true` | `text` | 13: en, hi, ta, te, kn, ml, mr, gu, bn, pa, or, as, ur | 1 | Target 3 s (not yet measured) |
| `indic-translate` | FR-3, FR-6 | `POST /v1/chat/completions` with one user message and `target_language_code` (plus `source_language_code`, which the API ignores because it infers the source) | `choices[0].message.content` | Same 13 | 1 | Target 2 s |
| `indic-speak` | FR-4 | `POST /v1/audio/speech` with `input`, `voice` and `instructions` set to `{"lang": "<target>"}` | The WAV body, saved to a file | Same 13 | 1 | Target 2.5 s |

A typed turn skips transcription: one translate call and one speak call.

### Design questions answered

- **Over-limit input:** Not handled in V0. Recording has no time limit, and `indic-transcribe` rejects audio over 30 seconds. Tracked as FR-11 and risk R1.
- **Audio format:** The app records raw PCM and writes its own WAV header, so it never produces the AAC or AMR files that `indic-transcribe` refuses.
- **Language selection:** The user picks both languages. The speaker's language is always passed to `indic-transcribe`.
- **Failure behaviour:** Each step's error appears as a snackbar naming the step. Only a 401 gets friendly wording today (FR-9).

**Companion services:** None. No LLM or other third-party API is called.

### Gaps and feedback for Bodhan

- Bodhan issues one key per model, so a bring-your-own-key app asks users to paste three keys before the first turn.
- The app's author could not find a voice list, so Voice is a free-text field defaulting to "Amit" for every language. A default voice per language in the API, or a voice-list endpoint, would remove the guesswork.

## 8. Non-functional requirements

| Area | Requirement | How it will be checked |
| --- | --- | --- |
| Latency | A full voice turn, from tapping Stop to hearing audio, in 8 s or less on 4G | Timed over 10 turns on a mid-range phone (not yet done) |
| Reliability | A failed step must not lose the user's input or the steps that already succeeded | Not met: if speech fails, the transcript and translation are discarded |
| Low bandwidth | Upload is 32 KB per second of speech, about 250 KB for an 8-second turn | Arithmetic from the recorder settings; measure on a slow connection |
| Device limits | Android 8.0 or later, with a microphone | Manifest declares the microphone as required |
| Accessibility | Every icon button has a spoken label | Partly met: mic, swap and back buttons have no content description |
| Localisation | Interface in English; conversation content in 13 languages | Manual check of each language pair |
| Quality of AI output | A native Tamil speaker accepts at least 16 of 20 English-to-Tamil turns as understandable | Not yet done |

## 9. Cost and usage estimate

Prices are from [Bodhan pricing](https://console.bodhan.ai/#pricing) on 13 Oct 2026. The session assumptions are estimates, not measurements.

### Cost per conversation

Assumes 10 turns, 5 each side, with 8 seconds of speech, a 30-token translation and an 80-character spoken reply per turn.

| Model | Priced per | Units per conversation | Price per unit (₹) | Cost (₹) |
| --- | --- | --- | --- | --- |
| `indic-transcribe` | minute of input audio | 1.33 (10 × 8 s) | 0.10 | 0.13 |
| `indic-translate` | 10,000 output tokens | 0.03 (10 × 30 tokens) | 0.20 | 0.01 |
| `indic-speak` | 10,000 input characters | 0.08 (10 × 80 characters) | 6.00 | 0.48 |
| **Total** | | | | **0.62** |

Transcription is billed by the second, so 80 seconds costs 1.33 minutes' worth. Speech output is about three quarters of the cost. Keeping spoken replies short matters more than trimming the input audio.

### Volume and credits

| Estimate | Number |
| --- | --- |
| Cost per conversation (₹) | 0.62 |
| Conversations per active user per month | 20 (a visitor's first month in a new city) |
| Cost per active user per month (₹) | 12.40 |
| Credits needed to build and test V0 (₹) | 50: about 80 test conversations |
| Credits needed for a 20-person pilot (₹) | 250 |
| Peak requests per minute, per model | About 2 per active user (one call per model per turn, roughly 2 turns a minute); each user has their own keys, so users do not share an allowance |
| Who pays after V0 | Each user, with their own Bodhan keys (Section 10) |

## 10. Security, privacy and data handling

**API key custody: bring your own key.** Each person running the app pastes their own three
Bodhan keys into Settings. The app stores them with Android's `EncryptedSharedPreferences`
(AES-256, backed by the Android Keystore) and excludes that file from cloud backup and device
transfer. No key is in the source, the build configuration or the logs.

This deviates from the cookbook rule that mobile recipes call Bodhan through a backend. It is
acceptable for V0 only because the person who installs the app is the person whose key and
credits are used. It must not be distributed to people using someone else's key. V1 adds a
backend proxy (Section 4).

Keys used: `indic-transcribe`, `indic-translate` and `indic-speak`, one key each.

### Data inventory

| Data item | Collected from | Sent to | Stored where, and for how long | Personal data? |
| --- | --- | --- | --- | --- |
| Voice recording | Microphone, from the visitor or the local person | Bodhan `indic-transcribe` | App cache; deleted when the turn finishes | Yes: a voice can identify a person |
| Transcript and translation | Bodhan responses, or typed text | Bodhan `indic-translate` and `indic-speak` | Memory only; gone when the app closes | Possibly, depending on what is said |
| Spoken translation (WAV) | Bodhan `indic-speak` | Nowhere | App cache, kept for replay and never deleted by the app | Possibly |
| Settings (languages, voice, auto-play) | The user | Android Auto Backup, if enabled | Device storage, indefinitely | No |
| Bodhan keys | The user | Bodhan, as a bearer token | Encrypted storage until the app is uninstalled; no clear button, but blanking a field and tapping Save overwrites a key | Credentials, not personal data |

### Questions answered

- [x] **Personal data collected:** voice recordings of both people, and whatever they say. Nothing is kept after the session except the spoken-translation files.
- [ ] **The local person is recorded.** V0 relies on the visitor asking before handing over the phone. Open question Q2: should the app show a short consent line in the local language first?
- [x] **Users under 18:** the app targets adults. A bystander could be a minor; under India's DPDP Act, 2023 that would need verifiable parental consent, so the demo must use adults only.
- [ ] **Consent and deletion:** there is no consent screen, no way to delete cached audio from inside the app, and no clear button for keys. V1 work.
- [x] **Data leaving the device:** only to Bodhan (`api.bodhan.ai`). No analytics or crash reporting is included.
- [x] **Sample data:** none committed.
- [x] **Backend abuse:** not applicable; there is no backend.

## 11. UX and design

**Mock-ups:** none. The built screens are the reference.

| Screen | Purpose | Key elements | Loading, empty and error states |
| --- | --- | --- | --- |
| Conversation | Hold the conversation | Language bar with swap; conversation log; two large mic buttons, each captioned with its language; "Type instead" toggle | Empty-state hint; status line while each call runs ("Listening…" during transcription, then "Translating…" and "Preparing voice…"); recording mic turns red and pulses; errors as snackbars |
| Settings | Enter keys and preferences | Three masked key fields, Save, My and Local language, Voice, auto-play switch | "API keys saved" confirmation; first launch opens here |

- **Interface language:** English only. The conversation itself runs in any of the 13 languages.
- **Credit to Bodhan:** the README names the Bodhan models. The app is called Saathi. It names Bodhan only where it asks for Bodhan API keys, and does not use the Bodhan logo.

## 12. Success criteria and demo plan

| Criterion | Target | How it is measured |
| --- | --- | --- |
| J1 works end to end with live Bodhan calls | 9 of 10 runs | Scripted English-to-Tamil exchanges on a physical phone |
| Tamil output is understandable | 16 of 20 turns accepted | Rated by a native Tamil speaker |
| Voice turn wait | 8 s or less | Timed from tapping Stop to the start of playback |
| Reverse direction works | Tamil-to-English replies understood | The same rater, 10 turns |

### Demo plan (5 minutes)

1. Setting: a visitor at a shop counter, phone in hand, English and Tamil selected.
2. The visitor asks "How much are these bananas?" and the phone speaks it in Tamil.
3. A Tamil speaker answers into the Tamil mic; the phone speaks the answer in English.
4. Switch to "Type instead" and send a typed sentence.
5. Failure case: replace the speak key with a wrong one and show the error message.

- **Sample data:** spoken live by team members who consent; nothing is recorded or kept.
- **Fallback:** a screen recording of steps 2 and 3.

## 13. Milestones and timeline

Only the contribution dates were recorded for V0. The rows marked V1 are the proposed next steps.

| Milestone | Date | Done when |
| --- | --- | --- |
| Code written | 29 Sep 2026 | First commit of the app |
| Merged to the cookbook | 5 Oct 2026 | PR #6 merged |
| V1: passes the cookbook validator | To be set | `.env.example` (declaring `BODHAN_API_KEY`) added, `.env` added to `.gitignore`, and the validator recognises Kotlin |
| V1: 30-second cap and per-language voices | To be set | FR-11 met; voice follows the target language |
| V1: backend proxy | To be set | No end user needs a Bodhan key |

**Team and time:** one developer.

## 14. Risks, dependencies and open questions

### Risks

| ID | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | A reply longer than 30 seconds is rejected by `indic-transcribe`, and the user sees only an error code | High | Medium | Cap recording at 30 s with a countdown; split longer audio (FR-11) |
| R2 | One voice ("Amit") speaks every target language | High | Medium | Choose the voice from the target language using the published voice list, with an explicit default for English, which has no voice group of its own (any voice can read any language) |
| R3 | Pasting three keys puts off new users | Medium | Medium | A backend proxy in V1 |
| R4 | Recording a bystander without consent | Medium | High | A consent line in the local language before the first recording (Q2) |
| R5 | Another developer cannot build the project as committed: the wrapper jar and `gradlew` scripts are missing, and the wrapper points at a file on the author's machine | High | Medium | Commit a standard wrapper with a public distribution URL |
| R6 | The app fails the cookbook validator, so CI on `main` is red | Certain | Low | Add `.env.example` and a `.gitignore` entry; teach the validator about Kotlin |

### Dependencies

- Three Bodhan keys with credit (one per model)
- An Android phone with a microphone and mobile data
- A native Tamil speaker to rate output quality

### Open questions

- [ ] Q1: Is bring-your-own-key acceptable for mobile cookbook examples, or must every mobile app use a proxy? (Maintainers)
- [ ] Q2: Should the app ask the local person's consent, in their language, before recording? (Maintainers)
- [ ] Q3: Should translation move to the preferred `POST /translate` route? (Maintainers)

## 15. Open source and contribution

| Item | Answer |
| --- | --- |
| Code location | `examples/AndroidSpeak/` in the cookbook |
| Licence | Apache-2.0, the cookbook's licence |
| Third-party licences | AndroidX and Jetpack Compose, Retrofit, OkHttp, Gson, Kotlin coroutines: all Apache-2.0 |
| README | Has How it works and Setup; no Run section (validator warning) |
| Configuration | Keys are entered in the app, so there is no `.env`. The validator still requires `.env.example`, and `.gitignore` must ignore `.env` |
| Dependencies | Pinned in `app/build.gradle.kts` |
| Sample data | None |
| AI assistance | Not recorded for PR #6 |

## 16. Review

*Illustrative: this is how a reviewer might complete the section for this app.*

### Reviewer checklist

- [ ] The problem and primary user are specific, with evidence (no evidence recorded)
- [x] V0 scope proves one clear value
- [x] Every Must requirement has a testable acceptance criterion
- [x] Every Bodhan model and language used is supported today
- [ ] Audio formats, long inputs and API errors are handled (no 30-second cap; no 429 handling)
- [x] No Bodhan key ships in the app (bring your own key)
- [ ] Personal data, children's data, consent and deletion are covered (bystander consent open)
- [x] The cost estimate is worked through
- [x] Sample and demo data are synthetic or consented
- [ ] Licence and code location are agreed (folder fails the validator)

### Comment log

| # | Section | Comment | Status | Resolution |
| --- | --- | --- | --- | --- |
| 1 | 10 | Bring-your-own-key on mobile deviates from CONTRIBUTING | Accepted | Allowed for V0 as a developer demo; V1 adds a proxy |
| 2 | 6, 7 | No 30-second cap, but `indic-transcribe` rejects longer audio | Open | FR-11 added |
| 3 | 7 | The same voice reads every language | Open | R2 |
| 4 | 15 | Folder fails the cookbook validator | Open | R6 |
| 5 | 10 | The local person is recorded without being asked | Open | Q2 |

### Decision

| Field | Value |
| --- | --- |
| Decision | Illustrative: Approved with changes |
| Conditions | Resolve comments 2 to 5 before the app is promoted in the cookbook README |
| Next step | Architecture review of [ARCHITECTURE.md](ARCHITECTURE.md) |
