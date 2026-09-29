# Saathi — a two-way voice translator for Chennai

An Android app built around Bodhan's `indic-transcribe`, `indic-translate`, and `indic-speak`
APIs, for exactly the problem you described: you speak/understand English (or another Indic
language), the people around you speak Tamil, and neither side follows the other.

## How it works

The main screen has two big microphone buttons — one for **your** language, one for the
**local** language (Tamil by default, but any of the languages in the dropdown can be picked
for either side, e.g. if you travel elsewhere in India).

1. Tap the mic on your side and speak. The app records audio and sends it to
   `indic-transcribe` in your language.
2. The transcribed text is sent to `indic-translate`, converted into the other language.
3. The translation is sent to `indic-speak`, and the resulting audio is played out loud
   automatically (so you can hand the phone to a shopkeeper/auto driver, or just hold it up).
4. Both the original and translated text stay on screen as a running conversation log, with a
   replay button on every entry.

Tap the mic on the *other* language's side and the pipeline runs in reverse — so it also works
as a way to understand what's being said *to* you.

There's also a "Type instead" fallback (a text field + send button) for noisy environments or
when you'd rather not speak out loud.

## Setup

1. Open the `AndroidSpeak` folder in Android Studio (Koala/2024.1 or newer). Android Studio will
   generate the missing Gradle wrapper jar on first sync — this environment had no local Gradle
   or JDK to run that step itself, so `gradle/wrapper/gradle-wrapper.jar` is not checked in.
   Alternatively, if you have Gradle installed, run `gradle wrapper` once from this folder.
2. Let Gradle sync (it will fetch Compose, Retrofit, DataStore, etc. from Google/Maven Central).
3. Run the app on a device or emulator with a microphone. **On first launch it takes you
   straight to Settings** — paste your Bodhan API key there (it's stored in
   `EncryptedSharedPreferences`, not plain text), pick "My language" and "Local language," then
   go back to start talking.

## Assumptions worth double-checking against Bodhan's actual docs

I built this from the three code samples you shared, and inferred a few details that aren't
fully pinned down there — worth verifying against Bodhan's docs/support before you rely on the
app:

- **Language codes**: I used the ISO codes implied by your samples (`hi`, `en`, `kn`, `ta`, …).
  The dropdown in [`Language.kt`](app/src/main/java/ai/bodhan/saathi/data/Language.kt) has 13
  common Indic languages + English — add/remove codes there if Bodhan supports a different set.
- **Audio format for transcription**: your sample posts a `.wav` file, so the recorder
  ([`WavRecorder.kt`](app/src/main/java/ai/bodhan/saathi/audio/WavRecorder.kt)) captures 16 kHz
  mono 16-bit PCM and wraps it in a standard WAV header. If Bodhan expects a different sample
  rate or format, change the constant in `WavRecorder`.
- **Voice name**: defaulted to `"Amit"` per your sample, editable in Settings. I don't know the
  full list of available voices, so it's a free-text field rather than a dropdown.
- **`instructions` field**: sent as `{"lang": "<target-code>"}`, matching your `indic-speak`
  example.

## What I didn't build (and why)

- No offline mode — every step is an API call, so it needs connectivity.
- No conversation history persistence — entries live in memory for the session. Say the word if
  you want past conversations saved to disk.
- No custom app icon design beyond a simple placeholder soundwave mark — happy to swap in a real
  logo if you have one.


