# Multilingual feedback analyser

> Turns a mixed inbox of customer feedback (typed messages, voice notes and photographed comment cards, in any Indian language or script) into one report an English-reading owner can act on, and drafts a checked reply to every complaint in the customer's own language.

**Bodhan models:** `indic-transcribe`, `indic-ocr`, `indic-translate`, `indic-transliterate`, `indic-speak`
**Languages shown:** Hindi, Tamil, Bengali, Marathi, Telugu, Gujarati, Kannada, Malayalam, Urdu, English, and roman-script Hindi and Tamil
**Format:** command-line tool that writes an HTML report, JSON and CSV

## What it does

A shop, clinic or delivery kitchen in India hears from customers in several languages at once, and not only as typed text: people send voice notes, write "delivery bahut late thi" in roman letters, and fill in paper comment cards. The person who has to act on it usually reads one or two of those languages.

You give the tool a CSV that lists the inbox. It gives you:

- `outputs/report.html`: totals, which themes draw complaints, sentiment by language, and every item worst-first with the original text, its English translation, sentiment and themes.
- A suggested reply for each complaint, written in the customer's language and script. Each reply is translated back to English and compared with what was meant; replies that drift are marked **needs human review** instead of **ready to send**.
- A spoken version of the reply (`outputs/replies/<id>.wav`) for customers who sent a voice note.
- `outputs/report.json` and `outputs/report.csv` with the same data for a spreadsheet or CRM.

Nothing is sent to customers. The tool drafts; a person sends.

## Setup

You need Python 3.10 or newer.

```bash
cd examples/multilingual_feedback_analyser
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # then paste your keys from https://console.bodhan.ai
```

The console issues one key per model, so this recipe needs five: create a key for each model listed above and paste it next to the matching name in `.env`. If you have a key that covers several models, put it in `BODHAN_API_KEY` and leave the others empty.

## Run

```bash
python -m feedback_analyser run sample_data/inbox.csv
```

Open `outputs/report.html` in a browser. The sample inbox has 26 synthetic items: 20 typed messages, 3 voice notes and 3 comment cards.

Then score the run against the hand-labelled answers:

```bash
python -m feedback_analyser evaluate outputs/report.json sample_data/expected.csv
```

Useful options for `run`:

| Option | Effect |
|---|---|
| `--dry-run` | Count what would be sent to Bodhan, then stop. Makes no API calls. |
| `--speak voice\|all\|none` | Which replies to voice. Default `voice`: only customers who sent a voice note. |
| `--roman-default hi` | Language assumed for roman-script text when the row has no `language` value. |
| `--analyser llm` | Use a companion LLM for sentiment and themes instead of the built-in lexicon. Needs the `LLM_*` variables in `.env`. |
| `--workers 4` | Items processed in parallel. Keep it under your key's parallel-request limit. |
| `--no-cache` | Call Bodhan again for inputs already seen. |
| `--keep-pii` | Do not mask phone numbers and email addresses in the report. |

The unit tests need no key and no network:

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

## Use your own inbox

The manifest is a CSV with five columns. File paths are relative to the CSV.

```csv
id,channel,text,file,language
m1,text,खाना ठंडा था और डिलीवरी देर से आई।,,
m2,text,"app romba slow ah irukku",,ta
m3,voice,,voice/note_17.wav,hi
m4,card,,cards/card_03.jpg,
```

- `channel` is `text`, `voice` (a WAV file) or `card` (a PNG or JPEG photo of one card).
- `language` is optional. Fill it in when you know it: it is the only way to tell roman-script Tamil from roman-script Hindi, or Marathi from Hindi in a short message.

Themes, reply wording and domain vocabulary live in [`taxonomy.json`](taxonomy.json), not in code. Each theme has `keywords` (whole words; end one with `*` to match a prefix, so `deliver*` matches "delivered") and the English `reply` that is translated for the customer. `sentiment_words` adds words that are neutral in everyday English but not in your business ("late", "stale", "cold" for a food shop).

## How it works

```
inbox.csv ─┬─ text ─────────────────────────────┐
           ├─ voice ── indic-transcribe ────────┤
           └─ card ─── indic-ocr ───────────────┤
                                                ▼
                         script detection (Unicode ranges, local)
                                                ▼
                         indic-translate → English ("pivot" text)
                                                ▼
                         sentiment + themes per clause (local)
                                                ▼
                     complaint? ── no ──► report
                         │ yes
                         ▼
     reply template (English) ── indic-translate ──► reply in customer's language
                         ▼
     indic-translate back to English ── compare ──► ready to send / needs review
                         ▼
     customer wrote in roman script? ── indic-transliterate ──► roman-script reply
     customer sent a voice note?     ── indic-speak ──────────► replies/<id>.wav
```

