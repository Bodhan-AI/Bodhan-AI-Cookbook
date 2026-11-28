# Translate extension — Architecture & Technology Decisions

Companion to [SPECIFICATION.md](SPECIFICATION.md): which parts exist, how they connect, and why each
technology was chosen over its alternatives.

## 1. Overview

```
┌──────────── Web page ──────────────┐      ┌──── Extension ──────────────────────┐
│ content.js (Shadow DOM bubble/card)│◄───►│ background.js (service worker)      │
└────────────────────────────────────┘ msg  │   ├─ lib/text.js      splitting      │
┌── popup ───────┐  ┌─ settings page ─┐◄───►│   ├─ lib/translate.js piece by piece │
└────────────────┘  └─────────────────┘     │   ├─ lib/bodhan.js    API client     │──HTTPS + key──► api.bodhan.ai
                                            │   └─ lib/settings.js  storage + keys │
                                            └──────────────────────────────────────┘
```

The idea behind the layout: **the part that runs inside web pages holds nothing valuable, and the
part that holds the keys can't be reached by pages.** The parts talk by messages.

| Part | Runs in | Responsibility | Network access |
|---|---|---|---|
| Content script (`content.js`) | every webpage | watch selections, draw the bubble and card, play audio | none; messages only |
| Popup and Settings page | extension pages | text box; keys, API URL, defaults, mock mode | none; messages only |
| Service worker (`background.js`) | extension background | check input, split text, cache, choose voice, context menu, call Bodhan | only the API URL in Settings |

## 2. How a translation works

1. **Select.** `content.js` notices the selection and shows **अ** at its end.
2. **Ask.** A click sends the text and target language to the service worker.
3. **Check.** `background.js` refuses empty text, text over 5,000 characters and unknown languages, checks it may reach the API URL, and answers from its cache when it can.
4. **Split.** `text.js` splits the text into paragraphs, then each paragraph at sentence breaks (including `।`) into pieces of at most 1,000 characters (500–5,000 in Settings).
5. **Translate.** `bodhan.js` sends the pieces one at a time to `indic-translate` (see §5).
6. **Show.** `translate.js` joins the pieces with the original paragraph breaks; the card shows the result, with a warning if any piece was cut off.

**Listen** works the same way with `indic-speak`: the translation is cut into short segments (the
first up to 100 characters so sound starts sooner, the rest up to 200), and the next segment is
fetched while the current one plays. Audio is played with Web Audio, which a page's Content
Security Policy cannot block.

**Language choice.** The card and the popup use the default language from Settings. Picking another
language there translates into it for that translation only; only Settings changes the default.

**Errors** come back as `{ok: false, error, code}`. The card shows the message, highlights
**Settings** for key problems, and ends the message with Bodhan's request id when Bodhan sends one.

| Code | Meaning |
|---|---|
| `not_configured` | no key saved, or no permission to reach the API URL |
| `auth` | Bodhan rejected the key |
| `model_denied` | the key isn't allowed to use that model; add a key for it in Settings |
| `rate_limited` | Bodhan returned 429; the message gives the wait from `Retry-After` when present |
| `network` | the API couldn't be reached |
| `too_long`, `bad_language`, `bad_request`, `empty` | input refused by the extension or by Bodhan |

## 3. Frontend: the extension

| Decision | Why | Alternatives considered |
|---|---|---|
| **Browser extension** | People read on other sites; an extension brings translation to where they already are. | A website (copy and paste into another tab: the problem being solved); a bookmarklet (no background context, nowhere safe for keys). |
| **Manifest V3, Chromium first** | The only manifest version the Chrome Web Store accepts. Chromium browsers (Chrome, Edge, Brave, Opera) cover most desktop users in India with one build. | Manifest V2 (retired); Firefox first (smaller share in India). |
| **Plain JavaScript, no framework, no build step** | Three small screens. Anyone can load the folder directly, with no toolchain, and every file runs as written. | An extension framework such as WXT with TypeScript and a small UI library: worth adopting with the UI redesign (roadmap item 1), when the UI grows and Firefox builds are wanted. Next.js doesn't fit: it is built around a server, and an extension is static files in the browser. |
| **Closed Shadow DOM for the in-page UI** | Page styles can't break the card, the card can't break the page, and page scripts can't reach into it. | An iframe (heavier, awkward to position next to a selection). |
| **Results shown with `textContent`** | API output is untrusted and must never become markup. | `innerHTML` with sanitising (more code, more risk). |
| **Only the service worker makes requests** | Keys never enter page context; host permission exempts the worker from CORS; one place for caching and splitting. | Requests from the content script (key exposed to the page, blocked by CORS). |
| **`chrome.storage.local` for settings and keys** | Stays on this computer. | `storage.sync` (would copy the keys to every device signed in to Chrome). |
| **All API code in `bodhan.js`** | One place to change when the API changes or a model is added; easy to test with a fake `fetch`. | Calls spread across handlers. |
| **Temporary language choice in the card and popup** | Predictable: a one-off check in another language never changes future translations. | Remembering the last language used (convenient, but changes behaviour without the user noticing). |

