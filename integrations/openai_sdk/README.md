# Bodhan with the OpenAI SDK

Bodhan's `/v1` routes accept OpenAI-style requests, so the official `openai` package works unchanged once you set `base_url`. This covers speech to text, text to speech and document OCR. Translation and transliteration are plain JSON endpoints and use `requests`.

## Setup

```bash
pip install openai==1.51.0 requests==2.32.3
export BODHAN_API_KEY=<your key>
```

## Run

```bash
jupyter notebook openai_sdk.ipynb
```

## What it shows

| Bodhan model | SDK call |
|---|---|
| `indic-transcribe` | `client.audio.transcriptions.create(...)` |
| `indic-speak` | `client.audio.speech.create(...)` |
| `indic-ocr` | `client.chat.completions.create(...)` with an `image_url` part |
| `indic-translate` / `indic-transliterate` | `requests.post(...)` (not OpenAI-shaped) |

The same pattern works in the Node `openai` package: `new OpenAI({ baseURL: "https://api.bodhan.ai/v1", apiKey: process.env.BODHAN_API_KEY })`.
