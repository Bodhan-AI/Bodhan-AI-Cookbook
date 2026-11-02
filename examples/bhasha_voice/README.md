# BhashaVoice

> Write with your voice and translate selected text across Indian languages using Bodhan AI.

- **Bodhan models:** `indic-transcribe`, `indic-translate`
- **Languages:** All enumerated Bodhan speech/translation codes (see LANGUAGE_SUPPORT.md)
- **Format:** Next.js app + local React SDK

## What it does

Speak a thought, review or edit the transcript, and insert it into a text field
at the saved cursor. Highlight words in the field or a paragraph to translate
them. The translation card shows the original and translated text; editable
fields also offer Replace selection. Translation Copy buttons are disabled.

This is a self-contained example for developers building multilingual editors,
forms, and writing tools. The backend and SDK are included in this folder, so
it works when copied out of its original repository. You do not need a
published npm package, a database, or a separately running API server.

## Setup

### Prerequisites

- Node.js **22.20+** and npm. `.nvmrc` pins the version used for verification.
- **FFmpeg** installed on the machine running the Next.js server.
- Server-side keys with access to Bodhan's transcription and translation models,
  available from [console.bodhan.ai](https://console.bodhan.ai/).

Verify the local tools:

```bash
node --version
npm --version
ffmpeg -version
```

From the cookbook repository root, enter this recipe:

```bash
cd examples/bhasha_voice
npm ci
cp .env.example .env.local
```

If you copied just this folder elsewhere, enter that folder and run the same
`npm ci` and copy commands. `npm ci` uses this recipe's own lockfile and local
SDK workspace; it does not install anything from a parent repository.

Edit `.env.local` and fill in the keys for the models you want to try:

| Variable | Purpose |
| --- | --- |
| `BODHAN_TRANSCRIBE_API_KEY` | Preferred key for `indic-transcribe` |
| `BODHAN_TRANSLATE_API_KEY` | Preferred key for `indic-translate` |
| `BODHAN_API_KEY` | Optional common fallback if your key has the required model access |
| `FFMPEG_PATH` | Optional absolute FFmpeg binary path; blank uses `ffmpeg` on PATH |

Transcription prefers its dedicated key, then `BODHAN_API_KEY`. Translation
prefers its dedicated key, then the transcription key, then `BODHAN_API_KEY`.
If you use the common fallback, leave the dedicated keys blank. Bodhan may
issue model-specific keys, so separate keys are the recommended setup.

All variables are server-only. Never use a `NEXT_PUBLIC_` prefix or put provider
keys in React props. The example environment file has blank values; missing
keys return a safe configuration error. Restart the server after changing them.

## Run

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). If the port is occupied:

```bash
npm run dev -- --port 3002
```

The `predev` script builds the included SDK before starting Next.js. Webpack is
selected in the scripts to avoid the Turbopack worker restriction encountered
during development. The app uses system fonts and needs no font download.

### Walkthrough

1. Choose a supported spoken language using the control beside the microphone. On desktop
   the language control appears on hover or keyboard focus; touch devices keep
   it visible.
2. Press the microphone, allow access, and speak a short sentence. Press Stop.
   Stop or the 25-second limit uploads the recording; Cancel discards it.
3. Review the returned transcript. Try Edit, then Insert. Check that existing
   text before and after the cursor is preserved. The voice card also offers Copy.
4. Select `Good morning.` in the editor and click the translation launcher.
   Choose Hindi. A representative result from the prototype's live check is
   `सुप्रभात।`; wording can vary. Use Replace selection to insert the result.
5. Select part of the sample paragraph. The same translation card opens,
   without replacement for read-only text and without translation Copy actions.

These actions send requests to Bodhan and can use your account credit. The
commands in the Checks section below stub provider responses and do not.

### Recorded demo

The 2 November 2026 demo uses actual moving screen-recorded footage. It shows
English microphone dictation, transcript review/edit/insertion, the language
selector, translation and Urdu replacement, and Hindi translation of selected
read-only text. The user reported that the physical microphone works in the
demonstrated setup. Other speech languages and browser/device combinations need
separate live checks.

The silent 44.8-second MP4, `bhashavoice-demo-full-resolution.mp4`, is exported at
2940 × 1912 and 60 fps and delivered separately from this recipe. A hosted demo
link is pending. See [DEMO.md](DEMO.md) for the walkthrough and verification limits.

### Production build

```bash
npm run build
npm run start
```

This needs a Node.js server and FFmpeg; it cannot be deployed as a static-only
site. Add host authentication and shared usage limits before exposing it publicly.

## How it works

```text
Microphone → browser recording → POST /api/bhasha/transcribe
           → FFmpeg (mono, 16 kHz WAV) → Bodhan indic-transcribe
           → review/edit → insert at saved cursor

Selected text → translation launcher → POST /api/bhasha/translate
              → Bodhan indic-translate → show result / replace selection
```

