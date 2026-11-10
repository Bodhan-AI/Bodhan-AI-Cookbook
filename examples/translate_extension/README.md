# Translate extension

> A Chrome/Edge extension that translates any text you select on a webpage into an Indian language and reads it aloud.

**Bodhan models:** indic-translate, indic-speak
**Languages shown:** all 26 indic-translate targets: English, Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Punjabi, Odia, Assamese, Urdu, Nepali, Sanskrit, Konkani, Maithili, Dogri, Bodo, Santali, Kashmiri and Manipuri and Sindhi (two scripts each)
**Format:** Chrome/Edge extension (Manifest V3, plain JavaScript, no build step, no server)

## What it does

Select text on any page and click **अ** (or right-click → **Translate with Bodhan**): the translation appears in a card next to the selection. `indic-translate` detects the source language; you choose the target language and the output script (native, Roman, or code-mixed). **Listen** reads the translation aloud with an `indic-speak` voice. The toolbar popup does the same for text you type or paste.

Each user brings their own Bodhan keys. They are saved only in the extension's storage on their computer and sent only to `api.bodhan.ai` (see [Your API keys](#your-api-keys)).

## Setup

You need **Chrome 116+** or **Edge 116+** and two Bodhan keys from [console.bodhan.ai](https://console.bodhan.ai) (**Virtual Keys**). Bodhan keys work with one model each, so create one for `indic-translate` and one for `indic-speak` (needed only for **Listen**). There is nothing to install or build.

1. Open `chrome://extensions` (or `edge://extensions`) and turn on **Developer mode**.
2. Click **Load unpacked** and choose the `extension/` folder inside this recipe.
3. The Settings page opens. Paste both keys, choose your default language, and click **Save & test**.

No key yet? Tick **Mock mode** in Settings to try the whole UI with fake translations and a beep.

The extension is built for Chromium browsers. Firefox and Safari are not tested yet.

## Run

**Save & test** translates "Hello, how are you?" into your default language and checks the speech key. Then, on any page:

- **Select text → click अ**, or **right-click → Translate with Bodhan**. The card uses your default language. Pick another language in the card to translate the same text again; that choice applies to this card only.
- **Listen** reads the translation aloud, **Copy** copies it, **Esc** closes the card.
- **Toolbar button:** type or paste text, then **Ctrl+Enter**. Its language picker also applies to that translation only.

Change the default language at any time in Settings.

### Optional: check your keys from a terminal

With Node.js 20.12+, copy `.env.example` to `.env`, add both keys, and run:

```bash
npm run check:live
```

```
translate → hi (native): बैठक स्थगित कर दी गई है।
translate → ta (roman): Kootam thalaimurai seyyappattullathu.
translate → bn (native): সভাটি স্থগিত করা হয়েছে।
speak (hi, Kavya): 86060 bytes → outputs/live_check.wav
```

The extension itself never reads `.env`.

## How it works

```
web page ── content script (bubble + card)
                │ messages
toolbar popup ──┤
settings page ──┤
                ▼
        service worker (background.js) ── HTTPS + your key ──► api.bodhan.ai
        check, split, cache, choose voice
```

1. The content script shows the **अ** bubble for a selection and sends the text to the service worker. It holds no key and makes no requests.
2. The service worker refuses empty text, text over 5,000 characters and unknown languages, and answers repeats from its cache. Long text is split at paragraph and sentence breaks into pieces of 1,000 characters (500–5,000 in Settings), translated one at a time.
3. `bodhan.js` calls `indic-translate` at `POST /translate`. If that route returns 404, it uses the equivalent `POST /v1/chat/completions` instead. A cut-off translation is flagged in the card.
4. **Listen** splits the translation into short segments and fetches each from `indic-speak` while the previous one plays, so sound starts quickly.

| File | What it does |
|---|---|
| [`extension/manifest.json`](extension/manifest.json) | Manifest V3: parts, permissions, access to `api.bodhan.ai` |
| [`extension/src/background.js`](extension/src/background.js) | Service worker: message router, checks, cache, context menu. Start reading here |
| [`extension/src/lib/bodhan.js`](extension/src/lib/bodhan.js) | The only file that makes network requests: the Bodhan calls, errors, mock mode |
| [`extension/src/lib/translate.js`](extension/src/lib/translate.js), [`text.js`](extension/src/lib/text.js) | Splitting long text and translating it piece by piece |
| [`extension/src/lib/languages.js`](extension/src/lib/languages.js) | Languages, scripts and voices |
| [`extension/src/lib/settings.js`](extension/src/lib/settings.js) | Settings and keys in `chrome.storage.local` |
| [`extension/src/content/content.js`](extension/src/content/content.js) | The in-page bubble and card, in a Shadow DOM so page styles can't break it |
| [`extension/src/popup/`](extension/src/popup/), [`extension/src/options/`](extension/src/options/) | Toolbar popup and Settings page |
| [`scripts/live_check.js`](scripts/live_check.js) | Terminal check of your keys against the real API |

The full design is in [`docs/SPECIFICATION.md`](docs/SPECIFICATION.md) (what and why) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (how, and the choices behind it).

### Your API keys

- Saved in `chrome.storage.local`: on this computer only, never synced.
- Used only by the service worker and sent only to the API URL in Settings (`https://api.bodhan.ai` by default). Web pages can't read extension storage.
- No key is shipped in the code.

> **Open decision.** The cookbook asks browser recipes to call Bodhan through a small backend. This recipe keeps each user's own key in the extension instead, since no shared key is shipped. The maintainers will decide whether that is acceptable; a local-proxy design that keeps keys out of the browser is the alternative. See [`docs/ARCHITECTURE.md` § 11](docs/ARCHITECTURE.md#11-open-decision-where-the-api-key-lives).

### Tests

No key, network or browser needed:

```bash
npm test
```

To work on the card without reloading the extension, serve the recipe folder and open the harness, which stubs the `chrome.*` APIs:

```bash
python -m http.server 5173 --bind 127.0.0.1
```

Open http://127.0.0.1:5173/extension/dev/harness.html and select some text, or run `__harness.open('some text')` in the console. After changing extension code, click **Reload** on `chrome://extensions` and refresh the page you're testing.

## Limitations

- **Up to 5,000 characters** per translation. Each paragraph is translated separately, in pieces of up to 1,000 characters, so a translation makes roughly one request per 1,000 characters, or one per paragraph when paragraphs are short. Each key allows 120 requests and 10 at a time; if the limit is reached, the card says when to try again and never retries on its own.
- **Repeated text:** a selection that repeats the same sentences many times may come back shorter, without a warning.
- **No source-language choice:** `indic-translate` detects it. Roman output writes the target language in Latin letters; it is not a translation into English.
- **Code-mixed output** isn't available for Kashmiri (Devanagari) and Sindhi (Perso-Arabic); those use native script.
- **Listen** starts after a short pause. Languages without their own voice are read by a Hindi voice.
- **Pages the extension can't run on:** `chrome://` pages, the Chrome Web Store and the built-in PDF viewer. Use the toolbar popup there.
- **Chromium browsers only** (Chrome, Edge). Firefox and Safari are not tested.
- **Each user needs their own Bodhan keys.**

## Ideas for contributors

Translation is the core of this extension, so the roadmap improves translation first, in priority order:

| # | Idea | Where to start |
|---|---|---|
| 1 | UI redesign of the card, popup and settings (a good moment to move to an extension framework such as WXT) | the CSS and HTML at the top of `content.js`; try it in `extension/dev/harness.html` |
| 2 | Translate the whole page in place, with a way back to the original | walk the page's text nodes and send them through `translateText` |
| 3 | Fewer requests for short paragraphs, by sending several in one request | `chunkText` in `text.js`; first check with a live test that paragraph breaks come back intact |
| 4 | Usage counter and optional daily limit in Settings | count requests in `background.js`, show them in `options/` |
| 5 | Translate what you type in a text box and replace it | a card action for editable fields |
| 6 | Keyboard shortcut and full keyboard and screen-reader use | `commands` in the manifest; focus handling in `content.js` |
| 7 | Firefox support | `background.scripts` and `browser_specific_settings.gecko` in the manifest (`docs/ARCHITECTURE.md` § 3.1) |
| 8 | Chrome Web Store release with a hosted backend, so users don't need their own keys | icons, privacy policy, store listing (`docs/ARCHITECTURE.md` § 9) |

**Maybe later**, if the extension grows beyond translation: translating text in images (`indic-ocr`), voice typing (`indic-transcribe`), transliterating names in forms (`indic-transliterate`), Safari support, and translation history.

Include tests for new logic in `extension/src/lib/`.