1. **Read each item as text.** Typed text is used as is. A voice note goes to `indic-transcribe`; a WAV longer than 28 seconds is cut into 25-second pieces first, because the API takes about 30 seconds per request. A card image goes to `indic-ocr`, and the Markdown it returns is flattened to plain text. If the average confidence of the OCR layout blocks is under 0.6 the item is flagged for a look by eye.
2. **Work out the language.** The script is found locally by counting characters in each Unicode block, so "खाना" is Devanagari and "khana" is Latin. A `language` value in the CSV always wins. Without one, the script decides (Tamil script means Tamil), with two small marker rules for scripts shared by several languages (ळ or आहे means Marathi, ৰ or ৱ means Assamese). For Latin text the tool translates to English and compares: if the translation is nearly the same words, the text was already English; if not, it is roman-script Indic text and gets the `--roman-default` language plus a flag.
3. **Translate to English** with `indic-translate`. English is the pivot: every later step works on one language instead of twenty-two.
4. **Analyse the English.** The text is split into clauses at sentence ends and at "but / however / although". Each clause gets a sentiment score from the VADER lexicon, extended with the words in `taxonomy.json`, and is matched against each theme's keywords. A theme's sentiment is the average of the clauses that mention it, so "the food was delicious but the delivery was late" becomes quality positive, delivery negative, rather than one blurred score.
5. **Draft a reply** when the overall sentiment or any theme is negative. The reply is the English template of the worst-scoring theme, translated into the customer's language.
6. **Check the reply.** The translation is translated back to English and compared with the template by word overlap (F1 over content words, with contractions such as "we're" expanded first). Under 0.5, or if the reply is not in the script that language uses, it is marked for human review and is not voiced.
7. **Match the customer's script and channel.** If they wrote in roman script, the native-script reply is passed through `indic-transliterate` so they can read it. If they sent a voice note, the native-script reply is spoken with `indic-speak` in the `Customer Care` style, one request per 250 characters, and the clips are joined.
8. **Write the report.** One HTML file with inline CSS and no JavaScript, plus JSON and CSV.

Each item is processed independently on a small thread pool. If one file is unreadable or one call fails, that item is listed in the report as "not analysed" with the reason, and the rest carry on.

## Design choices and trade-offs

