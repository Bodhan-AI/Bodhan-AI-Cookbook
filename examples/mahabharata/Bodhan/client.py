
import json
import os
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent

LANGUAGES = {"en": "English", "te": "తెలుగు (Telugu)", "hi": "हिन्दी (Hindi)"}

SPEAK_VOICE = os.getenv("BODHAN_VOICE", "Amit")
TIMEOUT = 60

class BodhanError(RuntimeError):
    pass

def _load_env():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _key(name):
    return os.getenv(name, "")


def _base():
    return os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1").rstrip("/")


def _call(req):
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _check_lang(code):
    if code not in LANGUAGES:
        raise ValueError(f"Unsupported language: {code}")


# Translation

def _translate_once(text, target):
    payload = {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": text}],
        "target_language_code": target,
        "target_script": "native"
    }

    req = urllib.request.Request(
        _base() + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {_key('BODHAN_TRANSLATE_KEY')}",
            "Content-Type": "application/json"
        }
    )

    return json.loads(_call(req))["choices"][0]["message"]["content"]


def to_language(text, target):
    return _translate_once(text, target)


def to_english(text, source_lang=None):
    return text if source_lang == "en" else to_language(text, "en")


def query_to_english(query, lang):
    return query if lang == "en" else to_language(query, "en")


def answer_to_language(answer, lang):
    return answer if lang == "en" else to_language(answer, lang)


# Transcription

def transcribe(audio_bytes, lang, filename="speech.wav"):
    boundary = uuid.uuid4().hex

    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nindic-transcribe\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{lang}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="vad"\r\n\r\ntrue\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f'Content-Type: audio/wav\r\n\r\n'
    ).encode() + audio_bytes + f'\r\n--{boundary}--\r\n'.encode()

    req = urllib.request.Request(
        _base() + "/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {_key('BODHAN_TRANSCRIBE_KEY')}",
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        }
    )

    return json.loads(_call(req)).get("text", "")


# Speech

def speak(text, lang, voice=None, max_chars=2500):
    payload = {
        "model": "indic-speak",
        "input": text[:max_chars],
        "voice": voice or SPEAK_VOICE,
        "instructions": json.dumps({"lang": lang})
    }

    req = urllib.request.Request(
        _base() + "/audio/speech",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {_key('BODHAN_SPEAK_KEY')}",
            "Content-Type": "application/json"
        }
    )

    return _call(req)
