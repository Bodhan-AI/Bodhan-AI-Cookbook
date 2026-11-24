# Scheme Saathi: government scheme summariser

> Turn a dense government scheme notice into a scheme card in the reader's own language. The card is read aloud, can be shared on WhatsApp, and can answer questions asked by voice.

**Bodhan models:** indic-ocr, indic-translate, indic-transliterate, indic-speak, indic-transcribe
**Languages shown:** English notice; Hindi, Tamil, Bengali and Marathi cards; Hindi voice questions (any of 21 Indian languages in the app)
**Format:** notebook walkthrough + Streamlit app + CLI

## What it does

Scholarship, pension, crop-insurance and housing schemes are announced in dense PDFs, usually in English or formal Hindi. The people they are meant for often read neither comfortably. CSC operators, NGO volunteers and family members end up explaining the same notice line by line.

Scheme Saathi reads the notice, a PDF or a phone photo of a printed page, and builds a **scheme card** with eight sections: *About, Who can apply, Who cannot apply, What you get, Documents needed, How to apply, Important dates, Help and contact*. It translates the card into the reader's language, reads each section aloud, and makes a roman-script copy for WhatsApp. A citizen can then ask "मुझे कौन से दस्तावेज़ चाहिए?" into the mic and hear the matching lines of the notice back in Hindi.

**It uses Bodhan models only, with no general-purpose LLM.** Sections come from the notice's own headings, and answers are lines selected from the notice. Nothing is paraphrased, so a model can't alter an income limit or invent a deadline. If the notice doesn't cover something, the card says *"Not stated in the document"* and the Q&A says the notice doesn't answer it.

**Sample output** from the synthetic notice, live against `api.bodhan.ai`:

```
## आवश्यक दस्तावेज़                          (Documents needed)
- आवेदक का आधार कार्ड।
- भूमि रिकॉर्ड (खसरा/खतौनी) या पंजीकृत पट्टा समझौता।
- आईएफएससी कोड के साथ बैंक पासबुक का पहला पृष्ठ।
  …
## महत्वपूर्ण तिथियां                        (Important dates)
- आवेदन करने की अंतिम तिथि: 31 दिसंबर 2026।
```

