# Voice notice translator

> Record an announcement once, get it back as text and audio in five languages.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak
**Languages shown:** Hindi in; English, Tamil, Bengali, Telugu, Marathi out
**Format:** notebook

## What it does

Schools, housing societies and small offices announce the same thing to audiences who speak different languages. This recipe takes a short spoken notice, transcribes it, translates it, and reads each translation aloud in a native voice with a news-style delivery. The output is a WAV per language, ready for a PA system, a WhatsApp broadcast, or a web page.

## Setup

```bash
cd examples/voice_notice_translator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # paste your key from https://console.bodhan.ai
```

## Run

```bash
jupyter notebook voice_notice_translator.ipynb
```

The notebook uses the repo's `sample_data/sample_hindi.wav`. To use your own recording, put a WAV or MP3 of up to ~30 seconds in `sample_data/` and change `SOURCE` and `SOURCE_LANG` in step 1.

## How it works

1. `POST /v1/audio/transcriptions` with `indic-transcribe` turns the recording into text.
2. `POST /translate` produces one translation per target language.
3. `POST /v1/audio/speech` with `indic-speak` renders each translation with a matching voice and the `AIR style news` style.

## Limitations

- One request handles about 30 seconds of audio in and one or two sentences out. Chunk longer notices (the speech-to-text and text-to-speech tutorials show how).
- Voices are per language; see `scripts/bodhan_api_rules.json` for the full list.
