# Text to speech

Generate natural speech in 22 Indian languages plus English from 45 voices, with speaking styles and a recipe for joining longer text.

| | |
|---|---|
| Model | `indic-speak` |
| Endpoint | `POST /v1/audio/speech` |
| Notebook | [text_to_speech.ipynb](text_to_speech.ipynb) |

## Setup

```bash
pip install -r requirements.txt
export BODHAN_API_KEY=<your key from https://console.bodhan.ai>
```

## Run

```bash
jupyter notebook text_to_speech.ipynb
```

Reference: [console.bodhan.ai/api-docs](https://console.bodhan.ai/api-docs/)
