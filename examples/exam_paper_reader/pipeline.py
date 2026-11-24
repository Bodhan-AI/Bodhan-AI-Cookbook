"""Exam paper reader pipeline: photo of a paper -> text -> translation -> speech,
and a spoken answer -> text.

Each step is one Bodhan API call:
    read_paper()  -> indic-ocr         (POST /v1/chat/completions)
    translate()   -> indic-translate   (POST /v1/chat/completions)
    speak()       -> indic-speak       (POST /v1/audio/speech)
    transcribe()  -> indic-transcribe  (POST /v1/audio/transcriptions)

Run it on one image from the command line:
    python pipeline.py sample_data/sample_paper.png --lang hi
"""

from __future__ import annotations

import argparse
import base64
import functools
import hashlib
import io
import json
import os
import re
import time
import wave
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

load_dotenv()
BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")

# language code -> (display name, voice). Voices come from scripts/bodhan_api_rules.json.
LANGUAGES = {
    "hi": ("Hindi", "Kavya"),
    "bn": ("Bengali", "Ishita"),
    "ta": ("Tamil", "Anitha"),
    "en": ("English", "Amit"),
}
SPEECH_STYLE = "educational lecture"
MAX_SPEECH_CHARS = 250  # indic-speak wants a sentence or two (~30 s) per request


# A console key can be limited to one model. If you have one key per model, set the
# variable on the right; otherwise BODHAN_API_KEY is used for every call.
KEY_VARS = {
    "indic-ocr": "BODHAN_OCR_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
}


def api_key(model: str) -> str:
    """Return the key for this model: its own variable if set, else BODHAN_API_KEY."""
    key = os.environ.get(KEY_VARS[model]) or os.environ.get("BODHAN_API_KEY")
    if not key:
        raise RuntimeError(f"No API key found. Set BODHAN_API_KEY or {KEY_VARS[model]} in .env")
    return key


RETRY_WAITS = (5, 10, 20, 30)  # seconds to wait after each 429 before trying again


def bodhan(path: str, body: dict, files: dict | None = None) -> requests.Response:
    """POST to the Bodhan API. Backs off on 429 and raises readable errors.

    The body is sent as JSON, or as form fields next to `files` when there is a file to
    upload (transcription takes the audio as a multipart form, not JSON).

    The rate limit is counted per minute and a retry counts as a request, so the waits
    are long and few: about a minute in total before giving up.
    """
    headers = {"Authorization": f"Bearer {api_key(body['model'])}"}
    payload = {"data": body, "files": files} if files else {"json": body}
    for wait in (*RETRY_WAITS, None):
        resp = requests.post(f"{BASE_URL}{path}", headers=headers, timeout=180, **payload)
        if resp.status_code != 429 or wait is None:
            break
        retry_after = resp.headers.get("Retry-After", "")
        time.sleep(int(retry_after) if retry_after.isdigit() else wait)
    if not resp.ok:
        try:
            err = resp.json()["error"]
        except (ValueError, KeyError):
            resp.raise_for_status()
        raise RuntimeError(
            f"{resp.status_code} {err.get('code')}: {err.get('message')} "
            f"(request_id={err.get('request_id')})"
        )
    return resp


CACHE_DIR = Path(__file__).parent / "outputs" / "ocr_cache"
MAX_IMAGE_SIDE = 2000  # px; phone photos are often 4000+ px, which only slows the upload


def prepare_image(image: bytes) -> tuple[bytes, str]:
    """Make a photo ready for OCR. Returns (image bytes, mime type).

    Phones often save a photo sideways and record the turn in EXIF metadata, so the
    image is rotated upright first. Large photos are shrunk to MAX_IMAGE_SIDE.
    Images that need no change are returned untouched, so their cache key stays stable.
    """
    with Image.open(io.BytesIO(image)) as img:
        fmt = img.format
        rotated = img.getexif().get(0x0112, 1) != 1  # 0x0112 is the EXIF orientation tag
        upright = ImageOps.exif_transpose(img)
        too_big = max(upright.size) > MAX_IMAGE_SIDE
        if fmt in ("PNG", "JPEG") and not rotated and not too_big:
            return image, f"image/{fmt.lower()}"
        upright.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        out = io.BytesIO()
        upright.convert("RGB").save(out, format="JPEG", quality=90)
        return out.getvalue(), "image/jpeg"


def read_paper(image: bytes, mime: str = "image/png") -> dict:
    """OCR one PNG/JPEG page. Returns the full response: Markdown text plus layout blocks.

    OCR is billed per image, so each response is saved under outputs/ocr_cache/, named by
    a hash of the image bytes. The same image is never sent (or paid for) twice.
    """
    cache_file = CACHE_DIR / f"{hashlib.sha256(image).hexdigest()[:16]}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    b64 = base64.b64encode(image).decode()
    body = {
        "model": "indic-ocr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                ],
            }
        ],
        "table_format": "markdown",  # default is HTML; Markdown tables are easier to clean
    }
    result = bodhan("/v1/chat/completions", body).json()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