The client SDK calls same-origin routes. Those routes validate input, call
Bodhan with server credentials, and return safe JSON. Provider URLs are
`https://api.bodhan.ai/v1/audio/transcriptions` and
`https://api.bodhan.ai/v1/chat/completions`. The latter is the translation
transport verified in the prototype; `/translate` returned 404 in that check.
See the [Bodhan API reference](https://console.bodhan.ai/api-docs/).

Requests have size, duration, concurrency, and timeout limits. The handlers
remove temporary audio files after conversion, disable response caching, and
avoid logging recordings, transcript text, keys, or raw provider errors. Rate
limits preserve retry backoff; paid requests are not automatically repeated.

### Reuse the SDK

```jsx
import { CompactVoiceInput, SelectionTranslator } from "@bhashavoice/react";
import "@bhashavoice/react/styles.css";

<CompactVoiceInput defaultLanguage="en" onInsert={insertAtSavedCursor} />
<SelectionTranslator showCopyButtons={false} />
```

Define the insertion callback in a Client Component. `src/app/VoiceWorkspace.jsx`
shows how to remember a controlled textarea's selection and insert into it.
Mount one `SelectionTranslator` per document. Set `showCopyButtons={true}` to
show Copy original and Copy translation. Omitting this prop also enables them.
Only the translation Copy actions are controlled by this prop.

For continuous SDK rebuilding after source changes, run `npm run dev:sdk` in a
second terminal. The SDK source, types, CSS, and logo are local in `packages/`.

## Checks

From this recipe folder:

```bash
npm test
npm run lint
npm run build
```

The tests cover recorder/transcription lifecycles, selection replacement,
translation errors and cancellation, and real FFmpeg decoding of generated
WAV, WebM, and M4A audio. Browser media and Bodhan responses are stubbed. The
tests do not load `.env.local` or spend provider credit; they do not replace
a physical-microphone check or the live walkthrough above.

From a Bodhan cookbook checkout, also run its recipe validator:

```bash
python scripts/validate_recipe.py examples/bhasha_voice
```

Run that command from the cookbook root, where its `scripts/` directory exists.
For the full cookbook checks, follow its CONTRIBUTING.md and run `make check`.
Those validation tools are not required to start this standalone app.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Transcription/translation not configured | Fill the relevant server key in this folder's `.env.local`, then restart |
| Credentials rejected | The chosen key must have access to the requested model; check dedicated keys before the fallback |
| Audio conversion unavailable | Run `ffmpeg -version`, or set an absolute `FFMPEG_PATH` |
| Microphone unavailable | Use localhost or HTTPS and check browser/device permission |
| Port already in use | Pass `--port 3002` to the dev command |
| SDK import missing | Run `npm ci`, then `npm run build:sdk` in this folder |
| Rate-limited or out of credit | Respect the displayed backoff or check the Bodhan console |

## Limitations

- All enumerated Bodhan transcription and translation codes are selectable; see LANGUAGE_SUPPORT.md for separate capabilities and verification limits.
- Recordings: at most 25 seconds and 5 MiB. Translation: at most 2,000 UTF-16
  code units. Long audio is rejected rather than silently trimmed.
- Text and the five most recent voice inserts stay in memory and clear on refresh.
- Audio and selected text are sent to Bodhan when you activate their actions.
  Cancellation cannot guarantee an already accepted provider call is unbilled.
- The demonstrated local English microphone flow was manually checked on
  2 November 2026. Other browser/device combinations need verification; automated
  recording tests mock browser media APIs.
- Rich editors require an adapter. Shadow-root and iframe selections are unsupported.
- Two concurrent requests per endpoint per server process is not a hosting-wide
  quota. Public hosting needs authentication, shared limits, and FFmpeg packaging.

## Project layout

```text
bhasha_voice/
  .env.example              # Blank server settings; copy to .env.local
  package.json
  package-lock.json         # Independent, pinned install
  src/app/                  # Editor page and same-origin API routes
  src/server/               # FFmpeg, Bodhan transports, validation and limits
  packages/bhashavoice/      # Local React SDK with its own build and types
  work/                     # Automated checks and shared-source manifest
  sample_data/              # Empty; tests generate synthetic audio
  outputs/                  # Generated exports, ignored by Git
```

When copied into the cookbook, all required runtime files are inside this folder.
In the original development repository, `npm run prepare:cookbook` refreshes
shared files from its main SDK and backend. The manifest lists the copied files;
no development-repository command is needed to install or run this recipe.

## License and submission status

This is a local submission candidate, not a published npm package or submitted
cookbook PR. The app and included SDK remain private and `UNLICENSED` pending
a distribution license decision. Confirm the license with the project maintainers
before contributing to the Apache-2.0 Bodhan AI Cookbook. Complete the live
walkthrough and requested peer review before opening the PR.

## Specification, architecture and demo

- [Software specification](SOFTWARE_SPECIFICATION.md)
- [Architecture](ARCHITECTURE.md)
- [Language support and verification](LANGUAGE_SUPPORT.md)
- [Multilingual demo walkthrough](DEMO.md)
