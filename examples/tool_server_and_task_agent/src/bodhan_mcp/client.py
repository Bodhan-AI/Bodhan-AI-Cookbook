"""Bodhan API client: real (httpx) and mock (test-only) implementations.

Endpoints, verified against https://console.bodhan.ai/api-docs/ on 16 Nov 2026:
- STT:        POST /v1/audio/transcriptions  (multipart; file, model=indic-transcribe,
               optional language; <=30s per request; -> {"text": ...})
- Translate:  POST /translate                (JSON; text, target_language, optional
               script/max_output_tokens; -> translation, truncated flag, usage)
- TTS:        POST /v1/audio/speech         (JSON; model=indic-speak, input, voice,
               instructions='{"lang": ..., "style": ...}'; -> WAV bytes)
- OCR:        POST /v1/chat/completions     (JSON; model=indic-ocr, one user message
               with a base64 image_url part; -> Markdown + top-level blocks)

Pricing (assignment sheet): STT Rs 0.10/audio-minute; translate Rs 0.20/10k
output tokens; TTS Rs 6/10k input chars; OCR Rs 0.20/image. The client
computes the cost of every call so the agent can enforce its budget.

Retries: 429/5xx back off exponentially (1s, 2s, 4s, max 3 retries) and
respect Retry-After when present; 401 fails immediately. Results are
cached in memory by request hash, so the same input is never sent twice
in one run ("no waste" target).

The mock client returns canned, clearly-marked responses for the demo
sample files. It exists only so the pipeline can be verified end to end
without keys; it must never be used for a real run.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field

import httpx

from . import media
from .errors import (
    AUTH_ERROR,
    MISSING_KEY,
    RATE_LIMITED,
    UNSUPPORTED_LANGUAGE,
    UPSTREAM_ERROR,
    ToolError,
)
from .languages import TRANSLATE_LANGUAGES, TRANSLATE_REFUSED, TTS_LANGUAGES, VOICES

BASE_URL = "https://api.bodhan.ai"

# Pricing in INR.
PRICE_STT_PER_MINUTE = 0.10
PRICE_TRANSLATE_PER_10K_TOKENS = 0.20
PRICE_TTS_PER_10K_CHARS = 6.0
PRICE_OCR_PER_IMAGE = 0.20

# Translation context: keep each request well inside the window so a 413
# (payload too large) is unlikely; the tool splits longer text.
MAX_TRANSLATE_CHARS = 6000


def _cost_stt(duration_seconds: float) -> float:
    return round(duration_seconds * PRICE_STT_PER_MINUTE / 60.0, 4)


def _cost_translate(output_tokens: int) -> float:
    return round(output_tokens * PRICE_TRANSLATE_PER_10K_TOKENS / 10000.0, 4)


def _cost_tts(chars: int) -> float:
    return round(chars * PRICE_TTS_PER_10K_CHARS / 10000.0, 4)


def _estimate_tokens(text: str) -> int:
    # Rough token estimate used only when the API returns no usage object.
    return max(1, len(text) // 4)


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    duration_seconds: float
    cost_inr: float
    cached: bool = False


@dataclass
class TranslationResult:
    translation: str
    target_language: str
    script: str
    truncated: bool
    output_tokens: int
    cost_inr: float
    cached: bool = False


@dataclass
class SynthesisResult:
    audio_bytes: bytes
    duration_seconds: float
    voice: str
    language: str
    cost_inr: float
    cached: bool = False


@dataclass
class OCRResult:
    markdown: str
    blocks: list[dict]
    cost_inr: float
    cached: bool = False


def build_http_client(base_url: str, timeout: float) -> httpx.Client:
    """Build an httpx client that honours the environment proxy safely.

    httpx's own environment-proxy parsing crashes on ``no_proxy`` entries
    that are bracketed IPv6 literals (e.g. ``[fd8b:...::1]``), which this
    sandbox sets. So proxy handling is done here, explicitly:
    ``trust_env=False`` (httpx never parses the env itself), the proxy URL
    is read from HTTPS_PROXY/https_proxy, and hosts listed in NO_PROXY are
    reached directly.
    """
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    host = httpx.URL(base_url).host or ""
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    bypass = False
    for entry in (e.strip().strip("[]") for e in no_proxy.split(",")):
        if not entry:
            continue
        if host == entry or host.endswith("." + entry):
            bypass = True
            break
    proxy = None if (bypass or not proxy_url) else proxy_url
    # SSL_CERT_FILE is the standard way to point at a custom CA bundle
    # (e.g. an egress proxy's CA). httpx with trust_env=False ignores it,
    # so pass it explicitly; default to normal verification when unset.
    verify = os.environ.get("SSL_CERT_FILE") or True
    return httpx.Client(
        base_url=base_url, timeout=timeout, trust_env=False, proxy=proxy,
        verify=verify,
    )


class BodhanClient:
    """Real Bodhan hosted-API client (one key per model)."""

    def __init__(
        self,
        transcribe_key: str | None = None,
        translate_key: str | None = None,
        speak_key: str | None = None,
        ocr_key: str | None = None,
        timeout: float = 60.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.keys = {
            "transcribe": transcribe_key or os.environ.get("BODHAN_TRANSCRIBE_KEY"),
            "translate": translate_key or os.environ.get("BODHAN_TRANSLATE_KEY"),
            "speak": speak_key or os.environ.get("BODHAN_SPEAK_KEY"),
            "ocr": ocr_key or os.environ.get("BODHAN_OCR_KEY"),
        }
        self.timeout = timeout
        self._http = http_client or build_http_client(BASE_URL, timeout)
        self._cache: dict[str, object] = {}
        self.calls_made = 0
        self.cache_hits = 0

    # -- helpers ------------------------------------------------------------
    def _require_key(self, kind: str) -> str:
        key = self.keys.get(kind)
        if not key:
            env = f"BODHAN_{kind.upper()}_KEY"
            raise ToolError(
                MISSING_KEY,
                f"No Bodhan {kind} key configured.",
                hint=f"Set {env} (get one at https://console.bodhan.ai/).",
            )
        return key

    def _cache_key(self, method: str, payload: str) -> str:
        return hashlib.sha256(f"{method}:{payload}".encode()).hexdigest()

    def _request(self, method: str, url: str, key: str, **kwargs) -> httpx.Response:
        """One HTTP call with retries on 429/5xx. Raises ToolError on failure."""
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {key}"
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                resp = self._http.request(method, url, headers=headers, **kwargs)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt == 3:
                    raise ToolError(
                        UPSTREAM_ERROR,
                        f"Bodhan request timed out after {self.timeout}s: {method} {url}",
                        hint="Retry once; if it persists, the API may be down.",
                        retryable=True,
                    ) from exc
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 401:
                raise ToolError(
                    AUTH_ERROR,
                    "Bodhan rejected the API key (401).",
                    hint="Check the key in the console and rotate it if it leaked.",
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == 3:
                    raise ToolError(
                        RATE_LIMITED if resp.status_code == 429 else UPSTREAM_ERROR,
                        f"Bodhan API returned {resp.status_code} after retries.",
                        hint="Back off and retry later; do not loop-retry.",
                        retryable=True,
                        status=resp.status_code,
                    )
                retry_after = resp.headers.get("retry-after")
                time.sleep(float(retry_after) if retry_after else delay)
                delay *= 2
                continue
            if resp.status_code >= 400:
                code, message = self._parse_error(resp)
                raise ToolError(
                    code,
                    f"Bodhan API error ({resp.status_code}): {message}",
                    status=resp.status_code,
                )
            self.calls_made += 1
            return resp
        raise ToolError(UPSTREAM_ERROR, f"Bodhan request failed: {last_exc}")

    @staticmethod
    def _parse_error(resp: httpx.Response) -> tuple[str, str]:
        try:
            err = resp.json().get("error", {})
            code = err.get("code", "upstream_error")
            message = err.get("message", resp.text[:200])
        except Exception:
            code, message = "upstream_error", resp.text[:200]
        mapping = {
            "unsupported_language": UNSUPPORTED_LANGUAGE,
            "invalid_api_key_error": AUTH_ERROR,
            "rate_limit_exceeded_error": RATE_LIMITED,
            "invalid_request_error": "invalid_request",
            "payload_too_large": "payload_too_large",
        }
        return mapping.get(code, code), message

    # -- STT -----------------------------------------------------------------
    def transcribe(
        self, audio_path: str, language: str | None = None
    ) -> TranscriptionResult:
        wav = media.ensure_wav(audio_path, workdir="/tmp/bodhan_audio")
        duration = media.wav_duration(wav)
        if duration > 30.0:
            raise ToolError(
                "invalid_input",
                f"Audio is {duration:.1f}s; one STT request takes at most 30s.",
                hint="Split it into <=30s chunks first (the transcribe tool does this).",
            )
        cache_key = self._cache_key("stt", f"{media.sha256_file(wav)}:{language}")
        if cache_key in self._cache:
            self.cache_hits += 1
            result = self._cache[cache_key]
            assert isinstance(result, TranscriptionResult)
            result.cached = True
            return result
        key = self._require_key("transcribe")
        with open(wav, "rb") as fh:
            files = {"file": (os.path.basename(wav), fh, "audio/wav")}
            data = {"model": "indic-transcribe"}
            if language:
                data["language"] = language
            resp = self._request(
                "POST", "/v1/audio/transcriptions", key, files=files, data=data
            )
        text = resp.json().get("text", "")
        result = TranscriptionResult(
            text=text,
            language=language,
            duration_seconds=duration,
            cost_inr=_cost_stt(duration),
        )
        self._cache[cache_key] = result
        return result

    # -- Translation ----------------------------------------------------------
    def translate(
        self, text: str, target_language: str, script: str = "native"
    ) -> TranslationResult:
        if target_language in TRANSLATE_REFUSED or target_language not in TRANSLATE_LANGUAGES:
            from .languages import LANGUAGE_NAMES

            supported = ", ".join(sorted(TRANSLATE_LANGUAGES))
            raise ToolError(
                UNSUPPORTED_LANGUAGE,
                f"Translation into {target_language!r} "
                f"({LANGUAGE_NAMES.get(target_language, 'unknown language')}) "
                "is not supported by Indic-Translate.",
                hint=f"Supported targets: {supported}.",
            )
        cache_key = self._cache_key(
            "translate", f"{target_language}:{script}:{hashlib.sha256(text.encode()).hexdigest()}"
        )
        if cache_key in self._cache:
            self.cache_hits += 1
            result = self._cache[cache_key]
            assert isinstance(result, TranslationResult)
            result.cached = True
            return result
        key = self._require_key("translate")
        # The live API has no plain /translate route (it 404s); translation
        # goes through the OpenAI-compatible chat route, like the other
        # models. Source language is inferred; only the target is named.
        resp = self._request(
            "POST",
            "/v1/chat/completions",
            key,
            json={
                "model": "indic-translate",
                "messages": [{"role": "user", "content": text}],
                "target_language_code": target_language,
                "target_script": script,
            },
        )
        body = resp.json()
        choices = body.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        translation = message.get("content", "") or ""
        truncated = bool(choices) and choices[0].get("finish_reason") == "length"
        tokens = int((body.get("usage") or {}).get("completion_tokens") or 0)
        if tokens == 0:
            tokens = _estimate_tokens(translation)
        result = TranslationResult(
            translation=translation,
            target_language=target_language,
            script=script,
            truncated=truncated,
            output_tokens=tokens,
            cost_inr=_cost_translate(tokens),
        )
        self._cache[cache_key] = result
        return result

    # -- TTS ------------------------------------------------------------------
    def synthesize(
        self, text: str, language: str, voice: str, style: str | None = None
    ) -> SynthesisResult:
        if language not in TTS_LANGUAGES:
            raise ToolError(
                UNSUPPORTED_LANGUAGE,
                f"Speech synthesis in {language!r} is not supported by Indic-Speak.",
                hint=f"Supported: {', '.join(sorted(TTS_LANGUAGES))}.",
            )
        if voice not in VOICES.get(language, {}).values() and voice not in {
            v for vs in VOICES.values() for v in vs.values()
        }:
            raise ToolError(
                "invalid_input",
                f"Unknown voice {voice!r}.",
                hint="List voices with the bodhan://voices MCP resource.",
            )
        cache_key = self._cache_key(
            "tts", f"{language}:{voice}:{style}:{hashlib.sha256(text.encode()).hexdigest()}"
        )
        if cache_key in self._cache:
            self.cache_hits += 1
            result = self._cache[cache_key]
            assert isinstance(result, SynthesisResult)
            result.cached = True
            return result
        key = self._require_key("speak")
        instructions: dict = {"lang": language}
        if style:
            instructions["style"] = style
        resp = self._request(
            "POST",
            "/v1/audio/speech",
            key,
            json={
                "model": "indic-speak",
                "input": text,
                "voice": voice,
                "instructions": json.dumps(instructions),
            },
        )
        audio = resp.content
        # 24 kHz mono PCM16 WAV: duration from the byte length.
        duration = max(0.0, (len(audio) - 44) / 2 / 24000.0) if len(audio) > 44 else 0.0
        result = SynthesisResult(
            audio_bytes=audio,
            duration_seconds=round(duration, 2),
            voice=voice,
            language=language,
            cost_inr=_cost_tts(len(text)),
        )
        self._cache[cache_key] = result
        return result

    # -- OCR ------------------------------------------------------------------
    def ocr_image(
        self, image_path: str, table_format: str = "markdown", max_tokens: int = 2048
    ) -> OCRResult:
        image_path = str(image_path)
        cache_key = self._cache_key(
            "ocr", f"{media.sha256_file(image_path)}:{table_format}:{max_tokens}"
        )
        if cache_key in self._cache:
            self.cache_hits += 1
            result = self._cache[cache_key]
            assert isinstance(result, OCRResult)
            result.cached = True
            return result
        key = self._require_key("ocr")
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        mime = "image/png" if ext == "png" else "image/jpeg"
        body = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        }
                    ],
                }
            ],
            "table_format": table_format,
            "max_tokens": max_tokens,
        }
        resp = self._request("POST", "/v1/chat/completions", key, json=body)
        payload = resp.json()
        content = payload["choices"][0]["message"]["content"]
        blocks = payload.get("blocks", [])
        result = OCRResult(
            markdown=content, blocks=blocks, cost_inr=PRICE_OCR_PER_IMAGE
        )
        self._cache[cache_key] = result
        return result


# ---------------------------------------------------------------------------
# Mock client (test-only): canned responses for the demo sample files.
# ---------------------------------------------------------------------------

MOCK_SUMMARY_EN = (
    "Lines and angles. A line is straight and goes on forever in both "
    "directions. Two lines that meet form an angle. When two lines cross, "
    "the angles opposite each other are equal. A right angle measures 90 "
    "degrees. Parallel lines never meet."
)

_MOCK_TRANSCRIPTS = {
    "voice_note_hi.wav": ("नमस्ते, मेरा नाम सुजल है। मैं आज दिल्ली जा रहा हूँ।", "hi"),
    "clip_01.wav": ("నమస్కారం, నేను హైదరాబాద్ నుండి మాట్లాడుతున్నాను.", "te"),
    "clip_02.wav": ("आज मौसम बहुत अच्छा है।", "hi"),
    "clip_03.wav": ("తెలుగు నా మాతృభాష.", "te"),
    "clip_04.wav": ("Hello, this is a test recording.", "en"),
    "clip_05.wav": ("வணக்கம், நான் சென்னையில் இருக்கிறேன்.", "ta"),
}

_MOCK_TRANSLATIONS = {
    ("Hello, my name is Sujal.", "hi"): "नमस्ते, मेरा नाम सुजल है।",
    ("Hello, my name is Sujal. Water boils at 100 degrees Celsius.", "hi"): (
        "नमस्ते, मेरा नाम सुजल है। पानी 100 डिग्री सेल्सियस पर उबलता है।"
    ),
    ("Water boils at 100 degrees Celsius.", "hi"): "पानी 100 डिग्री सेल्सियस पर उबलता है।",
    (MOCK_SUMMARY_EN, "ta"): (
        "கோடுகள் மற்றும் கோணங்கள். ஒரு கோடு நேரானது, இரு திசைகளிலும் "
        "என்றும் நீள்கிறது. இரண்டு கோடுகள் சந்திக்கும்போது ஒரு கோணம் "
        "உருவாகிறது. இரண்டு கோடுகள் கடக்கும்போது, எதிரெதிர் கோணங்கள் "
        "சமம். செங்கோணம் 90 டிகிரி அளவிடப்படுகிறது. இணை கோடுகள் "
        "ஒருபோதும் சந்திப்பதில்லை."
    ),
    ("నమస్కారం, నేను హైదరాబాద్ నుండి మాట్లాడుతున్నాను.", "en"): (
        "Hello, I am speaking from Hyderabad."
    ),
    ("తెలుగు నా మాతృభాష.", "en"): "Telugu is my mother tongue.",
    ("नमस्ते, मेरा नाम सुजल है। मैं आज दिल्ली जा रहा हूँ।", "en"): (
        "Hello, my name is Sujal. I am going to Delhi today."
    ),
    ("Name | Marks\nAsha | 85\nRavi | 92", "mr"): (
        "नाव | गुण\nआशा | ८५\nरवी | ९२"
    ),
    (
        "# Worksheet: Marks\n\n| Name | Marks |\n| --- | --- |\n"
        "| Asha | 85 |\n| Ravi | 92 |\n\nWrite the total marks.",
        "mr",
    ): (
        "# कार्यपत्रक: गुण\n\n| नाव | गुण |\n| --- | --- |\n"
        "| आशा | ८५ |\n| रवी | ९२ |\n\nएकूण गुण लिहा."
    ),
}

_MOCK_OCR = {
    "chapter.page01.png": (
        "# Lines and Angles\n\nA line is straight and goes on forever in both "
        "directions. Two lines that meet form an angle.\n\n## Types of angles\n\n"
        "- Acute angle: less than 90 degrees\n- Right angle: exactly 90 degrees\n"
        "- Obtuse angle: more than 90 degrees",
        [{"order": 0, "label": "Title", "type": "Title", "conf": 0.98,
          "text": "Lines and Angles"},
         {"order": 1, "label": "Paragraph", "type": "Text", "conf": 0.97,
          "text": "A line is straight and goes on forever in both directions."}],
    ),
    "chapter.page02.png": (
        "## Parallel lines\n\nParallel lines never meet. When a third line crosses "
        "them, the angles formed follow simple rules.\n\n$$\\angle 1 = \\angle 2$$",
        [{"order": 0, "label": "Section-title", "type": "SectionHeader",
          "conf": 0.96, "text": "Parallel lines"},
         {"order": 1, "label": "Equation", "type": "Text", "conf": 0.99,
          "text": "$$\\angle 1 = \\angle 2$$"}],
    ),
    "worksheet.png": (
        "# Worksheet: Marks\n\n| Name | Marks |\n| --- | --- |\n| Asha | 85 |\n| Ravi | 92 |\n\n"
        "Write the total marks.",
        [{"order": 0, "label": "Table", "type": "Table", "conf": 0.95,
          "text": "Name | Marks\nAsha | 85\nRavi | 92"}],
    ),
    "chapter_page.png": (
        "# Lines and Angles\n\nA line is straight and goes on forever in both "
        "directions. Two lines that meet form an angle.",
        [{"order": 0, "label": "Title", "type": "Title", "conf": 0.98,
          "text": "Lines and Angles"}],
    ),
}


@dataclass
class MockBodhanClient:
    """Canned Bodhan responses for local verification (no network, no cost).

    Transcripts, translations and OCR text are keyed by file name or exact
    input text. Anything not in the tables gets a clearly-marked
    ``[mock ...]`` response, so mock output can never pass as real.
    TTS writes a real WAV (a tone) and registers its hash, so a later
    back-transcription of that file returns the original text.
    """

    calls_made: int = 0
    cache_hits: int = 0
    _cache: dict = field(default_factory=dict)
    _tts_registry: dict = field(default_factory=dict)

    def _cached(self, key: str):
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        return None

    def transcribe(self, audio_path: str, language: str | None = None) -> TranscriptionResult:
        import os
        import re

        name = os.path.basename(str(audio_path))
        # Chunked files are named <stem>.chunkNN.wav and converted ones
        # <stem>.converted.wav; look the transcript up under the stem.
        lookup = re.sub(r"\.(chunk\d+|converted)\.wav$", ".wav", name)
        digest = media.sha256_file(audio_path)
        if digest in self._tts_registry:
            text = self._tts_registry[digest]
            return TranscriptionResult(
                text=text, language=language or "unknown",
                duration_seconds=media.wav_duration(audio_path),
                cost_inr=_cost_stt(media.wav_duration(audio_path)),
            )
        cached = self._cached(f"stt:{name}:{language}")
        if cached is not None:
            cached.cached = True
            return cached
        self.calls_made += 1
        duration = media.wav_duration(audio_path) if str(audio_path).endswith(".wav") else 5.0
        if lookup in _MOCK_TRANSCRIPTS:
            text, lang = _MOCK_TRANSCRIPTS[lookup]
        else:
            text, lang = f"[mock transcript of {name}]", language or "en"
        result = TranscriptionResult(
            text=text, language=lang, duration_seconds=duration,
            cost_inr=_cost_stt(duration),
        )
        self._cache[f"stt:{name}:{language}"] = result
        return result

    def translate(
        self, text: str, target_language: str, script: str = "native"
    ) -> TranslationResult:
        if target_language in TRANSLATE_REFUSED or target_language not in TRANSLATE_LANGUAGES:
            from .languages import LANGUAGE_NAMES

            raise ToolError(
                UNSUPPORTED_LANGUAGE,
                f"Translation into {target_language!r} "
                f"({LANGUAGE_NAMES.get(target_language, 'unknown language')}) "
                "is not supported by Indic-Translate.",
                hint=f"Supported targets: {', '.join(sorted(TRANSLATE_LANGUAGES))}.",
            )
        key = f"translate:{target_language}:{script}:{hashlib.sha256(text.encode()).hexdigest()}"
        cached = self._cached(key)
        if cached is not None:
            cached.cached = True
            return cached
        self.calls_made += 1
        translation = _MOCK_TRANSLATIONS.get((text, target_language))
        if translation is None:
            translation = f"[mock {target_language}] {text}"
        tokens = _estimate_tokens(translation)
        result = TranslationResult(
            translation=translation, target_language=target_language,
            script=script, truncated=False, output_tokens=tokens,
            cost_inr=_cost_translate(tokens),
        )
        self._cache[key] = result
        return result

    def synthesize(
        self, text: str, language: str, voice: str, style: str | None = None
    ) -> SynthesisResult:
        if language not in TTS_LANGUAGES:
            raise ToolError(
                UNSUPPORTED_LANGUAGE,
                f"Speech synthesis in {language!r} is not supported by Indic-Speak.",
                hint=f"Supported: {', '.join(sorted(TTS_LANGUAGES))}.",
            )
        key = f"tts:{language}:{voice}:{style}:{hashlib.sha256(text.encode()).hexdigest()}"
        cached = self._cached(key)
        if cached is not None:
            cached.cached = True
            return cached
        self.calls_made += 1
        duration = max(0.5, len(text) * 0.05)
        import io

        buf = io.BytesIO()
        import math
        import struct
        import wave

        n = int(duration * 24000)
        frames = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 24000)))
            for i in range(n)
        )
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(frames)
        audio = buf.getvalue()
        self._tts_registry[hashlib.sha256(audio).hexdigest()] = text
        result = SynthesisResult(
            audio_bytes=audio, duration_seconds=round(duration, 2),
            voice=voice, language=language, cost_inr=_cost_tts(len(text)),
        )
        self._cache[key] = result
        return result

    def register_audio(self, audio_bytes: bytes, text: str) -> None:
        """Register a finished WAV (e.g. joined chunks) for back-transcription."""
        self._tts_registry[hashlib.sha256(audio_bytes).hexdigest()] = text

    def ocr_image(
        self, image_path: str, table_format: str = "markdown", max_tokens: int = 2048
    ) -> OCRResult:
        import os

        name = os.path.basename(str(image_path))
        key = f"ocr:{name}:{table_format}"
        cached = self._cached(key)
        if cached is not None:
            cached.cached = True
            return cached
        self.calls_made += 1
        if name in _MOCK_OCR:
            markdown, blocks = _MOCK_OCR[name]
        else:
            markdown, blocks = f"[mock OCR of {name}]", []
        result = OCRResult(markdown=markdown, blocks=blocks, cost_inr=PRICE_OCR_PER_IMAGE)
        self._cache[key] = result
        return result


def get_client(mock: bool | None = None):
    """Build a Bodhan client. ``mock=True`` (or BODHAN_MOCK=1) returns the
    test-only mock client; otherwise the real hosted-API client."""
    if mock is None:
        mock = os.environ.get("BODHAN_MOCK", "").strip() == "1"
    if mock:
        return MockBodhanClient()
    return BodhanClient()