### 3.1 Browser support

Version 0 is built for **Chromium browsers** and used on Chrome and Edge. **Firefox and Safari are
not tested.** The code uses only standard WebExtension APIs (`runtime`, `storage.local`,
`contextMenus`, `tabs`, `scripting`, `permissions`) and plain web features, so a port should need
little code change. Expected work:

| Browser | What a port needs | Effort |
|---|---|---|
| **Firefox** | Add `"scripts": ["src/background.js"]` next to `service_worker` in `background` (Firefox doesn't run extension service workers; it uses whichever key it understands). Add `browser_specific_settings.gecko` with an add-on id. Test every feature via `about:debugging`. Publish on addons.mozilla.org. | Small |
| **Safari** | On a Mac, wrap `extension/` in an app with `xcrun safari-web-extension-converter` and build it in Xcode. Safari asks per site before the extension runs. Publishing needs the App Store and an Apple Developer account. | Medium, mostly setup |

## 4. Backend

**None in Version 0.** The service worker calls Bodhan directly with the user's own keys. Whether
this stays is an open decision (§11).

| Decision | Why | Alternatives considered |
|---|---|---|
| **No server** | Nothing to host, run or pay for; users install one thing. No shared key exists to protect, because each user brings their own. | A local proxy that keeps keys out of the browser (§11); a hosted backend (needs hosting, sign-in and per-user limits: roadmap item 8). |
| **Separate speech key, falling back to the translation key** | Bodhan keys work with one model each, so Listen needs its own `indic-speak` key. If Bodhan issues keys covering several models, an empty speech key will reuse the translation key with no code change. | A single key field (doesn't work with per-model keys). |

## 5. Bodhan API

| Model | Call | Notes |
|---|---|---|
| `indic-translate` | `POST /translate` `{text, target_language, script}` | `script` is not sent for English. This route currently returns 404, so the extension falls back to chat completions (next row) and remembers that until the service worker restarts. |
| `indic-translate` | `POST /v1/chat/completions` `{model, messages, target_language_code, target_script}` | Documented as giving the same output. `finish_reason: "length"` means the output was cut off. |
| `indic-speak` | `POST /v1/audio/speech` `{model, input, voice, instructions: "{\"lang\":\"hi\"}"}` | Returns WAV, 24 kHz mono (labelled `audio/mpeg`). Only voices from `VOICES` in `languages.js` are sent, since an unknown voice fails the request. |

Errors use one envelope, `{"error": {"message", "code", "request_id"}}`.

**Request size.** Bodhan doesn't publish a character limit (it returns 413 above an undocumented
size, and marks output cut off at its token budget as truncated). Tested against the live API, a
single 5,000-character paragraph translated completely into Hindi, Tamil and Malayalam, with no 413
and no truncation (Tamil output was the longest, at about 6,800 characters). That is why Settings
allows pieces of up to 5,000 characters. One caveat: text that repeats the same sentences many times
can come back shorter without being marked as truncated.

### 5.1 Rate limits

Each key's limits are sent in response headers. For a translation key they are **120 requests**
and **10 at a time**; the time window isn't documented. Going over either returns **429**
(`rate_limit_exceeded_error`), and **retries count against the limit**.

| Limit | How the extension stays inside it |
|---|---|
| 10 at a time | Translation pieces go one at a time; Listen fetches one segment ahead, so at most about two requests are in flight. |
| 120 requests | Pieces are 500–5,000 characters (1,000 by default), the cache answers repeats, and nothing is sent until the user clicks. Text with many short paragraphs still makes one request per paragraph. |
| 429 | No automatic retry. The card says "Rate limit reached", with the wait from `Retry-After` when present. |

## 6. Database

**None.** Only a few settings need to persist, and `chrome.storage.local` holds them. A database
would matter only for translation history or user accounts, and would belong to a hosted backend.

## 7. Caching

An in-memory cache in the service worker keeps the last **100 translations** and the last **20
audio segments**, so switching languages back and forth or replaying audio costs nothing. It is
cleared when Chrome stops the service worker, which is fine: it avoids repeat calls during one
reading session and isn't meant as history.

## 8. Message queue

**None.** Requests are short and made one at a time by one user. A queue would only matter for a
shared hosted backend serving many users.

## 9. External services and deployment

- **External services:** only the Bodhan API (or another API URL the user sets). No analytics or other network calls.
- **Deployment (Version 0):** none. Users load the `extension/` folder in Developer mode.
- **Production path (roadmap item 8):** publish to the Chrome Web Store with icons, a privacy policy and a listing, backed by a hosted proxy with sign-in, per-user limits and a spending cap, so users don't need their own keys. Settings already accepts another API URL and asks for permission to reach it.

## 10. Security

- **No key ships with the code.** Each user enters their own; it is stored in `chrome.storage.local` on that computer and never synced.
- **Only the service worker uses the keys**, sending them as `Authorization: Bearer` to the API URL in Settings. The Settings page reads them to display them. Web pages can't read extension storage. The content script could technically read it (Chrome allows content scripts to use `chrome.storage`), but it never does.
- **Permissions:** host access to `https://api.bodhan.ai/*` only; another API URL (https, or http for localhost) is requested when saved. The content script runs on every page so the bubble can appear anywhere, but it only reacts to selections and sends text only when the user clicks.
- **Error messages** don't repeat the server's authentication details.
- **Rendering:** results use `textContent`, never `innerHTML`.
- **Remaining risk:** anyone with access to the user's Chrome profile, or a malicious update to the extension, could read the keys. A backend removes this (§11).

## 11. Open decision: where the API key lives

**Status: waiting for the maintainers.** Version 0 keeps each user's own keys in the extension.

**The rule.** The cookbook's CONTRIBUTING.md says: *"Never expose a key in client-side code. Browser
and mobile recipes must call Bodhan through a small backend or serverless function."*

**The argument for Version 0's approach.** The rule's main risk is a *shared* key shipped in client
code, which anyone could extract. Here no key is shipped: each user enters their own, and it leaves
their computer only to reach Bodhan.

| Option | For | Against |
|---|---|---|
| **User's own key in the extension** (Version 0) | Nothing to run but the extension | Doesn't follow the rule's wording; the key is readable from the Chrome profile |
| **Local proxy** | Follows the rule: keys stay in `.env` on the user's computer, never in the browser; no hosting cost | Users also run a small Node server (`npm start`); more code to maintain |
| **Hosted proxy with sign-in** | Users need no key at all | Hosting, accounts and per-user limits: production work |
| **Both modes** | Flexible | Two code paths and two security models to explain |

**How the local proxy works**, if chosen: a dependency-free Node server on `127.0.0.1` reads the
keys from `.env`, answers only the extension (refusing web pages and non-local host names), checks
every request, and never logs text. The extension's Settings then ask for the proxy URL instead of
keys, and its host permission becomes `http://127.0.0.1/*`.

## 12. Testing

- **Unit tests** (`npm test`, `node:test`, no dependencies, no key or network): text splitting, the `/translate` → chat fallback, error handling (including `Retry-After` and request ids), mock mode, audio helpers, voices and settings checks.
- **UI harness** (`extension/dev/harness.html`): runs the content script in a normal tab with stubbed `chrome.*` APIs and a stored default language, for working on the card and testing language switching.
- **Live check** (`npm run check:live`): runs the extension's own client against the real API with keys from an optional `.env`.

## 13. Extending it

To add a translation feature or another Bodhan model: add a method to `extension/src/lib/bodhan.js`, a
message handler in `background.js`, and the UI entry point (a context menu, a card button). Add
tests alongside the existing ones in `tests/`.
