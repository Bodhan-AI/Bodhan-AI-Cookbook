import io
import json
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feedback_analyser.analyse import LexiconAnalyser, load_taxonomy  # noqa: E402

RECIPE = Path(__file__).resolve().parent.parent


def make_wav(seconds: float, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status=200, payload=None, content=b"", headers=None):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.headers = headers or {}
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        result = self.responder(url, kwargs)
        if isinstance(result, Exception):
            raise result
        return result


class FakeBodhan:
    """Stands in for BodhanClient in pipeline tests; answers from small lookup tables."""

    def __init__(self, to_english=None, to_language=None, transcripts=None, pages=None, fail_on=None):
        self.to_english = to_english or {}
        self.to_language = to_language or {}
        self.transcripts = transcripts or {}
        self.pages = pages or {}
        self.fail_on = fail_on or set()
        self.log = []

    def translate(self, text, target_language, script=None):
        self.log.append(("translate", target_language, text))
        if target_language == "en":
            return self.to_english.get(text, text)
        return self.to_language.get((text, target_language), f"[{target_language}] {text}")

    def transliterate(self, text, language, script="roman"):
        self.log.append(("transliterate", language, script))
        return f"roman({text})"

    def transcribe(self, path, language=None):
        self.log.append(("transcribe", Path(path).name, language))
        if Path(path).name in self.fail_on:
            from feedback_analyser.bodhan import BodhanError
            raise BodhanError("audio could not be decoded", status=400)
        return self.transcripts[Path(path).name]

    def ocr(self, path):
        self.log.append(("ocr", Path(path).name))
        return self.pages[Path(path).name]

    def speak(self, text, voice, lang, style=None):
        self.log.append(("speak", lang, voice, style))
        return make_wav(0.1)


@pytest.fixture
def taxonomy():
    return load_taxonomy(RECIPE / "taxonomy.json")


@pytest.fixture
def analyser(taxonomy):
    return LexiconAnalyser(taxonomy)
