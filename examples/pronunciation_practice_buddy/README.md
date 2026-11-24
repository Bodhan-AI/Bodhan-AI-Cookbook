# pronunciation_practice_buddy

> Listen to a phrase in an Indian language, say it back, and get word-by-word feedback, for language learners.

**Bodhan models:** indic-speak, indic-transcribe, indic-transliterate, indic-translate
**Languages shown:** Hindi, Tamil, Bengali
**Format:** Streamlit app

## What it does

A learner picks a language and a short everyday phrase (or types an English phrase and has it translated), hears it spoken by a native voice, then records themselves repeating it in the browser. The recording is transcribed and compared with the target phrase word by word. Each word is shown green (recognised), orange (close) or red (missed), with a score out of 100 and a try-again loop. Roman script is shown under every phrase so beginners who cannot read the script can still follow along. It helps people who need to speak a new regional language in daily life, such as migrants and students.

## Setup

```bash
cd examples/pronunciation_practice_buddy
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # then paste your keys from https://console.bodhan.ai
```

Each Bodhan key is tied to one model, so create four keys (indic-speak, indic-transcribe, indic-transliterate, indic-translate) and put each in its own variable in `.env`. If you have one key that covers all four, set `BODHAN_API_KEY` instead. Keys stay on the server side of the Streamlit app and never reach the browser.

## Run

```bash
streamlit run app.py
# tests (no API key needed):
pytest tests
```

Open the local URL Streamlit prints, allow microphone access, pick a phrase and press **Listen**.

## How it works

1. `indic-speak` speaks the target phrase. **Listen carefully** uses the `educational lecture` style, the clearest style the API offers (Indic-Speak has no speed setting).
2. `indic-transliterate` shows the phrase in roman script (`script="roman"`).
3. `indic-translate` turns a typed English phrase into the practice language.

   Steps 2 and 3 call `/v1/chat/completions` because the plain `/translate` and `/transliterate` routes returned 404 when this recipe was built (see cookbook issue #34).
4. The learner's browser recording goes to `indic-transcribe` with the language code.
5. `scoring.py` aligns the transcript with the target using word-level diffing. Exact words score 1, near matches score 0.5 and missing words 0. It is plain Python with unit tests.

## Limitations

- The score reflects what the speech recogniser understood, not a phonetic analysis. A clearly said word can still be marked wrong if it is mis-recognised, and a recogniser can be forgiving of small mistakes.
- Recordings and phrases are limited to about 30 seconds per request; longer recordings are rejected with a message.
- Only Hindi, Tamil and Bengali phrases are bundled. Other languages work for typed phrases once you add a voice to `phrases.py`.
- Roman-script spelling comes from the model and may differ from the spelling you know.
- Ideas for improvement: character-level scoring, per-phoneme feedback, spaced repetition, and more languages.
