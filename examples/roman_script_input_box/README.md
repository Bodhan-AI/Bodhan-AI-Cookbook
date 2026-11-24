# Roman-script input box

> A chat input box where people type the way they text, in English letters ("kal meeting hai"), and the message is sent in their own script ("कल मीटिंग है").

**Bodhan models:** indic-transliterate
**Languages shown:** Hindi, Gujarati, Bengali, Tamil (the language menu lists all 25 the model supports)
**Format:** Python web app (standard library server, one HTML page) plus an evaluation script

## What it does

Most people in India type their own language in roman letters, because that is what a phone keyboard makes easy. This recipe is a WhatsApp-style input box. You type in roman letters, a preview line shows the text in the target script, and pressing Enter sends the native-script message. The original roman text stays under each bubble.

It also answers a practical question for anyone building this: should you convert **word by word** as the user types, the **whole sentence** at once, or a **hybrid** of the two? `eval/compare_modes.py` measures accuracy, consistency, latency and API calls for each, and its raw answers are committed so the numbers can be checked without a key.

<img src="screenshot.png" alt="Chat with a Gujarati and a Hindi message sent from roman input, and a live Hindi draft above the input box" width="320">

```
you type:   tame kem cho            (language: Gujarati)
preview:    તમે કેમ છો              (sentence pass · 535 ms)
you send:   તમે કેમ છો
            tame kem cho · 03:12 PM
```

## Setup

Requires Python 3.10+.

```bash
cd examples/roman_script_input_box
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your key from https://console.bodhan.ai
```

The key must have access to `indic-transliterate`. Keys are scoped per model: a key for another model gets `403 key_model_access_denied`.

## Run

```bash
python app.py
# open http://127.0.0.1:8000
```

Pick a language and a mode at the top, type in the box, and press Enter. `PORT=8080 python app.py` changes the port.

Check the comparison below without a key, or rerun it:

```bash
python eval/compare_modes.py --offline   # re-score the committed answers in eval/results/, no API calls
python eval/compare_modes.py             # call the API for anything not yet in eval/results/
```

Offline tests (no key, no network). They cover the client, the converter and the HTTP server:

```bash
python -m unittest discover -s tests
```

## How it works

```
browser (static/index.html)  ──POST /api/transliterate──▶  app.py  ──▶  converter.py  ──▶  bodhan_client.py  ──▶  api.bodhan.ai
```

| File | Role |
|---|---|
| `static/index.html` | The chat page: plain HTML, CSS and inline JS. It never sees the API key. |
| `app.py` | Standard-library HTTP server: serves the page and one JSON endpoint, validates input, maps API errors to clear messages. |
| `converter.py` | The conversion modes and the word cache. |
| `bodhan_client.py` | The only file that talks HTTP to Bodhan: route choice, request body, connection pool, 429 handling. |
| `eval/` | `compare_modes.py` and its test set and committed results; `probe.py` / `probe_language.py`, the API probes behind "Why the chat route needs an instruction". |
| `tests/test_offline.py` | 32 offline tests with a fake API. |

The conversion modes in `converter.py`:

- **sentence**: one call for the whole line.
- **word**: split into words, convert each new word in parallel (at most 4 at a time, and at most 20 new words per message), and put spaces, digits and punctuation back where they were. Answers go into an in-memory LRU cache, per language.
- **draft**: the same as word, but cache only. It makes no API calls, and unknown words stay in roman.

**Hybrid** (the default in the app) combines them:

1. **While you type**, each time you finish a word, you get a free **draft**.
2. **When you pause for 700 ms, or press Enter**, one **sentence** call is made. Its result is what gets sent. A pause and an Enter for the same text share one call.
3. **The cache learns from sentences.** When a sentence answer has as many words as the input, each word pair goes into the cache. So drafts improve the more you use the box, and they learn words in context.

A word only enters the cache if its answer is a single word written in the target script, with punctuation stripped. A wrong-script answer, like Gurmukhi for a Hindi request, is shown once but never cached.

## Word, sentence or hybrid?