WIDE_GAP = re.compile(r"\s{3,}|\t")
# A row that starts a new item: "- x", "* x", "3. x", "3) x", "(a) x" or a table row.
ITEM_START = re.compile(r"\s*([-*•|]|\d+[.)]|\([A-Za-z0-9]{1,3}\))\s")


def clean(text: str) -> str:
    """Remove Markdown marks so they are not translated or spoken."""
    text = re.sub(r"^[#>\-*\s]+", "", text)  # heading, quote and bullet markers
    text = re.sub(r"[*_`|]", "", text)  # bold, italic, code and table marks
    return re.sub(r"\s+", " ", text).strip()


def split_units(text: str) -> list[str]:
    """Split OCR text into units: one heading, one paragraph or one question each.

    In the OCR output a blank line separates units, while a single line break is only
    where the printed line wrapped. Wrapped lines are joined back into one paragraph,
    because a sentence translated one printed line at a time comes out as fragments.

    Exception: a wide gap separates two fields on one printed line, e.g.
    "Time: 30 minutes      Maximum marks: 10". Joined into one phrase, the translator
    mixes the fields up, so each field becomes its own unit.
    """
    units = []
    for chunk in re.split(r"\n\s*\n", text):
        rows = chunk.splitlines()
        if any(WIDE_GAP.search(row.strip()) for row in rows):
            pieces = [field for row in rows for field in WIDE_GAP.split(row)]
        else:
            pieces = []
            for row in rows:
                if pieces and not ITEM_START.match(row):
                    pieces[-1] += " " + row  # a wrapped line: continue the paragraph
                else:
                    pieces.append(row)  # the first line, or a new numbered or bulleted item
        units.extend(piece for piece in map(clean, pieces) if piece)
    return units


def paper_units(ocr: dict) -> list[tuple[str, str]]:
    """Turn an OCR response into (kind, text) units, where kind is "title" or "text".

    The layout blocks say what each piece of the page is (Chapter-title, Paragraph,
    List, ...), so headings can be shown as headings. Responses without blocks fall
    back to the plain Markdown, with every unit treated as text.
    """
    units = []
    for block in sorted(ocr.get("blocks") or [], key=lambda b: b.get("order", 0)):
        kind = "title" if "title" in block.get("label", "").lower() else "text"
        units.extend((kind, text) for text in split_units(block.get("text", "")))
    if not units:
        content = ocr["choices"][0]["message"]["content"]
        units = [("text", text) for text in split_units(content)]
    return remove_repeats(units)