| Asked (typed in roman script or spoken in Hindi) | Heard as | Answer, from the notice |
|---|---|---|
| *kya kirayedar kisan bhi apply kar sakta hai?* | Can a tenant farmer also apply? | किरायेदार किसान कम से कम 3 साल के पंजीकृत पट्टा समझौते के साथ आवेदन कर सकते हैं। |
| *main sarkari naukri se retired hoon, kya main apply kar sakta hoon?* | I am retired from government service, can I apply? | *Who cannot apply:* आयकरदाता और कार्यरत या सेवानिवृत्त सरकारी कर्मचारी। |
| *aakhri tareekh kya hai?* | What is the deadline? | *Important dates:* … आवेदन करने की अंतिम तिथि: 31 दिसंबर 2026। … |
| *kya isme loan bhi milta hai?* | Is there a loan available as well? | नोटिस इस प्रश्न का उत्तर नहीं देता है। (The notice doesn't answer this.) |
| 🎙️ इस योजना के लिए मुझे कौन कौन से दस्तावेज़ चाहिए? | What documents do I need for this plan? | All six documents, read aloud |

The WhatsApp copy, in roman script: *Kaun aavedan nahi kar sakta • Aay kar bharne wale aur sewa ya sevanivritt sarkari karmachari.*

## Setup

```bash
cd examples/government_scheme_summariser
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # then paste your key(s) from https://console.bodhan.ai
python make_sample_notice.py   # writes a synthetic 2-page notice to sample_data/
```

**Keys.** The console issues each model to one key. If you have one key covering all five models, set `BODHAN_API_KEY`. If you have a key per model, set `BODHAN_OCR_API_KEY`, `BODHAN_TRANSLATE_API_KEY`, `BODHAN_TRANSLITERATE_API_KEY`, `BODHAN_SPEAK_API_KEY` and `BODHAN_TRANSCRIBE_API_KEY`. Any of these left unset falls back to `BODHAN_API_KEY`. The app tells you which model is missing a key.

The sample is a made-up scheme ("Gramin Solar Pump Sahayata Yojana", Government of Sampleland). Every page is marked as synthetic, and it has a subsidy table, eligibility and exclusion rules, a document list, dates and a helpline. No real documents or personal data are used.

## Run

**App:** upload a notice (or use the sample), pick a language, listen, and ask by mic or by typing.

```bash
streamlit run app.py
```

**Notebook:** the step-by-step walkthrough of each API call.

```bash
jupyter notebook government_scheme_summariser.ipynb
```

**CLI:** writes `outputs/card_<lang>.md`, `card_<lang>_whatsapp.txt` and `card_<lang>.wav`.

```bash
python scheme_summariser.py sample_data/sample_scheme_notice.pdf --lang hi ta bn mr
```

**Tests:** offline, with no key or network needed.

```bash
pytest tests/
```

## How it works

```
notice.pdf ──indic-ocr──▶ layout blocks ──headings──▶ scheme card (verbatim lines)
                                                            │
                           indic-translate, one call/section│
             ┌──────────────────────────────────────────────┤
             ▼                                              ▼
   card in Hindi ──indic-speak──▶ audio per section    roman-script copy (script="roman") for WhatsApp

 spoken question ──indic-transcribe──┐
 typed "kaun se documents chahiye" ──indic-transliterate──┴──▶ indic-translate (→ en) ──▶ match card lines ──▶ translate + speak
```

1. **Read.** PDFs are rasterised at 150 DPI with PyMuPDF, and photos are downscaled to at most 2200 px. Each page goes to `indic-ocr` (`POST /v1/chat/completions`). Results are cached by image hash in `outputs/cache/`, since OCR is billed per page.
2. **Rebuild the layout.** The OCR Markdown prints section headings as plain lines, but the response's layout `blocks` label them `SectionHeader`. `ocr_to_markdown()` rebuilds the text from the blocks: it marks headings, re-joins lines the page wrapped mid-sentence (which also repairs a hyphenated email address), keeps a watermark repeated on every page only once, and sets signatures and footnotes aside.
3. **Structure.** `build_card()` splits the text on headings and maps each heading onto a card field by keyword. The order matters: "Who is *not eligible*" must be matched before "eligib". For a Hindi or Tamil notice, only the headings are translated to English for matching. Table rows are kept as `3 HP — Rs. 1,50,000 — Rs. 90,000`. The scheme name is taken from the lines above the first section. Notices with no headings still get dates and contacts, from lines that carry a deadline, phone number, email or URL.
4. **Translate.** `localise_card()` sends each card section to `indic-translate` as one request, with its lines joined by newlines. The model keeps one output line per input line, and `translate_lines()` checks this and falls back to line-by-line if it ever doesn't. That is about 10 calls per language instead of 40.
5. **Roman copy.** The WhatsApp copy comes from `indic-translate` with `script: "roman"`, straight from the source in one step. Many people type that way, and it reads on phones without Indic fonts.
6. **Speak.** `speak_card()` makes one clip per section with `indic-speak` (`POST /v1/audio/speech`, `Customer Care` style). Text is split on sentence ends (`.` and `।`) and then commas, so each request stays at a sentence or two. The 24 kHz WAV clips are joined with short pauses.
7. **Ask.** `ask()` transcribes a mic recording with `indic-transcribe` (`POST /v1/audio/transcriptions`). A question typed in roman script is converted to native script with `indic-transliterate` (`script: "native"`). The question is translated to English and routed to a card field by question hints ("which documents" → *Documents needed*). The answer is that field's lines that best match the question: "Can a tenant farmer apply?" returns just the tenant line, and "which documents" returns the whole list. A strong match in another section wins: "I'm a retired government employee, can I apply?" returns the exclusion line. The answer is translated back and spoken. If nothing matches, it says so instead of guessing.

Every Bodhan call goes through the small `Bodhan` client in `scheme_summariser.py`:

- **Keys.** It reads a per-model key or `BODHAN_API_KEY`.
- **Pacing.** It paces requests per key from the `x-ratelimit-*-limit-requests` header, since each key allows about 120 a minute.
- **Budget.** A `429` with `type: budget_exceeded` means the key's spending budget is used up. Waiting won't help, so it stops at once and names the key to top up. The app shows this as a plain message.
- **Rate limits.** It tells two other kinds of `429` apart. `rate_limit_exceeded_error` means the key spent its budget, so the client waits one window and paces lower from then on. "Maximum rate limit reached for the model" is the model's shared capacity being busy, so it backs off 5, 15, 30, then 60 s and gives up after that rather than loop.
- **Errors.** It retries once on a dropped connection, surfaces the API's JSON errors with their `request_id`, and warns when a translation is `truncated`. Long batches get an explicit `max_output_tokens`, because the server's default output budget can cut roman-script batches short.
- **Cache.** With `cache_dir` set, as the app, notebook and CLI do (`outputs/cache/`), translations and speech are stored on disk, keyed by their exact request. A second run, or a live demo, is instant and free. Spoken questions are never cached.
- **Transport.** Translation and transliteration go to the documented `/translate` and `/transliterate` routes first. If the gateway answers 404, as it did while this recipe was built, the client switches to the equivalent chat-completions transport (`POST /v1/chat/completions` with `model`, `target_language`, `script`), which the API reference documents as producing identical output.

## Files

| File | What it is |
|---|---|
| `scheme_summariser.py` | The pipeline: Bodhan client, OCR, card builder, translation, speech, Q&A, and a CLI |
| `app.py` | Streamlit app: upload, card with per-section audio, mic questions, downloads |
| `government_scheme_summariser.ipynb` | Walkthrough notebook, one step per cell |
| `make_sample_notice.py` | Generates the synthetic sample notice (PDF + PNG) |
| `tests/test_scheme_summariser.py` | 45 offline tests: layout-block parsing, section mapping, Q&A ranking, batching, per-model keys, both kinds of 429, 404 fallback, cache, errors |

## Limitations

- **Heading-driven.** Notices with clear section headings work best. A wall of unstructured text still gets dates and contacts through the fallbacks, but other sections may show as "not stated". That is the honest result, though a less useful one.
- **Extractive answers.** Q&A returns the notice's own lines, not a conversational reply. It can't combine conditions ("I lease 4 acres and earn ₹2.5 lakh, am I eligible?"). It shows the eligibility lines and leaves the judgement to the reader or operator.
- **Keyword routing is English.** Questions in any language are translated to English before matching, so an unusual phrasing that translates loosely can miss. Typing the question, or using the card's section audio, are the fallbacks.
- **Length limits.** Mic questions should be under ~30 seconds. The app reads at most 10 pages per notice to keep OCR cost predictable, which you can change with `max_pages` in `document_to_pages`.
- **Roman copy wording.** The roman-script card is its own translation (`script: "roman"`), so its wording can differ slightly from the native-script card. The facts are the same, because both come from the same source lines.
- **Cost and pace.** One run on the 2-page sample uses 2 OCR pages and about 20 translation calls per language (native and roman). Translating a card takes about a minute. Reading it aloud is about 20 speech calls, roughly 3 minutes for about 3 minutes of audio, which is why the app's audio is opt-in. When the shared model is busy, back-offs can add to that. Re-runs are served from `outputs/cache/`.
- **Not legal advice.** The card is a reading aid. Check the original notice and the scheme office before applying.
