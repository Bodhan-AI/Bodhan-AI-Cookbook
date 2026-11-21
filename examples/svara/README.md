# Svara: Multilingual AAC Speech Assistant

> An Indian-language Augmentative and Alternative Communication (AAC) assistant with code-mixed speech, voice personalisation, and zero-latency audio caching.

**Bodhan models:** indic-speak  
**Languages shown:** Hindi, English, Hindi-English code-mixed (extensible to 22 Indian languages)  
**Format:** Streamlit web application and Jupyter notebook  

## What it does

Non-verbal individuals—including people living with cerebral palsy, autism, post-stroke aphasia, or laryngectomy—often rely on AAC applications to communicate. Most existing AAC systems speak in robotic, Western-accented voices that cannot naturally pronounce bilingual Indian speech or reflect the user's cultural identity.

**Svara** bridges this gap using Bodhan's `indic-speak` model. It offers:
1. **Natural Code-Mixed Speech:** Vocalises bilingual sentences (like *"Mujhe thoda water chahiye, please"*) in a single fluent voice without unnatural language-switching boundaries.
2. **Voice Personalisation:** Allows users to pick an acoustic identity that matches their gender and personality (e.g. `Kavya`, `Amit`, `Suhani` in Hindi; `Deepika`, `Adarsh` in Kannada; `Anitha`, `Arun` in Tamil).
3. **Zero-Latency Audio Caching:** Pre-generates and caches daily communication phrases locally on disk, ensuring instant (< 1 ms) playback and offline availability during conversations.
4. **Emotional Expression:** Gives users explicit control over tone presets (`happy`, `sad`, `anger`) rather than having an AI infer or impose emotional intent.

## Setup

```bash
cd examples/svara
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and configure your API key from [console.bodhan.ai](https://console.bodhan.ai):

```bash
BODHAN_API_KEY=your_indic_speak_key_here
BODHAN_BASE_URL=https://api.bodhan.ai
```

## Run

### 1. Interactive Streamlit AAC App

Launch the full touch-accessible communication board with quick phrase tiles and live sentence composer:

```bash
streamlit run app.py
```

### 2. Guided Tutorial Notebook

Step through the code-mixed synthesis, voice exploration, and caching latency benchmark:

```bash
jupyter notebook svara.ipynb
```

## How it works

1. **Board Configuration:** Default categories (Emergency, Daily Needs, Social, Feelings) are loaded from `sample_data/sample_phrases.json`.
2. **Deterministic Cache Keying:** Phrases are mapped to a SHA-256 hash incorporating the phrase text, language code, voice, and speaking style.
3. **Local Cache Lookup:** If a phrase was previously synthesized, `SvaraClient` streams the cached WAV directly from disk with **0 ms** network latency.
4. **Live Synthesis via Indic-Speak:** If un-cached, Svara makes a request to `POST /v1/audio/speech` with instructions `{"lang": "...", "style": "..."}`.
5. **Resilient Rate-Limit Backoff:** Automatically handles HTTP 429 errors using exponential backoff to ensure reliable communication in mobile/bandwidth-constrained environments.

## Key Design Choices & Trade-offs

- **Pre-computed caching vs. Live API:** While live TTS allows arbitrary sentence composition, conversation requires instant response (< 200 ms). Svara's dual approach guarantees zero latency for high-frequency phrases while preserving the freedom to type novel sentences.
- **Explicit tone vs. Automated emotion detection:** Sentiment analysis models frequently misinterpret urgency or distress in disability contexts. In Svara, tone selection is strictly user-directed to protect speaker agency.
- **Pure Client/Local Architecture:** Does not require a heavy LLM or secondary cloud database, reducing operating costs and maximizing privacy for personal health communication.

## Limitations

- First-time live requests take ~1.2s to 2.5s depending on network connectivity before the phrase is cached.
- Long paragraphs should be segmented into short sentences (under 30 seconds of speech) to comply with Bodhan API audio limits.
