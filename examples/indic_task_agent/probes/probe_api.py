"""Live probes of the Bodhan API: what each model actually accepts and returns.

Every call is written to probes/out/<name>.json (request, status, latency, all response headers, full
body); audio responses are saved next to it as <name>.wav. Request headers are never recorded, so the
recordings hold no keys. Findings are written up in probes/FINDINGS.md.

    python probes/probe_api.py <probe> [--long] [--html]

    routes         Find the live routes for translate and transliterate (the Cookbook's /translate returns 404)
    translate      indic-translate with the documented parameters
    transliterate  indic-transliterate with the documented parameters
    transcribe     indic-transcribe: language hint, auto-detect, timestamps, bad language, and >30 s audio
    speak          indic-speak: audio format, styles, voice/language mismatch, errors
    ocr            indic-ocr: full response (Markdown, layout blocks, usage), table output, bad input

Each probe spends a little real Bodhan credit (well under Rs 1 in total).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import time
import wave
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, features

RECIPE = Path(__file__).resolve().parent.parent
REPO = RECIPE.parent.parent
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
BASE_URL = "https://api.bodhan.ai"
RULES = json.loads((REPO / "scripts" / "bodhan_api_rules.json").read_text(encoding="utf-8"))

load_dotenv(RECIPE / ".env")
sys.stdout.reconfigure(encoding="utf-8")

# Used to get Indian-language test text from indic-translate instead of writing it by hand.
SAMPLE_ENGLISH = (
    "The school will remain closed tomorrow because of heavy rain. "
    "Classes will resume on Monday at nine in the morning."
)
SAMPLE = REPO / "sample_data" / "sample_hindi.wav"  # 6.5 s, 16 kHz mono PCM
FONT = Path("C:/Windows/Fonts/Nirmala.ttc")  # Windows font with Devanagari; change on Linux/macOS


def key(env: str) -> str:
    value = os.environ.get(env, "")
    if not value or value.startswith("<"):
        sys.exit(f"{env} is not set in {RECIPE / '.env'}")
    return value


def _shorten(obj):
    """Keep base64 payloads out of the recordings."""
    if isinstance(obj, str) and obj.startswith("data:") and len(obj) > 200:
        return f"{obj[:40]}...<{len(obj)} chars>"
    if isinstance(obj, dict):
        return {k: _shorten(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shorten(v) for v in obj]
    return obj


def _wav_info(blob: bytes) -> dict:
    try:
        with wave.open(io.BytesIO(blob)) as wf:
            return {
                "channels": wf.getnchannels(),
                "sample_width": wf.getsampwidth(),
                "sample_rate": wf.getframerate(),
                "seconds": round(wf.getnframes() / wf.getframerate(), 2),
            }
    except (wave.Error, EOFError) as exc:
        return {"not_wav": str(exc)}


def call(name, path, key_env, *, json_body=None, data=None, files=None, timeout=180, api_key=None):
    """POST to Bodhan, record everything, print a one-line summary, return the response.

    Returns None on a transport error, or when a successful recording already exists
    (pass --fresh to call again), so re-runs only spend credit on new or failed cases.
    """
    previous = OUT / f"{name}.json"
    if previous.exists() and "--fresh" not in sys.argv:
        if json.loads(previous.read_text(encoding="utf-8")).get("status") == 200:
            print(f"{name:34} (already recorded, skipped)")
            return None
    record = {
        "name": name,
        "request": {
            "path": path,
            "json": _shorten(json_body),
            "form": data,
            "files": {k: f"{v[0]} <{len(v[1])} bytes, {v[2]}>" for k, v in (files or {}).items()},
        },
    }
    started = time.perf_counter()
    try:
        resp = requests.post(
            BASE_URL + path,
            headers={"Authorization": f"Bearer {api_key or key(key_env)}"},
            json=json_body,
            data=data,
            files=files,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        record.update(latency_ms=round((time.perf_counter() - started) * 1000), transport_error=repr(exc))
        resp = None
    else:
        ctype = resp.headers.get("content-type", "")
        record.update(
            status=resp.status_code,
            latency_ms=round((time.perf_counter() - started) * 1000),
            headers=dict(resp.headers),
        )
        if "json" in ctype:
            record["body"] = resp.json()
        elif ctype.startswith("audio") or resp.content[:4] == b"RIFF":
            audio = OUT / f"{name}.wav"
            audio.write_bytes(resp.content)
            record["body"] = {"saved_to": audio.name, "bytes": len(resp.content), **_wav_info(resp.content)}
        else:
            record["body_text"] = resp.text[:3000]

    (OUT / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    body = record.get("body")
    shape = list(body) if isinstance(body, dict) else type(body).__name__
    print(f"{name:34} status={record.get('status', 'ERR')} {record['latency_ms']:>6} ms  keys={shape}")
    return resp


def indic_text(lang: str, english: str = SAMPLE_ENGLISH) -> str:
    """Indian-language test text produced by indic-translate, cached in probes/out/."""
    digest = hashlib.sha256(english.encode()).hexdigest()[:8]
    cached = OUT / f"sample_text_{lang}_{digest}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    resp = call(f"sample_text_{lang}_{digest}", "/v1/chat/completions", "BODHAN_TRANSLATE_KEY", json_body={
        "model": "indic-translate",
        "messages": [{"role": "user", "content": english}],
        "target_language": lang,
    })
    if resp is None or not resp.ok:
        sys.exit(f"could not get {lang} sample text from indic-translate")
    text = resp.json()["choices"][0]["message"]["content"]
    cached.write_text(text, encoding="utf-8")
    return text


def translate(name: str, text: str, target: str | None, script: str | None = "native", **extra):
    api_key = extra.pop("api_key", None)
    body = {"model": "indic-translate", "messages": [{"role": "user", "content": text}], **extra}
    if target is not None:
        body["target_language_code"] = target
    if script is not None:
        body["target_script"] = script
    return call(name, "/v1/chat/completions", "BODHAN_TRANSLATE_KEY", json_body=body, api_key=api_key)


TABLE = """# Weekly worksheet

