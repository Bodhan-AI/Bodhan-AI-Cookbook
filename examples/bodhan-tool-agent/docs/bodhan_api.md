## indic-translate
import requests

resp = requests.post(
    "https://api.bodhan.ai/v1/chat/completions",
    headers={"Authorization": "Bearer your_bodhan_key"},
    json={
        "model": "indic-translate",
        "messages": [{"role": "user", "content": "The meeting has been postponed."}],
        "target_language_code": "kn",
        "target_script": "native"
    },
)

print(resp.json()["choices"][0]["message"]["content"])


## indic-transcribe
import requests

resp = requests.post(
    "https://api.bodhan.ai/v1/audio/transcriptions",
    headers={"Authorization": "Bearer your_bodhan_key"},
    files={"file": open("sample_hindi.wav", "rb")},
    data={"model": "indic-transcribe", "language": "hi", "vad": True},
)

print(resp.json()["text"])


## indic-ocr
import base64, requests

b64 = base64.b64encode(open("block.png", "rb").read()).decode()

resp = requests.post(
    "https://api.bodhan.ai/v1/chat/completions",
    headers={"Authorization": "Bearer your_bodhan_key"},
    json={
        "model": "indic-ocr",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}
        ]}]
    },
)

print(resp.json()["choices"][0]["message"]["content"])


## indic-speak
import requests

# lang/style ride in the 'instructions' field as a JSON string,
# since OpenAI's audio.speech has no field of its own for them
resp = requests.post(
    "https://api.bodhan.ai/v1/audio/speech",
    headers={"Authorization": "Bearer your_bodhan_key"},
    json={
        "model": "indic-speak",
        "input": "The meeting has been postponed.",
        "voice": "Amit",
        "instructions": '{"lang": "en"}'
    },
)
with open("speech.wav", "wb") as f:
    f.write(resp.content)