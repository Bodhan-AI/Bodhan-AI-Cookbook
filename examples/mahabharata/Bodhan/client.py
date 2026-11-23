"""Bodhan API client (standard library only). Keys come from Bodhan/.env or the environment.

    translate : POST /chat/completions        model=indic-translate
    transcribe: POST /audio/transcriptions    model=indic-transcribe  (multipart)
    speak     : POST /audio/speech            model=indic-speak       (returns WAV bytes)
"""
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent

# code -> label shown in the UI. Add more Indic languages here if your Bodhan plan supports them.
LANGUAGES = {"en": "English", "te": "తెలుగు (Telugu)", "hi": "हिन्दी (Hindi)"}

SPEAK_VOICE = os.getenv("BODHAN_VOICE", "Amit")
TIMEOUT = 60
CHUNK_CHARS = 1200  # long answers are translated paragraph-batch by paragraph-batch


class BodhanError(RuntimeError):
    pass


def _load_env():
    f = HERE / ".env"
    if f.is_file():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _key(name):
    k = (os.getenv(name) or "").strip().strip('"')
    if not k:
        raise BodhanError(f"{name} is not set (put it in Bodhan/.env)")
    return k


def _base():
    return os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1").rstrip("/")


def _call(req):
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise BodhanError(f"Bodhan HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise BodhanError(f"Bodhan unreachable: {e}") from None


def _check_lang(code):
    if code not in LANGUAGES:
        raise BodhanError(f"unsupported language: {code!r}")


# ---------------------------------------------------------------- translate
def _translate_once(text, target):
    payload = {"model": "indic-translate", "messages": [{"role": "user", "content": text}],
               "target_language_code": target, "target_script": "native"}
    req = urllib.request.Request(
        _base() + "/chat/completions", data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {_key('BODHAN_TRANSLATE_KEY')}",
                 "Content-Type": "application/json"})
    try:
        return json.loads(_call(req))["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        raise BodhanError("unexpected translate response") from None


def _chunks(text):
    """Split on blank lines, packing paragraphs up to CHUNK_CHARS (keeps markdown structure)."""
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
    """Translate text into `target` (e.g. 'te', 'hi', 'en'); source language is auto-detected."""
    _check_lang(target)
    if not text or not text.strip():
        return text
    return "\n\n".join(_translate_once(c, target) for c in _chunks(text))


def to_english(text, source_lang=None):
    if source_lang == "en":
        return text
    return to_language(text, "en")


def query_to_english(query, lang):
    return query if lang == "en" else to_language(query, "en")


def answer_to_language(answer, lang):
    return answer if lang == "en" else to_language(answer, lang)


# ---------------------------------------------------------------- transcribe
def transcribe(audio_bytes, lang, filename="speech.wav"):
    """Speech -> text in the spoken language (hi/te/en). Audio should be WAV."""
    _check_lang(lang)
    b = uuid.uuid4().hex
    parts = []
    for name, val in (("model", "indic-transcribe"), ("language", lang), ("vad", "true")):
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f'Content-Type: audio/wav\r\n\r\n'.encode() + audio_bytes + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request(
        _base() + "/audio/transcriptions", data=b"".join(parts), method="POST",
        headers={"Authorization": f"Bearer {_key('BODHAN_TRANSCRIBE_KEY')}",
                 "Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        return (json.loads(_call(req)).get("text") or "").strip()
    except ValueError:
        raise BodhanError("unexpected transcribe response") from None


# ---------------------------------------------------------------- speak
def _plain(text):
    """Strip markdown so it is not read aloud as symbols."""
    t = re.sub(r"`+|\*+|_{2,}|^#{1,6}\s*|^\s*[-•+]\s+|\|", " ", text, flags=re.M)
    return re.sub(r"[ \t]+", " ", t).strip()


def speak(text, lang, voice=None, max_chars=2500):
    """Text -> WAV bytes."""
    _check_lang(lang)
    text = _plain(text)[:max_chars]
    if not text:
        raise BodhanError("nothing to speak")
    payload = {"model": "indic-speak", "input": text, "voice": voice or SPEAK_VOICE,
               "instructions": json.dumps({"lang": lang})}
    req = urllib.request.Request(
        _base() + "/audio/speech", data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {_key('BODHAN_SPEAK_KEY')}",
                 "Content-Type": "application/json"})
    return _call(req)
