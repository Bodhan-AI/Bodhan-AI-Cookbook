# Bodhan Reader — Indic Multimodal Document Reader & Audiobook Synthesizer

A complete, end-to-end multimodal Streamlit application demonstrating how to pipeline Bodhan AI's core Indian-language models into an accessible document reader and audiobook generator.

## Overview

Bodhan Reader solves a fundamental accessibility challenge: enabling users to read, comprehend, and listen to physical books, PDFs, and signs in their mother tongue. 

The application pipelines four core Bodhan AI models:
- `indic-ocr`: Document layout block parsing (`Chapter-title`, `Section-title`, `Paragraph`, `Table`) with bounding box extraction.
- `indic-translate`: neural translation across 22+ Indian languages.
- `indic-transliterate`: Phonetic Roman script generation for non-native readers.
- `indic-speak`: Continuous multi-chunk WAV synthesis that stitches sentence frames to bypass the 30-second server limit for full audiobook generation.

### Dual-Input Book Mode (PDFs & Physical Photography)
The Book Reader module accepts both **digital multi-page PDFs** and **physical book photography / camera scans**:
- **Digital PDFs:** Ingests e-books, academic notes, and PDF documents. If the PDF contains embedded digital text, it extracts it cleanly; if the PDF consists of scanned pages, it automatically invokes `indic-ocr` for layout aware parsing.
- **Physical Page Images & Camera Scans:** Readers can take photos of real physical book pages using their phone or webcam. Each photo is parsed for paragraphs and chapter titles, translated into the user's preferred Indian language, and stitched into a seamless audiobook.


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
   - **Book Mode (`pages/3_Book_Mode.py`):** Ingest both digital multi-page PDF documents and physical photographic page scans. The app automatically extracts text, translates across chapters into your preferred Indian language, and synthesizes continuous, full-length audiobooks with background processing.

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

Bodhan Reader supports 22+ official Indian languages with complete native localization and voice synthesis:

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
| `as` | Assamese | অসমীয়া | Ananya |
| `ur` | Urdu | اُردُو | Kavya |
| `ne` | Nepali | नेपाली | Kavya |
| `sa` | Sanskrit | संस्कृतम् | Kavya |
| `kok` | Konkani | कोंकणी | Ananya |
| `mai` | Maithili | मैथिली | Kavya |
| `doi` | Dogri | डोगरी | Kavya |
| `sd` | Sindhi | सिन्धी | Kavya |
| `ks` | Kashmiri | كٲشُر | Kavya |
| `brx` | Bodo | बड़ो | Kavya |
| `mni` | Manipuri | ꯃꯤꯇꯩꯂꯣꯟ | Tanusree |
| `sat` | Santali | ᱥᱟᱱᱛᱟᱲᱤ | Kavya |
| `en` | English | English | Kavya |

---

## Accessibility & Social Impact

Bodhan Reader is engineered as an assistive tool built for:

1. **Specially Abled & Visually Impaired Individuals:**
   - People who cannot see or read printed text can listen to any physical book, document, or sign through continuous audio synthesis without hitting time limits.
2. **Elderly & Low-Vision Readers:**
   - Older adults and individuals who suffer from eye strain or weak eyesight no longer need to struggle with tiny text, they can simply turn any page into an easy-to-listen audiobook.
3. **People Who Cannot Read (Non-Literate):**
   - Users who cannot read written script can simply take a photo of any document, medicine prescription, government notice, or textbook and immediately hear it spoken aloud in their native tongue.
4. **Readers Lacking Books in Their Native Language:**
   - Millions of Indian readers love literature and learning, but thousands of textbooks and classic books are never published in their mother tongue. Bodhan Reader automatically bridges this gap by translating any book or PDF into 22+ Indian languages with authentic speech and script.

---

## AI Assistance

Used Gemini for code scaffolding, design UI, and i18n dictionary structuring.