def remove_repeats(units: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop a unit whose text repeats inside its neighbour.

    OCR layout blocks can overlap, so the same text comes back twice in a row, e.g.
    "Time: 30 minutes" from one block and again at the start of the next. We keep the
    longer one. Only neighbours are compared, so a phrase that legitimately appears
    in two separate questions is left alone.
    """
    kept: list[tuple[str, str]] = []
    for unit in units:
        if kept and unit[1] in kept[-1][1]:
            continue  # this text is already inside the previous unit
        if kept and kept[-1][1] in unit[1]:
            kept[-1] = unit  # the previous text is inside this one; keep the longer
            continue
        kept.append(unit)
    return kept


# Unicode ranges of the scripts our target languages are written in.
SCRIPTS = {
    "hi": ("ऀ", "ॿ"),  # Devanagari
    "bn": ("ঀ", "৿"),  # Bengali
    "ta": ("஀", "௿"),  # Tamil
    "en": ("A", "z"),  # Latin letters
}


def already_in(text: str, language: str) -> bool:
    """True if most letters of the text are already in the target language's script.

    Translating Hindi "into Hindi" makes the model reword the text and introduces
    mistakes, so such text is kept as it is. This is a script check, not a language
    check: Marathi and Nepali also use Devanagari and will be treated like Hindi.
    """
    letters = [ch for ch in text if ch.isalpha()]
    low, high = SCRIPTS[language]
    return bool(letters) and sum(low <= ch <= high for ch in letters) > len(letters) / 2


@functools.lru_cache(maxsize=2048)  # the same text is translated once per run, not per click
def translate(text: str, target_language: str) -> str:
    """Translate text into the target language. The source language is auto-detected.

    Text already written in the target language's script is returned unchanged.
    """
    if already_in(text, target_language):
        return text
    body = {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": text}],
        "target_language": target_language,
    }
    choice = bodhan("/v1/chat/completions", body).json()["choices"][0]
    if choice["finish_reason"] == "length":
        # A cut-off translation still reads fluently, so flag it instead of failing silently.
        print(f"warning: translation was cut off for: {text[:60]!r}")
    return choice["message"]["content"]


def chunk_for_speech(text: str, limit: int = MAX_SPEECH_CHARS) -> list[str]:
    """Split text at sentence ends so each piece fits in one indic-speak request."""
    sentences = re.split(r"(?<=[.?!।])\s+", text.strip())
    chunks, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def speak(text: str, language: str) -> bytes:
    """Read text aloud in the given language and return one WAV file as bytes."""
    voice = LANGUAGES[language][1]
    frames, params = [], None
    for chunk in chunk_for_speech(text):
        body = {
            "model": "indic-speak",
            "input": chunk,
            "voice": voice,
            "instructions": json.dumps({"lang": language, "style": SPEECH_STYLE}),
        }
        wav_bytes = bodhan("/v1/audio/speech", body).content
        with wave.open(io.BytesIO(wav_bytes)) as wav:
            params = params or wav.getparams()
            frames.append(wav.readframes(wav.getnframes()))
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setparams(params)
        wav.writeframes(b"".join(frames))
    return out.getvalue()


# A question starts with a number ("3.", "3)", "Q3", "Q. 3", "प्रश्न 3") or ends with "?".
# "(?!\d)" keeps a decimal such as "3.5 kg" from counting as question 3.
QUESTION_START = re.compile(r"\s*(\d+\s*[.)](?!\d)|Q\.?\s*\d+|प्रश्न\s*\d+)", re.IGNORECASE)


def is_question(text: str) -> bool:
    """True if a unit of the paper looks like a question the student should answer.

    Checked on the original text, because question numbers survive OCR well.
    """
    return bool(QUESTION_START.match(text)) or text.rstrip().endswith("?")


MAX_LISTEN_SECONDS = 25  # indic-transcribe takes about 30 s of audio per request
MAX_ANSWER_SECONDS = 180  # longest recording accepted: every 25 s of it is one paid request


def split_wav(audio: bytes, seconds: int = MAX_LISTEN_SECONDS) -> list[bytes]:
    """Cut a WAV recording into pieces of at most `seconds`, each a complete WAV file.

    The recording comes from a browser, so it is checked before any paid call: it must
    be a readable PCM WAV file and no longer than MAX_ANSWER_SECONDS.
    """
    pieces = []
    try:
        with wave.open(io.BytesIO(audio)) as wav:
            params = wav.getparams()
            if wav.getnframes() > wav.getframerate() * MAX_ANSWER_SECONDS:
                raise RuntimeError(
                    f"The recording is longer than {MAX_ANSWER_SECONDS // 60} minutes. "
                    "Record the answer in shorter parts."
                )
            while frames := wav.readframes(wav.getframerate() * seconds):
                out = io.BytesIO()
                with wave.open(out, "wb") as piece:
                    piece.setparams(params)
                    piece.writeframes(frames)
                pieces.append(out.getvalue())
    except (wave.Error, EOFError) as err:
        raise RuntimeError("The recording could not be read as a WAV file.") from err
    return pieces


def transcribe(audio: bytes, language: str) -> str:
    """Turn a spoken answer (a WAV recording) into text. Returns "" if nothing was heard.

    `language` is the language the student speaks; the model takes it as a strong hint.
    Recordings longer than MAX_LISTEN_SECONDS are sent in pieces and the texts joined.
    A word cut in half at a piece boundary can come out wrong.
    """
    texts = []
    for n, piece in enumerate(split_wav(audio)):
        body = {"model": "indic-transcribe", "language": language}
        files = {"file": (f"answer_{n}.wav", piece, "audio/wav")}
        result = bodhan("/v1/audio/transcriptions", body, files=files).json()
        texts.append(str(result.get("text") or "").strip())
    return " ".join(text for text in texts if text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read an exam paper image aloud in another language.")
    parser.add_argument("image", help="PNG or JPEG of one page")
    parser.add_argument("--lang", default="hi", choices=LANGUAGES, help="target language")
    parser.add_argument("--no-audio", action="store_true", help="skip text to speech")
    args = parser.parse_args()

    out_dir = Path(__file__).parent / "outputs"
    out_dir.mkdir(exist_ok=True)

    print("1/3 Reading the paper (indic-ocr)...")
    image, mime = prepare_image(Path(args.image).read_bytes())
    ocr = read_paper(image, mime)
    units = paper_units(ocr)

    print(f"2/3 Translating {len(units)} parts into {LANGUAGES[args.lang][0]} (indic-translate)...")
    translated = [translate(text, args.lang) for _, text in units]
    for (kind, original), new in zip(units, translated, strict=True):
        print(f"   [{kind}] {original}\n   -> {new}")

    if not args.no_audio:
        print("3/3 Reading it aloud (indic-speak)...")
        audio_path = out_dir / f"paper_{args.lang}.wav"
        audio_path.write_bytes(speak(" ".join(translated), args.lang))
        print(f"   saved {audio_path}")


if __name__ == "__main__":
    main()