Results from `python eval/compare_modes.py`, run on 24 November 2026: 20 everyday sentences, 5 each in Hindi, Gujarati, Bengali and Tamil.

- **dev**: 12 sentences used while designing the instruction.
- **held-out**: 8 sentences written afterwards, with punctuation, digits and English words, and never used to tune anything.

CER is character error rate against the closest accepted spelling (0 is perfect).

| Mode | Exact match (dev / held-out) | Mean CER, all 20 | Median latency per message | API calls |
|---|---|---|---|---|
| **sentence** (script named in the instruction) | **12/12 · 7/8** | **0.003** | **327 ms** | 20 |
| word (each word on its own) | 6/12 · 5/8 | 0.052 | 680 ms* | 76 |
| bare (no instruction, `language` field only) | 6/12 · 3/8 | 0.327 | 357 ms | 20 |

\* The word calls were made one after another (the eval is throttled). The figure is the slowest single word, which is what a message waits for when its words run in parallel, as they do in the app.

**Consistency.** Each sentence was asked 3 times in sentence mode. All 20 gave the same answer all 3 times.

What the mistakes look like:

| Input | Word by word | Whole sentence | Bare |
|---|---|---|---|
| `tame kem cho` (gu) | તમે કેમ **ચો** | તમે કેમ છો | **तुम लोग कौन हो** (Hindi, and a different meaning) |
| `maru naam ravi che` (gu) | મારું નામ રવિ **ચે** | મારું નામ રવિ છે | **మళ్లీ నన్ను రవి చెయ్యి** (Telugu) |
| `kya tum kal free ho?` (hi) | क्या तुम कल फ्री हो? | क्या तुम कल फ्री हो? | **ਕੀ ਤੁਸੀਂ ਕੱਲ੍ਹ ਫ੍ਰੀ ਹੋ?** (Punjabi) |
| `kal meeting hai please time pe aana` (hi) | … **कृपया समय** पे आना | … प्लीज टाइम पे आना | … **कृपया समय पर** आना |
| `naan veetla irukken` (ta) | நான் **வீட்டில் இருக்கும்** | நான் வீட்ல இருக்கேன் | நான் **வீட்டில் இருக்கிறேன்** |
| `amma saapittiya?` (ta) | அம்மா சாப்பிட்டியா? | அம்மா சாப்பிட்**டீ**யா? | அம்மா சாப்பிட்**டீ**யா? |

What this means:

- **Context matters more than speed.** Roman spelling is ambiguous. "ch" can be ચ or છ, and "che" only reads as છે ("is") inside a sentence. On its own, a word also tends to get *translated* ("please" became कृपया) or turned into formal written Tamil (வீட்ல became வீட்டில்).
- **Word by word is slower and costs more.** A message waits for its slowest word, and it used almost 4× the calls. With the model throttle at about 15 calls a minute, the cost matters even more than the accuracy.
- **Without the instruction, the chat route isn't reliable in any language.** Even a Hindi sentence came back in Gurmukhi, and 5 of the 10 Gujarati and Bengali sentences came back fully or partly in the wrong script.
- **Sentence mode isn't perfect.** Its one miss was a vowel-length slip (டீ for டி). The eval counts it as an error rather than adding it as an accepted spelling.

So the app sends the **sentence** result. For feedback while typing, it uses the free cache-only draft instead of one call per word. That's the **hybrid** mode, and it costs one call per pause.

Every answer behind these numbers is in `eval/results/api_answers.json`, and the per-sentence report is in `eval/results/compare_modes.json`.

## Why the chat route needs an instruction