Answer all questions before Friday.

| Subject | Topic | Marks |
|---|---|---|
| Science | Photosynthesis | 10 |
| History | The Mughal Empire | 15 |
"""


def transliterate(name: str, text: str, language: str, script: str):
    body = {
        "model": "indic-transliterate",
        "messages": [{"role": "user", "content": text}],
        "target_language_code": language,
        "target_script": script,
    }
    return call(name, "/v1/chat/completions", "BODHAN_TRANSLITERATE_KEY", json_body=body)


def wav_bytes(repeat: int = 1) -> bytes:
    """The sample clip, optionally looped `repeat` times to make a longer clip."""
    with wave.open(str(SAMPLE)) as src:
        params, frames = src.getparams(), src.readframes(src.getnframes())
    buf = io.BytesIO()
    with wave.open(buf, "wb") as dst:
        dst.setparams(params)
        dst.writeframes(frames * repeat)
    return buf.getvalue()


def transcribe(name: str, audio: bytes, **fields):
    return call(
        name,
        "/v1/audio/transcriptions",
        "BODHAN_TRANSCRIBE_KEY",
        data={"model": "indic-transcribe", **fields},
        files={"file": ("clip.wav", audio, "audio/wav")},
    )


def speak(name: str, text: str, voice: str, lang: str, style: str | None = None):
    instructions = {"lang": lang, **({"style": style} if style else {})}
    body = {"model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps(instructions)}
    return call(name, "/v1/audio/speech", "BODHAN_SPEAK_KEY", json_body=body)


def make_page() -> Path:
    """A synthetic scanned page: two Hindi lines (from indic-translate) and an English table."""
    layout = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    if layout == ImageFont.Layout.BASIC:
        print("note: Pillow has no libraqm, so Devanagari conjuncts may render imperfectly")
    big = ImageFont.truetype(str(FONT), 34, layout_engine=layout)
    small = ImageFont.truetype(str(FONT), 26, layout_engine=layout)

    img = Image.new("RGB", (1240, 900), "white")
    draw = ImageDraw.Draw(img)
    hindi = indic_text("hi").split("। ")
    draw.text((60, 50), hindi[0], font=big, fill="black")
    draw.text((60, 120), hindi[-1], font=big, fill="black")

    rows = [("Subject", "Topic", "Marks"), ("Science", "Photosynthesis", "10"), ("History", "Mughal Empire", "15")]
    x0, y0, widths, h = 60, 260, (300, 420, 160), 70
    for r, row in enumerate(rows):
        x = x0
        for c, cell in enumerate(row):
            draw.rectangle([x, y0 + r * h, x + widths[c], y0 + (r + 1) * h], outline="black", width=2)
            draw.text((x + 15, y0 + r * h + 18), cell, font=small, fill="black")
            x += widths[c]

    page = OUT / "ocr_page.png"
    img.save(page)
    return page


def ocr(name: str, image: bytes, mime: str = "image/png", **extra):
    url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    body = {
        "model": "indic-ocr",
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}],
        **extra,
    }
    return call(name, "/v1/chat/completions", "BODHAN_OCR_KEY", json_body=body)


def probe_routes() -> None:
    """Find the live routes for translate and transliterate (the Cookbook's /translate returns 404)."""
    for env in ("BODHAN_TRANSLATE_KEY", "BODHAN_TRANSLITERATE_KEY"):
        resp = requests.get(f"{BASE_URL}/v1/models", headers={"Authorization": f"Bearer {key(env)}"}, timeout=30)
        print(f"GET /v1/models with {env}: {resp.status_code} {resp.text[:400]}")
    body = {"text": "Good morning", "target_language": "ta"}
    for path in ("/translate", "/v1/translate", "/v1/translation", "/v1/translations", "/api/translate"):
        call(f"route_translate{path.replace('/', '_')}", path, "BODHAN_TRANSLATE_KEY",
             json_body={**body, "model": "indic-translate"})
    call("route_translate_chat", "/v1/chat/completions", "BODHAN_TRANSLATE_KEY", json_body={
        "model": "indic-translate",
        "messages": [{"role": "user", "content": "Good morning"}],
        "target_language": "ta",
    })
    translit = {"text": "Good morning", "language": "hi", "script": "native", "model": "indic-transliterate"}
    for path in ("/transliterate", "/v1/transliterate"):
        call(f"route_transliterate{path.replace('/', '_')}", path, "BODHAN_TRANSLITERATE_KEY", json_body=translit)


def probe_translate() -> None:
    """Probe indic-translate with the documented parameters.
    --long: adds a ~2,500-word input to find the context window"""
    translate("tr_en_to_kn", "The meeting has been postponed.", "kn")  # the docs example
    translate("tr_en_to_ta", SAMPLE_ENGLISH, "ta")
    translate("tr_hi_to_en", indic_text("hi"), "en")  # source auto-detected?
    translate("tr_roman", SAMPLE_ENGLISH, "hi", script="roman")
    translate("tr_codemix", SAMPLE_ENGLISH, "hi", script="codemix")
    translate("tr_markdown_table", TABLE, "mr")
    translate("tr_source_language_code", SAMPLE_ENGLISH, "bn", source_language_code="en")
    translate("tr_max_output_tokens", SAMPLE_ENGLISH, "te", max_output_tokens=8)  # truncation signal?
    translate("tr_no_target", SAMPLE_ENGLISH, None)
    translate("tr_no_script", SAMPLE_ENGLISH, "gu", script=None)  # is target_script optional?
    translate("tr_unsupported_target", SAMPLE_ENGLISH, "fr")
    translate("tr_bad_script", SAMPLE_ENGLISH, "hi", script="cyrillic")
    translate("tr_empty_text", "", "hi")
    translate("tr_bad_key", SAMPLE_ENGLISH, "hi", api_key="invalid-probe-key")
    if "--long" in sys.argv:
        long_text = " ".join([SAMPLE_ENGLISH] * 120)  # about 2,500 words
        translate("tr_long_input", long_text, "hi")


def probe_transliterate() -> None:
    """Probe indic-transliterate with the documented parameters."""
    hindi = indic_text("hi")
    resp = transliterate("tl_hi_to_roman", hindi, "hi", "roman")
    if resp is not None and resp.ok:
        roman = resp.json()["choices"][0]["message"]["content"]
        transliterate("tl_roman_to_hi", roman, "hi", "native")
    transliterate("tl_ta_to_roman", indic_text("ta"), "ta", "roman")
    transliterate("tl_unsupported_language", "hello", "fr", "roman")
    transliterate("tl_bad_script", hindi, "hi", "cyrillic")


def probe_transcribe() -> None:
    """Probe indic-transcribe: language hint, auto-detect, timestamps, bad language, and >30 s audio."""
    clip = wav_bytes()
    transcribe("transcribe_hi", clip, language="hi")
    transcribe("transcribe_auto_language", clip)  # does it detect and report the language?
    transcribe("transcribe_verbose_json", clip, language="hi", response_format="verbose_json")
    transcribe("transcribe_srt", clip, language="hi", response_format="srt")
    transcribe("transcribe_unsupported_language", clip, language="fr")
    transcribe("transcribe_39s", wav_bytes(repeat=6), language="hi")  # past the ~30 s limit


def probe_speak() -> None:
    """Probe indic-speak: audio format, styles, voice/language mismatch, errors.
    --long: adds a ~700-character request to find the length limit"""
    VOICES = RULES["models"]["indic-speak"]["voices"]
    tamil = indic_text("ta")
    tamil_female = VOICES["ta"]["female"][0]
    speak("speak_ta_basic", tamil, tamil_female, "ta")
    speak("speak_ta_style_news", tamil, tamil_female, "ta", style="news")
    speak("speak_en", SAMPLE_ENGLISH, (VOICES.get("en") or VOICES["hi"])["female"][0], "en")
    speak("speak_voice_lang_mismatch", tamil, VOICES["hi"]["female"][0], "ta")  # Hindi voice, Tamil text
    speak("speak_bad_voice", tamil, "NoSuchVoice", "ta")
    speak("speak_bad_style", tamil, tamil_female, "ta", style="opera")
    if "--long" in sys.argv:
        long_tamil = indic_text("ta", " ".join([SAMPLE_ENGLISH] * 5))
        print(f"long input: {len(long_tamil)} characters")
        speak("speak_long_input", long_tamil, tamil_female, "ta")


def probe_ocr() -> None:
    """Probe indic-ocr: full response (Markdown, layout blocks, usage), table output, bad input.
    --html: also the same page with table_format=html (+Rs 0.20)"""
    page = make_page().read_bytes()
    ocr("ocr_markdown", page, table_format="markdown")
    ocr("ocr_not_an_image", b"this is not an image")
    if "--html" in sys.argv:
        ocr("ocr_html", page, table_format="html")


PROBES = {"routes": probe_routes, "translate": probe_translate, "transliterate": probe_transliterate, "transcribe": probe_transcribe, "speak": probe_speak, "ocr": probe_ocr}


if __name__ == "__main__":
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not names or any(n not in PROBES for n in names):
        sys.exit(f"usage: python probes/probe_api.py {{{'|'.join(PROBES)}}} [--long] [--html]")
    for name in names:
        PROBES[name]()
