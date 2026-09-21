# Speech to text

Transcribe short audio clips in 25 Indian languages plus English, with a language hint and a chunking recipe for longer recordings.

| | |
|---|---|
| Model | `indic-transcribe` |
| Endpoint | `POST /v1/audio/transcriptions` |
| Notebook | [speech_to_text.ipynb](speech_to_text.ipynb) |

## Setup

```bash
pip install -r requirements.txt
export BODHAN_API_KEY=<your key from https://console.bodhan.ai>
```

## Run

```bash
jupyter notebook speech_to_text.ipynb
```

Reference: [console.bodhan.ai/api-docs](https://console.bodhan.ai/api-docs/)