| Choice | Why | What it costs |
|---|---|---|
| Translate to English, then analyse | One analyser and one taxonomy for every language, and the owner reads English anyway. | Translation flattens tone: sarcasm, idiom and politeness levels can be lost before analysis sees them. |
| Lexicon and keyword analyser by default, LLM optional | Needs only a Bodhan key, costs nothing, gives the same answer every run, and every label can be traced to a word in `taxonomy.json`. | It reads words, not meaning. It misses complaints with no listed keyword and can be swayed by one positive word. See the measured results below. |
| Clause-level scoring | Real feedback is often mixed, and the owner needs to know which part was bad. | Clause splitting on "but" is crude; a long clause mentioning two themes gives both the same score. |
| Replies come from templates, not generated text | A business should not send text nobody wrote. Templates are short, reviewed once, and translate predictably. | Replies are generic. They do not refer to the customer's specific order. |
| Back-translation check before a reply is "ready" | The owner cannot read the Tamil reply. A round trip is the cheapest evidence that it says what was meant, and it catches gross failures: wrong language, cut-off output, wrong meaning. | Two extra translate calls per reply. Word overlap punishes harmless paraphrase and cannot see subtle errors such as a wrong honorific, so it is a filter, not proof. |
| One translation, then transliterate for roman-script customers | The text reply and the spoken reply come from the same native-script sentence, so they cannot disagree. Asking `indic-translate` for `script: "roman"` separately would give two independent translations. | One extra call for those customers. |
| Voice replies only for voice-note customers, by default | Answer in the channel the customer chose, and do not pay for audio nobody will play. | Change it with `--speak all`. |
| Script detection by Unicode range, done locally | Free, instant and exact for the script. | A script is not a language. Devanagari defaults to Hindi unless a marker or a `language` value says otherwise. |
| Content-addressed disk cache (`outputs/.cache/`) | Every call is billed. A re-run after editing `taxonomy.json` re-analyses for free, and a crashed run resumes where it stopped. | Stale results if the models improve; use `--no-cache`. The cache holds customer text, so it stays in the git-ignored `outputs/` folder. |
| Bounded retries | 429 and 5xx responses are retried at most three times, waiting for `retry-after` when the API sends it (capped at 30 s) and 1, 2, 4 s otherwise. Other 4xx errors are not retried because they will not succeed. | A long rate-limit window fails the item instead of blocking the run. Re-run and the cache skips what already worked. |
| `/translate` first, chat route on 404 | The API reference documents `POST /translate` and `POST /transliterate`, but on the day this was written they returned 404 ([#34](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/issues/34)) while `POST /v1/chat/completions` with the same model worked. The client tries the documented route once per run and remembers the answer. On the chat route `indic-transliterate` ignores `language` and translates to English instead; it transliterates only when sent `target_language` plus `script`, which is what the client sends. | One wasted request per run while the routes are down. |

## Measured results

One run of the 26-item sample inbox against the live API on 28 November 2026, scored with the `evaluate` command against `sample_data/expected.csv`. The expected labels were written before the first live run and have not been changed since.

| Measure | First run | After adding three words |
|---|---|---|
| Language identified | 26 / 26 | 26 / 26 |
| Sentiment (positive / neutral / negative) | 24 / 26 (92.3%) | 26 / 26 (100%) |
| Themes: precision / recall / F1 | 94.1% / 91.4% / 92.8% | unchanged |

The two sentiment misses on the first run were both polite complaints: "the bag of flour was torn, please replace it" and "the bill had an incorrect price, please check". VADER scores "please" as positive and did not know "torn" or "incorrect". I set "please" to neutral and added the other two as negative in `taxonomy.json`. The second column is therefore measured on the same items the fix was found on, so read it as "the tuning loop works", not as an accuracy estimate. Themes were not tuned. The five remaining theme misses are what a keyword list does: "payment" pulled an app complaint into pricing, "rider" pulled a compliment about a person into delivery, and "nobody answered my calls", "the food was very good" and "the bag was torn" name no keyword of the theme they belong to.

Reply check, final run: 15 replies drafted, 14 checked by back-translation (one was to an English speaker and needed no translation). Overlap scores ranged from 0.46 to 0.93, median 0.74; 13 were marked ready and 1 was sent to review. That one (Tamil, 0.46) reads as a correct reply whose back-translation was reworded, so it is a false alarm. No reply in this run was actually wrong, so the run says nothing direct about how many bad replies the check catches. As a stand-in, I compared each of the first run's 12 back-translations with the five templates it was *not* made from: 57 of 60 mismatched pairs scored under 0.5. The three that passed were between the "quality" and "wrong order" templates, which share most of their words. Expect roughly one false alarm in fourteen and weak discrimination between replies that are worded alike.

Other things the live run showed:

- Every item made it through: 3 voice notes, 3 cards and 20 texts in ten languages, with no failed calls.
- The first run made 57 requests (48 translate, 2 transliterate, 3 transcribe, 3 OCR, 1 speech) and took about 2 minutes 20 seconds with 4 workers. The re-run after editing `taxonomy.json` made 3, for the two newly drafted replies; the other 61 results came from the cache.
- `indic-translate` writes Tamil replies in a spoken, colloquial register ("உங்க ஆர்டர்" rather than "உங்கள் ஆர்டர்"). The back-translation check cannot see register. A Tamil reader should decide whether that suits the business.
- OCR returned the printed card heading ("Customer Feedback Card") as part of the feedback. It did not change any label here, but it is noise.
- The `--analyser llm` path is covered by unit tests only. It was not run against a real LLM.

## Limitations

- **The sample voice notes are synthetic speech** made with `indic-speak`. They are far cleaner than a phone recording in a market, so transcription on them is a best case. The comment cards are printed text, not handwriting.
- **Roman-script text has no reliable language signal.** Without a `language` value it is assumed to be `--roman-default`, and the report says so on each such item.
- **Languages sharing a script** are told apart only by a few marker characters and words. A short Marathi or Nepali message with no marker is read as Hindi, and the reply goes out in Hindi. Fill in `language` when the channel tells you (a regional phone line, a store location).
- **Sentiment is read from translated English with a word list.** Sarcasm, negation spread across clauses and complaints phrased as plain facts ("it came on Thursday") are missed.
- **Audio input must be WAV** for automatic splitting. Other formats are sent whole and must be under about 30 seconds.
- **Bhili and Bhojpuri** can be transcribed but `indic-translate` does not list them, so replies for those stay in English and are marked for review.
- **PII masking is basic**: phone numbers and email addresses are masked in the report. Names and addresses are not, and the unmasked text is still sent to Bodhan for translation.
- **One card per image.** A photo of several cards is read as one piece of feedback.

## Ideas for improvement

- Label which OCR blocks are printed form text and which are the customer's writing, and drop the former.
- Split voice notes on silence instead of fixed windows, so words are not cut at 25 seconds.
- Replace word overlap with a multilingual sentence-embedding similarity for the reply check.
- Track themes week over week and alert when one jumps.
- A small web form for the owner to approve, edit and send replies.

---

All sample data is synthetic. Regenerate it with `python tools/make_samples.py` (cards need Pillow and Noto Sans fonts; voice notes need a key).
