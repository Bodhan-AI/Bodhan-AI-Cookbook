
import os
import json
import uuid
import urllib.request
from pathlib import Path

# Load API keys
env = Path(__file__).parent / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

BASE_URL = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1")
LANGUAGES = {"en", "te", "hi"}
VOICE = os.getenv("BODHAN_VOICE", "Amit")


def request(endpoint, payload, key, content_type="application/json"):
    req = urllib.request.Request(
        BASE_URL + endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {os.environ[key]}",
            "Content-Type": content_type
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def translate(text, lang):
    if lang not in LANGUAGES:
        raise ValueError(f"Unsupported language: {lang}")

    if lang == "en" and not text.strip():
        return text

    payload = json.dumps({
        "model": "indic-translate",
        "messages": [{"role": "user", "content": text}],
        "target_language_code": lang,
        "target_script": "native"
    }).encode()

    response = request(
        "/chat/completions",
        payload,
        "BODHAN_TRANSLATE_KEY"
    )

    return json.loads(response)["choices"][0]["message"]["content"]


def transcribe(audio_bytes, lang, filename="speech.wav"):
    if lang not in LANGUAGES:
        raise ValueError(f"Unsupported language: {lang}")

    boundary = uuid.uuid4().hex

    fields = [
        ("model", "indic-transcribe"),
        ("language", lang),
        ("vad", "true")
    ]

    body = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        for name, value in fields
    )

    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        "Content-Type: audio/wav\r\n\r\n"
    ).encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()

    response = request(
        "/audio/transcriptions",
        body,
        "BODHAN_TRANSCRIBE_KEY",
        f"multipart/form-data; boundary={boundary}"
    )

    return json.loads(response).get("text", "").strip()


def speak(text, lang, voice=VOICE):
    if lang not in LANGUAGES:
        raise ValueError(f"Unsupported language: {lang}")

    payload = json.dumps({
        "model": "indic-speak",
        "input": text[:2500],
        "voice": voice,
        "instructions": json.dumps({"lang": lang})
    }).encode()

    return request("/audio/speech", payload, "BODHAN_SPEAK_KEY")