The [API reference](https://console.bodhan.ai/api-docs/#transliteration-api) documents `POST /transliterate` with `{"text", "language", "script"}`, returning the answer in a `transliteration` field. When this recipe was built (November 2026), that route returned `404 {"detail":"Not Found"}` for every key tried, and other contributors reported the same (issue #34).

`POST /v1/chat/completions` with `"model": "indic-transliterate"` does answer. Earlier recipes send the raw text there with a `language` field. Probing that showed:

- `language` is ignored. "tame kem cho" with `gu`, `hi` or `bn` all came back in Devanagari, and a made-up code `xx` still got `200`.
- With nothing to steer it, the model often *translates* instead of transliterating. "please time pe aana" became "कृपया समय पर आना" ("kindly be on time") rather than "प्लीज टाइम पे आना".

Naming the target script in the message fixes both:

```json
{"model": "indic-transliterate",
 "messages": [{"role": "user", "content": "Transliterate into Gujarati script: tame kem cho"}]}
```

→ `તમે કેમ છો`. The raw probes are in `eval/probe.py` and `eval/probe_language.py`.

`bodhan_client.py` keeps this in one place. Set `BODHAN_TRANSLIT_ROUTE` in `.env`:

| Value | Behaviour |
|---|---|
| `chat` (default) | Chat route with the instruction. |
| `plain` | The documented `POST /transliterate`, reading `transliteration` and `truncated`. Covered by an offline test, but not yet run against the live API, since that route is down. |
| `auto` | Try `/transliterate` once, and fall back to `chat` for the rest of the run if it returns 404. Not the default, because that 404 took 1 to 24 seconds. |

## Rate limits

Two separate limits showed up:

- **Per-key request quota.** Response headers `X-Ratelimit-Api_key-Limit-Requests: 120` and `...-Max_parallel_requests: 10`, with matching `-Remaining-` headers. These names differ from the `x-ratelimit-limit-requests` in the docs. The window isn't documented; in testing it refilled within about ten minutes. A 404 doesn't count against it.
- **Per-model throttle.** After roughly 15 calls in a minute, it returns `429 {"type": "throttling_error", "message": "Maximum rate limit reached for the model"}` with `Retry-After` of up to about a minute (54 s was the longest seen).

That's why hybrid drafts are cache-only, and why word mode is capped at 20 new words per message. Calling the API for every finished word would hit the throttle within two or three messages of normal typing.

How a 429 is handled:

- **Short waits.** The client waits and retries once, but only if `Retry-After` is 3 seconds or less.
- **Longer waits.** The app shows "Bodhan is throttling this key. Try again in N s" and keeps the text in the box.
- **Budget errors.** A budget 429 is never retried.
- **The eval script.** As a batch job, it waits out `Retry-After` and continues. Every answer is saved as it arrives, so an interrupted run loses nothing.

## Security

- **The key stays on the server.** The browser only talks to `app.py`, and the key is never sent to it or logged. The client refuses to start with the `.env.example` placeholder.
- **Only its own page can use the server.** Otherwise any website open in your browser could quietly spend your quota through it. Three checks enforce this:
  - The `Host` header must be local, which blocks DNS rebinding.
  - The body must be `application/json`, so browsers send a CORS preflight, which is never granted.
  - Cross-site `Origin` and `Sec-Fetch-Site` headers are rejected.
- **Bounded input.** Messages are limited to 500 characters and request bodies to 8 KB, and every field must be a string.
- **What people type is never logged.** The server logs only the method, path and status. Unexpected errors are logged with a traceback but without the message text.
- **It listens on `127.0.0.1` by default.** Setting `HOST=0.0.0.0` makes it listen on your network, but it still answers only requests addressed to a local host name. Put it behind your own authenticated backend before sharing it.

## Limitations

- **Undocumented route.** The app uses the chat route plus an instruction the documentation doesn't describe. If the gateway changes how it handles that route, `bodhan_client.py` is the one place to fix.
- **Languages tested.** Only Hindi, Gujarati, Bengali and Tamil are tested end to end. The other languages are in the menu, marked "(untested)".
- **A sanity check, not a benchmark.** The eval has 20 sentences, 5 per language, and one person wrote the references. They allow common spelling variants (हूँ / हूं, ઑ / ઓ, যাব / যাবো).
- **Word cache.** It lives in memory and is cleared when the server restarts. A misaligned sentence pair could still teach it a wrong word in the right script; the next sentence pass corrects what gets sent.
- **Message length.** Messages are capped at 500 characters, since this is a chat box. Long text is outside its scope.
