# Svara: Two-Way Intelligent AAC Speech Assistant

> An Indian-language Augmentative and Alternative Communication (AAC) platform featuring a Two-Way conversational loop, telegraphic concept expansion, voice personalisation, and zero-latency audio caching.

**Bodhan models:** indic-speak, indic-transcribe  
**Languages shown:** Hindi, Bengali, Marathi, Tamil, Telugu, Kannada, Gujarati, and English code-mixed  
**Format:** Streamlit web application and Jupyter notebook  

## What it does

Non-verbal individuals—including people living with cerebral palsy, autism, post-stroke aphasia, or laryngectomy—face severe communication barriers in daily healthcare and social environments. Most existing AAC systems are one-way soundboards that speak in robotic Western accents and cannot process natural bilingual Indian speech.

**Svara** solves this end-to-end through three interconnected capabilities:

1. **Two-Way Conversational Loop:** Caregivers or doctors speak aloud. Svara transcribes their speech with Bodhan's `indic-transcribe`, reasons over the context, and dynamically surfaces 3–4 instant 1-tap reply options.
2. **Telegraphic Concept Builder:** Non-verbal users with motor fatigue do not need to type full sentences. They tap 2–3 conceptual tokens (e.g. `[Water]` + `[Cold]` + `[Want]`), and Svara's reasoning engine reconstructs a fluent, natural code-mixed sentence (*"Mujhe thoda cold water chahiye, please"*).
3. **Voice Personalisation & Zero-Latency Playback:** Vocalises speech in the user's chosen identity (`Kavya`, `Amit`, `Suhani` in Hindi; `Ishita` in Bengali; `Anitha` in Tamil) with deterministic SHA-256 local audio caching that drops playback latency to **0 ms** (< 1 ms disk read).

## Setup

```bash
cd examples/svara
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure your virtual keys in `.env` from [console.bodhan.ai](https://console.bodhan.ai):

```bash
# Bodhan Virtual Keys (Required)
BODHAN_API_KEY=your_indic_speak_key_here
BODHAN_TRANSCRIBE_KEY=your_indic_transcribe_key_here
BODHAN_BASE_URL=https://api.bodhan.ai

# Companion Model for Contextual Reasoning (Optional, free from console.groq.com)
GROQ_API_KEY=your_groq_key_here
```

## Run

### 1. Interactive Streamlit Web Application

Launch the touch-friendly, multi-tab communication board:

```bash
streamlit run app.py
```

Features included:
* **🎙️ Two-Way Assistant:** Live microphone recording of caregiver speech $\rightarrow$ Bodhan ASR $\rightarrow$ AI contextual reply chips $\rightarrow$ Bodhan TTS.
* **🧩 Concept Builder:** Visual grid of concept chips with live natural sentence expansion.
* **📋 Multilingual Board:** Localized quick-fire cards across 7 Indian languages with 0 ms cached playback.

### 2. Guided Tutorial Notebook

Walk through each pipeline component step-by-step:

```bash
jupyter notebook svara.ipynb
```

## How it works

```
[ Two-Way Conversational Loop ]
Doctor / Caregiver Speaks  ──>  Bodhan indic-transcribe (ASR)  ──>  Contextual Intent Reasoning
                                                                            │
                                                                            ▼
[ Non-Verbal Interaction ]                                          1-Tap Reply Options
Telegraphic Concept Tokens ──>  Intelligent Grammar Expander  ──>   [Paani chahiye]
[Water] + [Cold] + [Want]       "Thoda cold water chahiye"                  │
                                                                            ▼
                                                                Bodhan indic-speak (TTS)
                                                                (Personalized voice & 0ms cache)
```

1. **ASR (Hearing):** Incoming caregiver audio is sent to `POST /v1/audio/transcriptions` via `indic-transcribe` to extract the exact spoken words.
2. **Contextual Reasoning (Thinking):** The transcript is analyzed to generate concise, natural response chips. If an open-source companion model (`qwen/qwen3.8-27b` via Groq) is configured, it reasons over open-ended nuance in under 200 ms; otherwise, it falls back to a deterministic semantic slot matcher.
3. **Concept Expansion:** Motor-impaired users tap 2–3 telegraphic concepts, which are expanded into grammatically fluent bilingual speech.
4. **Deterministic Audio Caching (Speed):** High-frequency sentences are keyed with SHA-256 hashes of `(text, voice, lang, style)`. Cached requests bypass network roundtrips entirely.
5. **Speech Synthesis (Voice):** Un-cached sentences are vocalized via `POST /v1/audio/speech` using `indic-speak` with user-selected speaking styles (`happy`, `sad`, `anger`).

## Key Design Choices & Trade-offs

- **Two-Way Loop vs. One-Way Soundboard:** Real assistive communication requires dialogue. Hearing the doctor's question and recommending instant answers cuts communication time from minutes to seconds.
- **Hybrid Reasoning Architecture:** Safety and sub-millisecond responsiveness are prioritized by combining deterministic slot grammar with an optional companion LLM hook, preventing hallucinations while supporting open-ended vocabulary.
- **Deterministic Disk Caching:** Dropping latency from ~1,800 ms to < 1 ms is essential for assistive communication where conversational delays create severe cognitive and social strain.

## Limitations

- First-time live speech requests require network connectivity and take ~1.2s to 2.2s before being cached.
- Long narrative text should be split into individual sentences (under 30 seconds of speech) to observe Bodhan API audio constraints.
