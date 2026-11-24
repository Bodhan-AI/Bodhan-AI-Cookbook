# PestSignal

PestSignal is a multilingual crop-reporting app that helps farmers submit crop symptoms in their native language and enables NGOs to monitor emerging regional agricultural signals. It also helps farmers identify a pest which which they haven't encountered before.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak

**Languages shown:** English, Tamil, Telugu, Hindi

**Format:** FastAPI app

## What it does

PestSignal lets farmers report crop symptoms using audio or text in English, Tamil, Telugu, or Hindi. Audio is transcribed using Bodhan's `indic-transcribe` model and translated into English using `indic-translate`; an LLM then extracts structured report fields such as crop, symptoms, suspected disease, and location. Reports are saved and searchable, while a regional signal dashboard flags clusters of reports from the same region for NGO follow-up. These signals are intended for monitoring and investigation—not confirmed diagnoses.

## How it works

1. The farmer submits a typed report or audio recording in a supported language.
2. Audio is transcribed with Bodhan `indic-transcribe`.
3. The original-language text is translated into English using Bodhan `indic-translate`.
4. The Groq-powered extraction step parses the English text into structured report fields.
5. The report is saved in the local SQLite database and displayed in the report dashboard.
6. Reports can be searched and filtered by crop, region, or symptoms.
7. Three or more reports from the same region trigger a regional agricultural signal, regardless of crop or symptom wording.
8. NGOs can review regional signals and update their status (Pending, Under review, or Investigated).
9. Bodhan `indic-speak` can be used to play text in supported languages.

## Screenshots

### Farmer Reporting Interface
![PestSignal](screenshots/Screenshot%202026-11-24%20152320.png)

### Crop Symptom Translation
![PestSignal](screenshots/Screenshot%202026-11-24%20152338.png)

### Data Extraction
![PestSignal](screenshots/Screenshot%202026-11-24%20152354.png)

### Pest Filtering
![PestSignal](screenshots/Screenshot%202026-11-24%20152417.png)

### Dashboard Entries
![PestSignal](screenshots/Screenshot%202026-11-24%20152435.png)

### NGO Viewing Dashboard
![PestSignal](screenshots/Screenshot%202026-11-24%20152457.png)

## Setup

### Windows (PowerShell)

```powershell
cd examples/pest_signal
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Open `.env` and add your API credentials from [Bodhan](https://console.bodhan.ai) and your Groq API key, as required by the configuration.

### Linux/macOS

```bash
cd examples/pest_signal
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run

```bash
python -m uvicorn main:app --reload
```

Open the local URL printed by Uvicorn (typically `http://127.0.0.1:8000`) in your browser.

## Limitations

- The audio uploaded can be only of the format .wav and a maximum of 30 second.
- Sometimes during translation or transcribing or speak the words change in spelling. Eg: Ooty to UT.
- Costs incurred - speak: Rs. 1.08, translate: Rs. 0.07, transcribe: Rs. 0.74


## AI assistance

I used ChatGPT for code assistance during coding and debugging.

---
