# Book/Article Narrator — Software Specification

> Written to the Bodhan AI Cookbook software specification template (worked example:
> `examples/AndroidSpeak`). Version 0.3 matches the V0 code in this folder. Anything not measured
> or confirmed is marked **Assumption** or **To verify**. The companion design is in
> [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Document details

| Field | Value |
| --- | --- |
| App name | Book/Article Narrator (`examples/book_article_narrator`) |
| One-line description | Paste text or upload a text PDF, pick a language, and hear it translated and read aloud |
| Authors | Renuka (RenukaPatil07) |
| Bodhan point of contact | Ishvinder Sethi |
| Status | V0 built, in review |
| Version | 0.3 |
| Last updated | 30 Oct 2026 |
| Architecture document | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Code | `examples/book_article_narrator/` |

### Change history

| Version | Date | What changed |
| --- | --- | --- |
| 0.1 | 14 Oct 2026 | First draft |
| 0.2 | 16 Oct 2026 | Added saved history in SQLite, at a senior's request |
| 0.3 | 30 Oct 2026 | Updated to the built V0: all 23 languages, text-PDF input, dark mode, parallel speech, measured timings, emoji and link clean-up, Hindi native digits, findings from live API runs |

## 2. Summary

- **What it is:** A web app that translates pasted text, or the text of an uploaded PDF, into any of 22 Indian languages (or English) and reads the result aloud as one narration.
- **Who it is for:** People who understand a language better than they can read it, the helpers who read notices to them, and developers who want a working example of chaining two Bodhan APIs.
- **Bodhan models used:** `indic-translate` and `indic-speak`.
- **Platform:** Python and Streamlit, run locally, with an optional local SQLite history.
- **What V0 proves:** Text of up to 2,000 characters can be narrated in one click in every language Bodhan's speech model supports, and a cut-off translation is never narrated.

## 3. Problem and users

**Problem.** Official notices, news and book chapters are often in a language the reader finds hard to read. A translator gives text, not a one-click way to *listen*. The speech API handles only about 30 seconds per request and does not split text, so developers must solve splitting, joining and error handling themselves.

**Evidence.** No formal user research yet. Plan: five short conversations (family, neighbours, colleagues) about struggling with official notices; results to be added here.

| Persona | Who they are | What they need |
| --- | --- | --- |
| Listener (primary) | Someone who finds the notice's language hard to read: an older adult, low literacy or low vision | To hear the text in their own language |
| Helper (secondary) | A family member, volunteer or help-desk worker | To paste text or upload a PDF, pick a language and press play |
| Developer (secondary) | A student or open-source contributor | A clean example of Translate then Text-to-Speech with the length limit handled |

**Context.** Content is shown in each language's own script (Noto fonts, right-to-left for Urdu, Kashmiri and Sindhi). The app needs internet. It targets adults and should not be used with children's personal data.

**How it differs from other cookbook projects.** Other projects take speech in and produce text, or handle live speech. This one takes text in and produces narrated audio.

## 4. Goals, non-goals and V0 scope

### Goals

1. G1: Paste text or upload a text PDF and hear a narration in the chosen language in one click.
2. G2: Never narrate a translation that was cut off, and say so clearly.
3. G3: Be readable code that teaches how to chain `indic-translate` and `indic-speak`, handle the speech length limit and keep the key safe.
4. G4: Reopen or delete earlier narrations without paying for them again.
5. G5: Cover every language the Bodhan speech and translation models support.

### In V0

- Text box (2,000-character cap) with a live counter that updates as you type, and a Clear button
- Upload of a text PDF (first 10 pages read; text trimmed to the cap with a notice)
- 23 target languages (22 Indian languages plus English), chosen from clickable chips, each with its Bodhan voices
- Speaking style: neutral, audiobook, news
- Paragraph-by-paragraph translation with a truncation check
- Original and translation side by side, right-to-left aware
- Sentence-by-sentence speech, run 3 at a time, joined into one WAV with pauses
- Emoji and web links removed from the speech only (they stay in the shown text)
- Hindi numbers converted to Devanagari digits for speech, so they are read in Hindi
- Progress status, audio player and WAV download
- Optional history in local SQLite (off by default), newest 20 kept, with open, delete and clear all
- Light and dark theme that follows the browser or Streamlit's menu
- Friendly errors with a "Details for support" box (request id when Bodhan gives one, status, code, UTC time)
- Demo mode (`NARRATOR_DEMO=1`) that runs with no key and no cost
- README, `.env.example`, pinned requirements, a sample text in the README, 91 offline tests

### Deferred to V1

- OCR for scanned PDFs and photos (`indic-ocr`; PNG or JPEG only, one image per request, needs its own key)
- Better reading of PDFs whose text layer scrambles Indian-language vowel signs
- Full-length books: chapters, saved progress, resumable narration
- Native digits for languages other than Hindi (to test by listening)
- Sign-in, per-user history, bring-your-own-key and a hosted demo
- Streaming playback before all clips are ready
- Native-speaker quality review for every language

### Non-goals

Not an official translation service; not a screen-reader replacement; no offline mode; no history sync between devices or users.

## 5. Platform and form factor

| Question | Answer |
| --- | --- |
| Platform | Python web app, Streamlit 1.66.1 |
| Why | The helper needs only a browser. One Python project runs everything and the key stays on the server |
| Targets | Python 3.10 or later, current desktop browser |
| Backend | The Streamlit server is the backend |
| How to run | Clone, create a venv, `pip install -r requirements.txt`, copy `.env.example` to `.env`, add keys, `python -m streamlit run app.py` |
| Offline | None |

## 6. User journeys and functional requirements

- **J1** A helper pastes a notice, chooses Marathi and the audiobook style, clicks **Translate and narrate**, reads the side-by-side text and plays the audio.
- **J2** A reader listens to a news paragraph in Hindi with the news style.
- **J3** A developer follows the README from a fresh clone to a working run.
- **J4** A translation is cut off: the app stops, names the paragraph and produces no audio.
- **J5** A user reopens or deletes a saved narration without any API call.
- **J6** A helper uploads a PDF; the text is extracted, trimmed to the cap if needed, and narrated.

| ID | The app shall… | Priority | Acceptance criterion |
| --- | --- | --- | --- |
| FR-1 | Accept pasted text with a live counter, and reject empty or over-limit input before any API call | Must | Empty and 2,001-character inputs show a message and make no call |
| FR-2 | Offer all 23 languages from `config.LANGUAGES`, each with its voices | Must | The live smoke test passes for every language |
| FR-3 | Offer three styles and a voice per language; neutral sends no style | Must | Style appears in `instructions` only when chosen |
| FR-4 | Translate each paragraph with the translate model | Must | A 3-paragraph text makes 3 translate calls |
| FR-5 | Treat any translation that did not finish normally as truncated, and stop | Must | With a mocked truncated reply, no audio is produced and the message names the paragraph |
| FR-6 | Show original and translation as soon as translation finishes | Must | If speech fails, the translation stays on screen |
| FR-7 | Split translations at `. ? ! । ॥ ۔ ؟ ꯫ ᱾ ᱿` and split sentences longer than 250 characters at spaces | Must | No speech request exceeds 250 characters |
| FR-8 | Remove emoji and links from speech text only | Must | Test shows speech input has none; shown text keeps them |
| FR-9 | Convert ASCII digits to Devanagari for Hindi speech only | Should | Test covers digit runs, mixed words and other languages |
| FR-10 | Speak chunks 3 at a time, keep their order, and join with pauses | Must | Audio order matches text order in a test with out-of-order replies |
| FR-11 | Show progress by stage and count | Should | Status text changes at every step |
| FR-12 | Offer a WAV download | Should | File plays in a standard player |
| FR-13 | Back off on 429, 5xx and network errors (3 attempts, honour `retry-after`); retry a 401 only if that key worked before; show a friendly message and keep the request id when present | Must | Mocked 429-then-200 completes; mocked 401 says to check the key |
| FR-14 | Read keys from the environment and never show or log them | Must | No key in UI, logs or repository |
| FR-15 | Keep results in session state so playing audio makes no API call | Must | Pressing play makes zero calls |
| FR-16 | Read the text of an uploaded PDF (10 pages), warn when trimmed, warn when the text looks mis-ordered, and refuse scanned, locked or invalid files with a clear message | Must | Seven test PDFs behave as described in the test plan |
| FR-17 | Save a narration to local SQLite only when the user ticks the box (off by default), keep the newest 20, and save once per run | Must | One row per run; reruns add none |
| FR-18 | List history newest first; reopen with no API call; delete one; clear all | Must | Opening makes zero calls; delete removes the row |
| FR-19 | Use parameterised SQL and never log pasted text | Must | A hostile string is stored as plain text and the table survives |
| FR-20 | Follow the light or dark theme | Should | Text is readable in both |

## 7. Bodhan API usage

| Model | Route | Sends | Uses |
| --- | --- | --- | --- |
| `indic-translate` | `POST /v1/chat/completions` | `model`, one paragraph as the user message, `target_language_code` | The reply text and `finish_reason` (anything but `stop` counts as truncated) |
| `indic-speak` | `POST /v1/audio/speech` | `model`, `input` (one chunk), `voice`, `instructions` (JSON string with `lang` and optional `style`) | A WAV body (PCM16, 24 kHz, mono) |

### Measured and observed

- **Speech speed:** about real time. 876 characters took 77 s one by one and 72 s with 3 in parallel (one slow clip dominated). A full 2,000-character text takes roughly 2 to 3 minutes, so the progress display matters.
- **Rate limits:** translate responses carry `X-Ratelimit-Api_key-Limit-Requests: 120` and `Max_parallel_requests: 10`. Speech responses carry no rate-limit headers. Speech sometimes returns 429 on rapid calls, which the retry handles.
- **Keys:** one key per model. Translate and speak need separate keys, and OCR would need a third.
- **Request id:** translate sends `Llm_provider-X-Request-Id`; speech sends none. The error box says "not provided by Bodhan for this call" when absent.
- **Audio label:** speech returns a valid WAV labelled `audio/mpeg`. The app checks the `RIFF` marker instead of trusting the label.
- **Route:** the documented `/translate` returned 404 for our key, so the chat-completions route is used. To confirm with Bodhan.
- **OCR:** takes PNG or JPEG only, not PDFs, so PDF OCR needs page rendering first (V1).
- **Voices:** a Bodhan voice sometimes sounds slightly different between runs of the same text. One apparent voice flip on a long paragraph could not be reproduced in 11 short clips.
- **Hindi numbers:** ASCII digits are read English-style by the Hindi voice. Devanagari digits and spelled-out numbers are read in Hindi.
- **Known pronunciation issues** (reported to Bodhan): "स्कूल प्राचार्यों" is mispronounced; Marathi output from Hindi source sometimes mixes Hindi words and has grammar slips; Urdu quotation marks.

**Companion services:** none.

## 8. Non-functional requirements

| Area | Requirement | Check |
| --- | --- | --- |
| Latency | Progress shown at every step; a 2,000-character text in about 2 to 3 minutes (measured) | Timed runs |
| Reliability | Failure never loses work already shown | Test with mocked speech failure |
| Correctness | A truncated translation is never narrated | Automated test |
| Low bandwidth | Audio is about 2.9 MB per minute | Arithmetic |
| Accessibility | Labelled widgets; text shown beside the audio; light and dark themes | Manual check |
| Localisation | Interface in English; content in 23 languages; right-to-left support | Manual check of scripts |
| AI output quality | Hindi listened to; other languages checked only for working audio | Native-speaker review is V1 |
| Storage | A failed save shows a warning and the narration still plays | Test |

## 9. Cost and usage estimate

Prices (verify on the Bodhan console): translation ₹0.20 per 10,000 output tokens, speech ₹6.00 per 10,000 input characters.

| Model | Units at the 2,000-character cap | Cost (₹) |
| --- | --- | --- |
| `indic-translate` | about 0.3 (assumption) | 0.06 |
| `indic-speak` | 0.2 | 1.20 |
| **Total** | | **about 1.26** |

A typical 500-character text costs about ₹0.32. Speech is about 95 percent of the cost. The all-language smoke test costs about ₹1 to ₹2. Reopening a saved narration costs nothing. After V0, the person running the app pays with their own keys.

## 10. Security, privacy and data handling

**Key custody.** Keys are read from environment variables (`.env`, git-ignored) on the server, never rendered, logged or sent to the browser. `.env.example` has placeholders only.

| Data | Sent to | Stored | Personal? |
| --- | --- | --- | --- |
| Pasted or PDF text | Bodhan translate | Session memory; local SQLite only if the user ticks Save | Possibly |
| Translation | Bodhan speech | Same | Possibly |
| Audio | Nowhere | Session memory; a BLOB in local SQLite only if saved; user's disk if downloaded | Possibly |
| Keys | Bodhan, as bearer tokens | `.env`, never committed | Credential |

- **History is local and single-user.** The database is `narrator.db`, git-ignored, unencrypted. If the app were hosted, every visitor would share one history, so a hosted version must keep history off or add sign-in. The README warns against saving sensitive text on a shared computer.
- **Deletion:** one narration or all, from the page. Deleting `narrator.db` removes everything.
- **SQL injection:** parameterised queries only.
- **Logs:** status, timing and request id only, never text or keys.
- **PDFs:** processed in memory and not stored unless saved with a narration. Sample PDFs are synthetic.
- **Copyright:** users are responsible for having the right to translate and narrate what they paste.
- **Abuse:** local use by the key owner cannot become an open proxy. A hosted copy needs bring-your-own-key or a limited demo mode.
- **Prompt injection:** output is only shown and spoken, with no tools attached.
- **Open question:** whether Bodhan retains text sent to its APIs (Q4).

## 11. UX and design

Single page, branded orange (#ff6207) with Manrope, Syne and Noto fonts, light and dark themes via Streamlit's theme settings and `color-mix` CSS.

| Element | Behaviour |
| --- | --- |
| Text area and PDF upload | Live counter updates while typing; small "✕ Clear" button empties both |
| Language chips | 23 chips generated from `config.LANGUAGES`; voice and style selectors follow |
| Translate and narrate | Progress status; side-by-side result; audio player; download |
| History (optional) | Expander with Open, Delete and Clear all |
| Errors | Plain message plus a "Details for support" box |

The footer names the Bodhan APIs used. The Bodhan logo is not used.

## 12. Success criteria and demo plan

| Criterion | Target | Measured by |
| --- | --- | --- |
| Every language works end to end | All 23 pass the live smoke test | `smoke_test.py` results table |
| No incomplete narration | 0 truncated translations narrated | Automated test |
| Reproducibility | Fresh clone to a working run using only the README | A fresh-clone run |
| History | Reopen makes zero API calls | Automated test and manual check |
| Offline tests | 91 pass; Ruff clean | `pytest`, `ruff` |

**Demo (2 to 3 minutes):** paste an English notice and narrate it in Marathi; upload a PDF and narrate it in Hindi; show the side-by-side text and download; show the friendly error for a wrong key; show dark mode and history.

## 13. Milestones

| Milestone | Date | Done when |
| --- | --- | --- |
| Spec and architecture drafted | 14 to 15 Oct 2026 | Done |
| API experiments | 20 Oct 2026 | Done: route, keys, speed, rate limits, formats found |
| V0 built and tested | 29 Oct 2026 | Done: FR-1 to FR-20 met on live calls and 91 tests pass |
| PR opened | 30 Oct to 2 Nov 2026 | README, video and validator done |

## 14. Risks, dependencies and open questions

| ID | Risk | Mitigation |
| --- | --- | --- |
| R1 | Cut-off translation reads fluently | Truncation check stops the run (FR-5) |
| R2 | A speech chunk is too long | 250-character limit; chunks planned before any call |
| R3 | Slow narration | Parallel speech; progress display; streaming is V1 |
| R4 | Voice quality uneven across languages | Say plainly that only Hindi was reviewed; native review in V1 |
| R5 | A translation mistake in a date or amount misleads | "Aid, not official translation" note; original shown beside translation |
| R6 | Rate limits (speech 429) | Capped retries, 3 workers, honour `retry-after` |
| R7 | Streamlit re-runs repeat paid calls | Results kept in session state |
| R8 | Personal text on disk | History off by default; local; delete controls; README warning |
| R9 | PDF text layer scrambles Indian-language vowel signs | Warning shown; OCR in V1 |
| R10 | Hosted copy shares one history and spends the owner's credit | Do not host V0 as is; BYOK and history off first |

**Open questions:** Q1 Does Bodhan plan one key for all models? Q2 Is `/translate` expected to work for these keys? Q3 Does Bodhan retain submitted text? Q4 Which styles suit which languages? Q5 Should history expire automatically?

## 15. Open source and contribution

| Item | Answer |
| --- | --- |
| Code location | `examples/book_article_narrator/` |
| Licence | The cookbook's licence (to confirm) |
| Dependencies | Pinned `name==version` in `requirements.txt` |
| Configuration | `.env.example`: `BODHAN_TRANSLATE_API_KEY` and `BODHAN_SPEAK_API_KEY` (or one `BODHAN_API_KEY` if a single key covers both), optional `NARRATOR_DEMO`, `NARRATOR_DB` |
| Sample data | A synthetic sample notice in the README (the repository ignores files in `sample_data/` by design) |
| AI assistance | AI tools helped research, draft, code and test this recipe; disclosed in the PR |

## 16. Review

*To be completed by the reviewer.*
