# Translate extension — Software Specification (Version 0)

**Owner:** Sumit · **Status:** Version 0 · **Created:** 14 Oct 2026 · **Updated:** 10 Nov 2026
**Bodhan models:** `indic-translate`, `indic-speak` · possibly later: `indic-ocr`, `indic-transcribe`, `indic-transliterate`
**Type:** Chrome/Edge extension (Manifest V3), no server · **Architecture:** [ARCHITECTURE.md](ARCHITECTURE.md)

## 1. Problem

Indian-language readers browse a web that is mostly in English, and English readers regularly meet
Indic-language content: news, government notices, forms, comments. To understand it they copy the
text into a separate translation site, losing their place and the page's context. Listening to a
passage in their own language usually means yet another tool.

## 2. Why this project

- **It works where people already read.** The translation appears next to the selected text on any
  page: no copying, no second tab.
- **It fits an existing habit.** Selecting text and clicking a button, or using the right-click
  menu, is something every reader already does. There is no app to open and no account to create.
- **It is easy to adopt.** One folder loaded in the browser and the user's own keys: no install, no
  server, no build step.
- **It is a reusable starting point.** The shell (selection bubble, card, popup, settings, one API
  client) lets other Bodhan models be added as new actions, and shows developers how to call Bodhan
  from a Manifest V3 extension without exposing a key to web pages.

## 3. Objective

Bring Bodhan to any webpage: select text, see the translation in place, and optionally hear it.
Version 0 does one job well: translation, plus read-aloud. Translation stays the focus; other Bodhan
models can be added later if the extension grows.

## 4. Target users

- Regional-language readers on English sites: students, job seekers, citizens reading government portals.
- English or other-language readers meeting Indic content.
- Developers looking for a working reference for Bodhan in a browser extension.

## 5. Version 0 features

1. **Selection bubble:** selecting text shows an **अ** button; clicking it opens a card with the translation next to the selection.
2. **Right-click menu:** "Translate with Bodhan" on any selection.
3. **Toolbar popup:** type or paste text and translate it (useful where selecting is awkward, such as PDFs).
4. **Target language:** 26 target codes. The default is set in Settings; the card and popup can switch language for one translation without changing the default. There is no source picker: `indic-translate` detects the source.
5. **Output script:** native, Roman (the target language in Latin letters) or code-mixed.
6. **Long text:** split at paragraph and sentence breaks into request-sized pieces; a cut-off translation is flagged.
7. **Listen:** reads the translation aloud with `indic-speak` (female or male voice per language).
8. **Copy** the translation.
9. **Settings:** translation key and speech key (Bodhan keys work with one model each), API URL, default language and script, voice, the selection bubble on or off, request size, and mock mode for trying the UI without credits.
10. **Bring your own keys:** stored only in the extension's local storage and sent only to the Bodhan API; no key ships with the code.

## 6. Functional requirements

| # | Requirement | Where it is met |
|---|---|---|
| F1 | Selecting 1–5,000 characters shows the bubble at the end of the selection; it hides on a click elsewhere or Esc | `content.js` |
| F2 | A translation is in the chosen language and script, with paragraph breaks kept | `translate.js`, `text.js` |
| F3 | Empty text, text over 5,000 characters or an unknown language is refused with a clear message before any API call | `background.js` |
| F4 | A translation cut off by Bodhan shows a warning | `bodhan.js`, `content.js` |
| F5 | Listen starts within a few seconds and plays the whole translation, fetching the next segment while one plays | `text.js`, `content.js`, `player.js` |
| F6 | The same text in the same language is not requested twice in a session | `background.js` |
| F7 | Every failure says what went wrong and what to do (key missing or rejected, key not allowed for a model, rate limit with the wait when known, network), and includes Bodhan's request id when there is one | `bodhan.js` |
| F8 | Keys are never shipped in code, synced, logged, or readable by web pages | `settings.js`, `background.js`, `manifest.json` |
| F9 | The whole UI can be tried without a key (mock mode) | `bodhan.js` |
| F10 | Picking a language in the card or popup translates into it at once and changes nothing else; only Settings changes the default | `content.js`, `popup.js` |

## 7. User flow

```
Select text ─┬─► अ bubble ─► click ─┐
             └─► right-click menu ──┤
Toolbar popup ─► type or paste ─────┤
                                    ▼
          service worker: check, split ─► Bodhan indic-translate
                                    ▼
          card: translation · language picker · Copy · Listen (─► indic-speak)
```

## 8. Scope and roadmap

**In Version 0:** the features above, on Chromium browsers (Chrome, Edge), loaded unpacked by a user
with their own Bodhan keys.

**Future scope, open for contributors.** Translation stays the core, so these come first, in
priority order:

| # | Feature | Why it matters |
|---|---|---|
| 1 | **UI redesign** of the card, popup and settings | A clearer, more polished card is the most visible improvement. This is also the point to move to an extension framework (see [ARCHITECTURE.md § 3](ARCHITECTURE.md#3-frontend-the-extension)) |
| 2 | **Translate the whole page** in place, with a way back to the original | The most useful step beyond single selections |
| 3 | **Fewer requests for short paragraphs** by sending several in one request | Saves quota on lists and short-paragraph text; needs a live test that paragraph breaks come back intact |
| 4 | **Usage counter and optional daily limit** in Settings | Lets users see and cap how much of their quota the extension uses |
| 5 | **Translate what you type** in a text box and replace it | Brings translation to writing (replies, comments, forms), not only reading |
| 6 | **Keyboard shortcut and accessibility**: open the card from the keyboard, full keyboard and screen-reader use | Faster for regular users and usable by everyone |
| 7 | **Firefox support** | A small manifest change plus testing ([ARCHITECTURE.md § 3.1](ARCHITECTURE.md#31-browser-support)) |
| 8 | **Chrome Web Store release** with a hosted backend, so users don't need their own keys | The production path ([ARCHITECTURE.md § 9](ARCHITECTURE.md#9-external-services-and-deployment)) |

**Maybe later**, only if the extension grows beyond translation:

| Feature | Bodhan model |
|---|---|
| Right-click an image → read and translate its text | `indic-ocr` |
| Voice typing in any text box | `indic-transcribe` |
| Transliterate names and addresses in forms | `indic-transliterate` |
| Safari support | — |
| Translation history | — |

## 9. Technology requirements

Chrome or Edge 116+ and a Bodhan key for `indic-translate` (plus one for `indic-speak` to use
Listen). No build step, no npm dependencies, no server. Node.js 20.12+ only for the tests and the
optional key check. Choices and their justification are in [ARCHITECTURE.md](ARCHITECTURE.md).

## 10. Open questions

- **Where the API key lives.** The cookbook asks browser recipes to call Bodhan through a backend; this recipe keeps each user's own key in the extension. The maintainers' decision is pending. See [ARCHITECTURE.md § 11](ARCHITECTURE.md#11-open-decision-where-the-api-key-lives).
- **Rate-limit window.** Each key allows 120 requests, but the time window isn't documented.
- **Differences between the API reference and the live API**, to raise with Bodhan: `/translate` returns 404 (chat completions works); the rate-limit headers have different names; speech returns WAV labelled `audio/mpeg`; an unknown voice returns 500 rather than 422.
