# Roman-script input box

> A chat input box where people type the way they text, in English letters ("kal meeting hai"), and the message is sent in their own script ("कल मीटिंग है").

**Bodhan models:** indic-transliterate
**Languages shown:** Hindi, Gujarati, Bengali, Tamil (the language menu lists all 25 the model supports)
**Format:** Python web app (standard library server, one HTML page) plus an evaluation script

## What it does

Most people in India type their own language in roman letters, because that is what a phone keyboard makes easy. This recipe is a WhatsApp-style input box. You type in roman letters, a preview line shows the text in the target script, and pressing Enter sends the native-script message. The original roman text stays under each bubble.

It also answers a practical question for anyone building this: should you convert **word by word** as the user types, the **whole sentence** at once, or a **hybrid** of the two? `eval/compare_modes.py` measures accuracy, latency and API calls for each.

```
you type:   tame kem cho            (language: Gujarati)
preview:    તમે કેમ છો
you send:   તમે કેમ છો
            tame kem cho · 14:02
```

## Setup

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

Pick a language and a mode at the top, type in the box, and press Enter.

To repeat the comparison below:

```bash
python eval/compare_modes.py            # about 70 API calls; answers are cached in outputs/
python eval/compare_modes.py --offline  # re-score from the cache, no API calls
```

Offline unit tests (no key needed):

```bash
python -m unittest discover -s tests
```

## How it works

```
browser (static/index.html)  ──POST /api/transliterate──▶  app.py  ──▶  converter.py  ──▶  bodhan_client.py  ──▶  api.bodhan.ai
```

1. `static/index.html` is the chat page. It never sees the API key; it only talks to `app.py`.
2. `app.py` is a standard-library HTTP server with one JSON endpoint. It validates the language, mode and length, and logs only method, path and status, never what people type.
3. `converter.py` implements the modes:
   - **sentence**: one call for the whole line.
   - **word**: split into words, convert each new word (in parallel, at most 4 at a time), and put spaces, digits and punctuation back where they were. Answers are kept in an in-memory LRU cache, per language.
   - **draft**: the same as word, but cache only. It makes no API calls, and unknown words stay in roman.
4. `bodhan_client.py` is the only file that knows about HTTP: the route, the request body, timeouts, and 429 handling.

**Hybrid** (the default in the app) combines them:
- **While you type:** each time you finish a word, you get a free **draft**.
- **When you pause for 700 ms, or press Send:** one **sentence** call, which is what gets sent.
- **The cache gets smarter:** when a sentence answer has the same number of words as the input, each word pair goes into the cache. So the drafts improve the more you use it, and the words they learn come from context.

## Word, sentence or hybrid?

Results from `python eval/compare_modes.py`: 12 everyday sentences, 3 each in Hindi, Gujarati, Bengali and Tamil, run on 24 November 2026. CER is character error rate against the closest accepted spelling (0 is perfect).

| Mode | Mean CER | Exact match | Median latency per message | API calls |
|---|---|---|---|---|
| **sentence** (script named in the instruction) | **0.000** | **12/12** | **285 ms** | 12 |
| word (each word on its own, in parallel) | 0.056 | 6/12 | 566 ms | 45 |
| bare (no instruction, `language` field only) | 0.352 | 6/12 | 368 ms | 12 |

What the mistakes look like:

| Input | Word by word | Whole sentence | Bare |
|---|---|---|---|
| `tame kem cho` (gu) | તમે કેમ **ચો** | તમે કેમ છો | **तुम लोग कौन हो** (Hindi, and a different meaning) |
| `maru naam ravi che` (gu) | મારું નામ રવિ **ચે** | મારું નામ રવિ છે | **మళ్లీ నన్ను రవి చెయ్యి** (Telugu) |
| `kal meeting hai please time pe aana` (hi) | … **कृपया समय** पे आना | … प्लीज टाइम पे आना | … **कृपया समय पर** आना |
| `enakku romba pasikkuthu` (ta) | … **பசிக்கிறது** | … பசிக்குது | … பசிக்குது |

What this means:

- **Context matters more than speed.** Roman spelling is ambiguous: "ch" can be ચ or છ, and "che" only reads as છે ("is") inside a sentence. On its own, a word also tends to get *translated* ("please" became कृपया, "time" became समय) or formalised (colloquial Tamil பசிக்குது became the textbook form).
- **Word by word is slower overall and costs more.** A message waits for its slowest word, and it used almost 4× the calls. With the model throttle at about 15 calls a minute, that matters more than accuracy.
- **Without the instruction, the chat route is unusable outside Hindi.** It returned the wrong script, fully or partly, for 4 of the 6 Gujarati and Bengali sentences.

So the app sends the **sentence** result. For feedback while typing, it uses a free cache-only draft instead of one call per word. That's the **hybrid** mode, and it costs one call per pause.

## Why the chat route needs an instruction

The API reference documents `POST /transliterate` with `{"text", "language", "script"}`. When this recipe was built (November 2026), that route answered `404 {"detail":"Not Found"}` for every key tried, and other contributors reported the same (issue #34).

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
| `chat` (default) | chat route with the instruction |
| `plain` | the documented `POST /transliterate` |
| `auto` | try `/transliterate` once, and fall back to `chat` for the rest of the run if it returns 404. Not the default, because that 404 took 1 to 24 seconds. |

Once `/transliterate` is live, switch to `plain`. Nothing else changes.

## Rate limits

Two separate limits showed up:

- **Per-key request quota.** Response headers `X-Ratelimit-Api_key-Limit-Requests: 120` and `...-Max_parallel_requests: 10`, with matching `-Remaining-` headers. The window isn't documented; in testing it refilled within about ten minutes. A 404 doesn't count against it.
- **Per-model throttle.** After roughly 15 calls in a minute: `429 {"type": "throttling_error", "message": "Maximum rate limit reached for the model"}` with `Retry-After` of up to about 36 seconds.

This is why hybrid drafts are cache-only. Calling the API for every finished word would hit the throttle within two or three messages of normal typing.

On a 429, the client waits and retries once, but only if `Retry-After` is 3 seconds or less. Otherwise the app shows "Try again in N s" and keeps the text in the box. The eval script, a batch job, waits out `Retry-After` and continues. A budget 429 is never retried.

## Limitations

- **Route.** It uses the chat route plus an instruction that isn't in the documentation. If the gateway changes how it handles that route, `bodhan_client.py` is the one place to fix.
- **Languages tested.** Only Hindi, Gujarati, Bengali and Tamil are tested end to end. The others are in the menu, marked "(untested)".
- **Small eval.** 12 sentences, 3 per language. References allow common spelling variants (हूँ / हूं, ઑ / ઓ), but this is a sanity check, not a benchmark.
- **Variable output.** The same request doesn't always return the same text, so numbers vary a little between runs. The eval caches its first answer for each request so the scores are reproducible.
- **Message length.** Messages are capped at 500 characters, since this is a chat box. Long text is outside its scope.
- **Cache lifetime.** The word cache lives in memory and is cleared when the server restarts.
- **Local only.** The server binds to `127.0.0.1` and has no auth. Put it behind your own backend before exposing it.
