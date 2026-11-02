# BhashaVoice React

multilingual voice input and selection translation for React. Review, edit, copy,
and insert a transcript; select text to translate it. React and React DOM 19.3
are the currently tested peer versions. Other versions are not advertised yet.

This is a local prerelease package named `@bhashavoice/react`. It is not published.
The npm scope and license must be selected before publication; `private: true`
currently prevents accidental publishing. No open-source license is granted yet.
The supplied Bodhan logo is included for the existing provider attribution.

## Install the local package

From the repository root:

```bash
npm install
npm run pack:sdk
```

In your React app, install the resulting file (use its full path):

```bash
npm install /absolute/path/to/artifacts/bhashavoice-react-0.1.0.tgz
```

## Use

```jsx
import { CompactVoiceInput, SelectionTranslator } from "@bhashavoice/react";
import "@bhashavoice/react/styles.css";

export default function VoiceTools({ insertAtSavedCursor }) {
  return <>
    <CompactVoiceInput
      defaultLanguage="en"
      apiBaseUrl="/api/bhasha"
      onInsert={insertAtSavedCursor}
    />
    <SelectionTranslator apiBaseUrl="/api/bhasha" />
  </>;
}
```

The host owns its editor and implements `insertAtSavedCursor(text)`. Preserve the
selection before users click the microphone. See the repository's React example
for a complete controlled textarea with cursor insertion. `onInsert` may return
a promise; rejecting it keeps the transcript available for retry. Omitting it
disables Insert while leaving recording, review, editing, and copying available.

Import the stylesheet once. Mount `SelectionTranslator` once per document. It
also translates selected paragraph text. Replacement is supported for editable
fields and basic contenteditable elements; framework-managed rich editors need
an adapter. Password fields and elements with `data-bhasha-ignore` are excluded.
Shadow roots and iframes are not supported.

To show translation without Copy actions, pass `showCopyButtons={false}`:

```jsx
<SelectionTranslator showCopyButtons={false} />
```

This hides both Copy original and Copy translation, including their feedback.
Editable selections can still be replaced. The default is `true` for existing
integrations; both repository demos explicitly disable Copy actions.

For Next.js App Router, use these components inside a Client Component and
define insertion callbacks there. The built entry preserves `"use client"`.
Import `@bhashavoice/react/styles.css` in your root layout. The package contains
compiled JavaScript; no `transpilePackages` configuration is required.

## Props

| Component | Prop | Default | Purpose |
| --- | --- | --- | --- |
| `CompactVoiceInput` | `apiBaseUrl` | `/api/bhasha` | Host backend URL prefix |
| `CompactVoiceInput` | `defaultLanguage` | `en` | Initial spoken language: a supported spoken-language code |
| `CompactVoiceInput` | `onInsert` | none | `(text: string) => void \| Promise<void>` |
| `SelectionTranslator` | `apiBaseUrl` | `/api/bhasha` | Host backend URL prefix |
| `SelectionTranslator` | `defaultTargetLanguage` | `en` | Initial translation target; no source-language guessing |
| `SelectionTranslator` | `showCopyButtons` | `true` | Show Copy original and Copy translation actions |

TypeScript definitions include `Language`, `CompactVoiceInputProps`, and
`SelectionTranslatorProps`. The package provides ES module imports.

## Backend required

Installing this package supplies the browser interface. It does not supply a
hosted API or any Bodhan credentials. Your backend must implement:

| Endpoint | Request | Success response |
| --- | --- | --- |
| `POST {apiBaseUrl}/transcribe` | Multipart `file` and `language` (a supported speech code) | `{ "status": "transcribed", "text": "...", "language": "en" }` |
| `POST {apiBaseUrl}/translate` | JSON `{ "text": "...", "target_language": "hi" }` | `{ "status": "translated", "translation": "...", "target_language": "hi", "truncated": false }` |

Return errors as `{ "error": { "code": "...", "message": "..." } }` with an
appropriate HTTP status. For backoff include numeric `retryAfterSeconds` in
`error` and/or a `Retry-After` header. Use `Cache-Control: no-store`.

The existing repository is a backend example: `src/app/api/bhasha/` routes call
the handlers in `src/server/`. To reuse it, keep both routes and all of
`src/server/`, including its audio conversion and request limits. Its `@/`
imports refer to `src/`. It requires Node.js 22, FFmpeg on PATH (or
`FFMPEG_PATH`), and server-only `BODHAN_TRANSCRIBE_API_KEY` and
`BODHAN_TRANSLATE_API_KEY`. Both transports also accept `BODHAN_API_KEY` as a
common fallback. Transcription prefers its dedicated key; translation prefers
its dedicated key, then the transcription key, then the common fallback. The
configured key must have access to the relevant Bodhan model.

Prefer a same-origin backend or reverse proxy. Browser requests use
`credentials: "same-origin"`; setting a cross-origin URL does not configure
CORS or cross-origin cookie authentication. The React example uses a development
proxy to the existing backend.

Add host authentication and shared usage limits before public deployment. The
example backend's concurrency limit applies only per process.

## Data and limits

Microphone access needs HTTPS or localhost. Stop, or reaching the 25-second
limit, sends audio to your backend and then Bodhan. Cancel before Stop discards
the recording. Clicking the translation launcher sends selected text. Tell your
users about that processing before use. Text and the last five inserted
transcripts are held in memory and clear on refresh. The backend example accepts
audio up to 5 MiB and translation text up to 2,000 UTF-16 code units. No provider
credentials belong in browser props or client environment variables.

## Language capabilities

The package exports `TRANSCRIPTION_LANGUAGES`, `TRANSLATION_LANGUAGES`, and their
validation helpers. Use `TranscriptionLanguage` for voice input and
`TranslationLanguage` for translation targets; `Language` remains a compatible
alias for `TranscriptionLanguage`. Bhili/Bhojpuri are speech-only; translation
includes `ks-Deva`, `mni-Beng`, and `sd-Arab` script variants.

`SelectionTranslator` defaults to English. Set `defaultTargetLanguage="hi"`
(or another translation code) to choose the host’s initial target. Unsupported
runtime defaults fall back to English. Selection never guesses source language.
