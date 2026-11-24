# Bodhan Reader — Indic Multimodal Document Reader & Audiobook Synthesizer

A complete, end-to-end multimodal Streamlit application demonstrating how to pipeline Bodhan AI's core Indian-language models into an accessible document reader and audiobook generator.

## Overview

Bodhan Reader solves a fundamental humanitarian accessibility challenge: enabling everyone to read, comprehend, and listen to physical books, documents, and signs in their mother tongue.

### Who this is built for:
- **Communities Lacking Native Books:** Millions of educational, scientific, and legal books exist only in English or Hindi. Bodhan Reader instantly bridges this gap for speakers of Kannada, Telugu, Tamil, Bengali, Marathi, and other regional languages.
- **Visually Impaired & Specially Abled:** Users who cannot see printed text can snap a photo or upload a document and listen to human-quality, natural-sounding audio narration in their native language.
- **Non-Literate Individuals:** Those who comprehend spoken language fluently but cannot read the written script can listen effortlessly.
- **Elderly Citizens:** Seniors with declining vision or eye strain can turn any physical book, prescription, or newspaper into a relaxing audiobook.

The application pipelines four core Bodhan AI models:
- `indic-ocr`: Document layout block parsing (`Chapter-title`, `Section-title`, `Paragraph`, `Table`) with bounding box extraction.
- `indic-translate`: Context-aware neural translation into 10 Indian languages.
- `indic-transliterate`: Phonetic Roman script generation for non-native readers.
- `indic-speak`: Continuous multi-chunk WAV synthesis that stitches sentence frames to bypass the 30-second server limit for full audiobook generation.

Designed with **Minimalism & Swiss Style** architecture (Light Mode, 0px sharp geometry, high contrast) and dynamic UI localization across 10 Indian languages (`i18n`).

---

## Setup

### 1. Prerequisites
- Python 3.10 or higher
- Bodhan AI API key from [console.bodhan.ai](https://console.bodhan.ai)

### 2. Installation
Clone the repository and install the pinned dependencies:

```bash
git clone https://github.com/Bodhan-AI/Bodhan-AI-Cookbook.git
cd Bodhan-AI-Cookbook/examples/indic_document_reader
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy the template environment file:

```bash
cp .env.example .env
```

Open `.env` and add your Bodhan API keys:

```ini
BODHAN_API_KEY_OCR=your_indic_ocr_key
BODHAN_API_KEY_TRANSLATE=your_indic_translate_key
BODHAN_API_KEY_TTS=your_indic_speak_key
BODHAN_API_KEY_STT=your_indic_transcribe_key
```

*(Note: Never commit your `.env` file to git. It is ignored by `.gitignore`)*.

---

## Run

Launch the Streamlit application:

```bash
streamlit run streamlit_app.py
```

The app will open automatically in your browser at `http://localhost:8501`.

1. **Step 1:** Select your preferred Indian language (e.g., Kannada, Telugu, Hindi, Tamil, Bengali). The entire system UI adapts to that language dynamically.
2. **Step 2:** Choose a module from the dashboard:
   - **Camera Reader (`pages/1_Camera_Reader.py`):** Upload or snap a document photo to view side-by-side translated layout blocks and hear full-page audio narration.
   - **Quick Translate (`pages/2_Quick_Translate.py`):** Type or paste text to get native script, Romanized phonetics, and speech playback.
   - **Book Mode (`pages/3_Book_Mode.py`):** Ingest multi-page PDFs or photographic pages, select page ranges, and generate continuous multi-page audiobooks.

---

## How it works

### 1. Document Layout Extraction (`indic-ocr`)
When an image or scanned document is submitted, it is sent to `https://api.bodhan.ai/ocr` using model `indic-ocr`. The model returns structured layout blocks (`bbox_xyxy`, `label`, `content`), distinguishing chapter headers from paragraphs and tables.

### 2. Contextual Machine Translation (`indic-translate`)
Extracted blocks are translated via `https://api.bodhan.ai/v1/chat/completions` using the `indic-translate` model. Formatting tokens (HTML/Markdown tables) are preserved during translation.

### 3. Romanized Phonetic Transliteration (`indic-transliterate`)
For non-native readers who speak the language but cannot read the script, the application calls `indic-transliterate` on `/v1/chat/completions` to produce phonetic Latin/English script (e.g. *"Namaskāra"*).

### 4. Overcoming the 30-Second Audio Limit (`indic-speak`)
Bodhan's TTS endpoint (`https://api.bodhan.ai/speak`) limits individual requests to approximately 30 seconds. To generate full audiobooks and long documents:
1. `text_to_speech_continuous()` partitions text at sentence boundaries (`।`, `.`, `?`, `!`, `\n`).
2. Each chunk is synthesized sequentially through `indic-speak`.
3. The raw audio frames are stitched seamlessly using Python's `wave` module into a single continuous WAV audio stream.

### 5. Asynchronous Background Execution
Heavy document translation and multi-page audiobook synthesis run on a non-blocking thread pool (`task_manager.py`). Users can navigate across tabs freely without interrupting long-running jobs.

---

## Supported Languages

| Code | Language | Native Name | Default Voice |
| :--- | :--- | :--- | :--- |
| `hi` | Hindi | हिन्दी | Kavya |
| `kn` | Kannada | ಕನ್ನಡ | Gagan |
| `te` | Telugu | తెలుగు | Lalitha |
| `ta` | Tamil | தமிழ் | Vasanth |
| `mr` | Marathi | मराठी | Ananya |
| `bn` | Bengali | বাংলা | Tanusree |
| `gu` | Gujarati | ગુજરાતી | Riddhi |
| `ml` | Malayalam | മലയാളം | Midhun |
| `pa` | Punjabi | ਪੰਜਾਬੀ | Gurpreet |
| `or` | Odia | ଓଡ଼ିଆ | Subhashree |
