
"""Minimal Bodhan API client (standard library only)."""

import json
import os
import re
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent

LANGUAGES = {"en": "English", "te": "తెలుగు (Telugu)", "hi": "हिन्दी (Hindi)"}

SPEAK_VOICE = os.getenv("BODHAN_VOICE", "Amit")
TIMEOUT = 60
CHUNK_CHARS = 1200


def _load_env():
    f = HERE / ".env"
    if f.is_file():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _key(name):
    return os.getenv(name, "").strip().strip('"')


def _base():
    return os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1").rstrip("/")


def _call(req):
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _check_lang(code):
    if code not in LANGUAGES:
        raise ValueError(f"Unsupported language: {code}")


# -------------------- Translate

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
        method="POST",
        headers={
            "Authorization": f"Bearer {_key('BODHAN_TRANSLATE_KEY')}",
            "Content-Type": "application/json"
        }
    )

    return json.loads(_call(req))["choices"][0]["message"]["content"]


def _chunks(text):
    out, cur = [], ""

    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > CHUNK_CHARS:
            out.append(cur)
            cur = ""

        cur = f"{cur}\n\n{para}" if cur else para

    if cur:
        out.append(cur)

    return out


def to_language(text, target):
    _check_lang(target)

    if not text or not text.strip():
        return text

    return "\n\n".join(
        _translate_once(chunk, target) for chunk in _chunks(text)
    )


def to_english(text, source_lang=None):
    return text if source_lang == "en" else to_language(text, "en")


def query_to_english(query, lang):
    return query if lang == "en" else to_language(query, "en")


def answer_to_language(answer, lang):
    return answer if lang == "en" else to_language(answer, lang)


# -------------------- Transcribe

def transcribe(audio_bytes, lang, filename="speech.wav"):
    _check_lang(lang)

    boundary = uuid.uuid4().hex

    parts = []

    for name, value in (
        ("model", "indic-transcribe"),
        ("language", lang),
        ("vad", "true")
    ):
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; '
            f'name="{name}"\r\n\r\n{value}\r\n'.encode()
        )

    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; '
        f'name="file"; filename="{filename}"\r\n'
        f'Content-Type: audio/wav\r\n\r\n'.encode()
        + audio_bytes + b"\r\n"
    )

    parts.append(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        _base() + "/audio/transcriptions",
        data=b"".join(parts),
        method="POST",
        headers={
            "Authorization": f"Bearer {_key('BODHAN_TRANSCRIBE_KEY')}",
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        }
    )

    return json.loads(_call(req)).get("text", "").strip()


# -------------------- Speak

def _plain(text):
    text = re.sub(
        r"`+|\*+|_{2,}|^#{1,6}\s*|^\s*[-•+]\s+|\|",
        " ",
        text,
        flags=re.M
    )

    return re.sub(r"[ \t]+", " ", text).strip()


def speak(text, lang, voice=None, max_chars=2500):
    _check_lang(lang)

    text = _plain(text)[:max_chars]

    payload = {
        "model": "indic-speak",
        "input": text,
        "voice": voice or SPEAK_VOICE,
        "instructions": json.dumps({"lang": lang})
    }

    req = urllib.request.Request(
        _base() + "/audio/speech",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {_key('BODHAN_SPEAK_KEY')}",
            "Content-Type": "application/json"
        }
    )

    return _call(req)
