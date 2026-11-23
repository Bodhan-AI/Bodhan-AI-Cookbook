"""Unified Bodhan HTTP client with retry, backoff, concurrency limiting, and mock fallback."""

from __future__ import annotations

import base64
import json
import math
import mimetypes
import os
import re
import threading
import time
import wave
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..models import OCRBlock, OCRResult, SpeakResult, TranscribeResult, TranslateResult
from .cache import GLOBAL_CACHE, ContentCache
from .pricing import (
    calculate_ocr_cost,
    calculate_speak_cost,
    calculate_transcribe_cost,
    calculate_translate_cost,
    estimate_tokens_from_text,
)

DEFAULT_BASE_URL = "https://api.bodhan.ai"
MAX_CONCURRENT_REQUESTS = 4

# Official voice catalog from bodhan_api_rules.json
VOICE_CATALOG: Dict[str, Dict[str, List[str]]] = {
    "as": {"female": ["Prastuti"], "male": ["Ankur"]},
    "bn": {"female": ["Ishita"], "male": ["Sourav"]},
    "brx": {"female": ["Gwrbw"], "male": ["Sansuma"]},
    "doi": {"female": ["Preeti"], "male": ["Sham"]},
    "gu": {"female": ["Dhara"], "male": ["Parth"]},
    "hi": {"female": ["Kavya", "Suhani"], "male": ["Amit"]},
    "kn": {"female": ["Deepika"], "male": ["Adarsh"]},
    "kok": {"female": ["Anjali"], "male": ["Sandeep"]},
    "ks": {"female": ["Zoon"], "male": ["Ishfaq"]},
    "mai": {"female": ["Vaidehi"], "male": ["Madhukar"]},
    "ml": {"female": ["Lakshmi"], "male": ["Kiran"]},
    "mni": {"female": ["Thoibi"], "male": ["Chaoba"]},
    "mr": {"female": ["Anagha"], "male": ["Chinmay"]},
    "ne": {"female": ["Srijana"], "male": ["Sagar"]},
    "or": {"female": ["Itishree"], "male": ["Akash"]},
    "pa": {"female": ["Kaur"], "male": ["Manpreet"]},
    "sa": {"female": ["Bharati"], "male": ["Aryaman"]},
    "sat": {"female": ["Phulmani"], "male": ["Sibu"]},
    "sd": {"female": ["Moomal"], "male": ["Rano"]},
    "ta": {"female": ["Anitha"], "male": ["Arun"]},
    "te": {"female": ["Sravani"], "male": ["Vamsi"]},
    "ur": {"female": ["Saba"], "male": ["Zaid"]},
    "en": {"female": ["Kavya"], "male": ["Amit"]},
}

ASR_ONLY_LANGUAGES = {"bhb", "bho"}

SUPPORTED_LANGUAGES = set(VOICE_CATALOG.keys()) | {
    "bhb", "bho", "ks-Deva", "mni-Beng", "sd-Arab"
}

TRANSLATE_LANGUAGES = SUPPORTED_LANGUAGES - ASR_ONLY_LANGUAGES

_CANONICAL_LANG_MAP: Dict[str, str] = {code.lower(): code for code in SUPPORTED_LANGUAGES}


def normalize_language_code(lang: str) -> str:
    """Normalize a language code preserving canonical casing (e.g. 'ks-Deva', 'mni-Beng', 'sd-Arab')."""
    cleaned = lang.strip()
    return _CANONICAL_LANG_MAP.get(cleaned.lower(), cleaned.lower())

LANGUAGE_CODE_TO_NAME: Dict[str, str] = {
    "as": "Assamese",
    "bhb": "Bhili",
    "bho": "Bhojpuri",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "en": "English",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "kok": "Konkani",
    "ks": "Kashmiri",
    "ks-Deva": "Kashmiri (Devanagari script)",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri",
    "mni-Beng": "Manipuri (Bengali script)",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi",
    "sd-Arab": "Sindhi (Perso-Arabic script)",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}


def _auto_load_env() -> None:
    """Automatically find and load .env file from common recipe and repository locations."""
    candidates = [
        Path.cwd() / "examples" / "bodhan_tool_agent" / ".env",
        Path.cwd() / ".env",
        Path(__file__).resolve().parent / ".env",
        Path(__file__).resolve().parents[2] / ".env",
        Path(__file__).resolve().parents[3] / "examples" / "bodhan_tool_agent" / ".env",
        Path(__file__).resolve().parents[3] / ".env",
    ]
    for cand in candidates:
        if cand.is_file():
            try:
                from dotenv import load_dotenv
                load_dotenv(cand, override=False)
            except Exception:
                pass
            try:
                for line in cand.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass
            break


class BodhanClient:
    """Client for calling Bodhan hosted models with resilience, caching, and offline mock support."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        transcribe_key: Optional[str] = None,
        translate_key: Optional[str] = None,
        speak_key: Optional[str] = None,
        ocr_key: Optional[str] = None,
        cache: Optional[ContentCache] = None,
        mock_mode: Optional[bool] = None,
        per_call_timeout: Optional[float] = None,
    ):
        _auto_load_env()
        self.base_url = (base_url or os.environ.get("BODHAN_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.per_call_timeout = per_call_timeout
        
        # Key resolution: specific key -> BODHAN_API_KEY fallback
        fallback_key = os.environ.get("BODHAN_API_KEY", "")
        self.transcribe_key = transcribe_key or os.environ.get("BODHAN_TRANSCRIBE_KEY") or fallback_key
        self.translate_key = translate_key or os.environ.get("BODHAN_TRANSLATE_KEY") or fallback_key
        self.speak_key = speak_key or os.environ.get("BODHAN_SPEAK_KEY") or fallback_key
        self.ocr_key = ocr_key or os.environ.get("BODHAN_OCR_KEY") or fallback_key

        # Determine mock mode: explicit flag or lack of keys
        has_any_key = bool(self.transcribe_key or self.translate_key or self.speak_key or self.ocr_key)
        env_mock = os.environ.get("BODHAN_MOCK", "").lower() in ("1", "true", "yes")
        self.mock_mode = mock_mode if mock_mode is not None else (env_mock or not has_any_key)

        # Use persistent disk-backed GLOBAL_CACHE for live API calls; in-memory cache for mock mode
        self.cache = cache or (ContentCache(cache_dir=None) if self.mock_mode else GLOBAL_CACHE)
        self._semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)

        # Setup requests session with automatic retries on network blips and 5xx
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _headers(self, key: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {key}",
            "User-Agent": "BodhanCookbook-Agent/1.0",
        }

    def _raise_or_extract_error(self, resp: requests.Response) -> Dict[str, Any]:
        """Parse structured Bodhan error responses: {"error": {"message", "code", "request_id"}}."""
        if resp.ok:
            return {}
        try:
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                err = data["error"]
                return {
                    "code": str(err.get("code", resp.status_code)),
                    "message": str(err.get("message", resp.text)),
                    "request_id": str(err.get("request_id", "")),
                }
        except Exception:
            pass
        return {
            "code": str(resp.status_code),
            "message": resp.text[:300],
            "request_id": "",
        }

    # -------------------------------------------------------------------------
    # Indic-Transcribe (Speech to Text)
    # -------------------------------------------------------------------------
    def transcribe(
        self,
        audio_bytes: bytes,
        duration_seconds: float,
        language: Optional[str] = None,
        file_name: str = "audio.wav",
    ) -> TranscribeResult:
        """Call indic-transcribe for audio chunk (<= 30 seconds)."""
        cache_key = self.cache.compute_hash("indic-transcribe", audio_bytes, {"language": language, "mock": self.mock_mode})
        cached_data = self.cache.get(cache_key)
        if cached_data:
            return TranscribeResult(
                text=cached_data["text"],
                language=cached_data.get("language", language),
                duration_seconds=duration_seconds,
                cost_inr=0.0,
                cached=True,
            )

        cost = calculate_transcribe_cost(duration_seconds)

        if self.mock_mode:
            # High-fidelity mock response for local testing and CI
            mock_text = self._mock_transcribe(audio_bytes, language)
            result = TranscribeResult(
                text=mock_text,
                language=language or "hi",
                duration_seconds=duration_seconds,
                cost_inr=cost,
                cached=False,
            )
            self.cache.set(cache_key, {"text": result.text, "language": result.language})
            return result

        if not self.transcribe_key:
            raise ValueError("BODHAN_TRANSCRIBE_KEY or BODHAN_API_KEY is required to call indic-transcribe.")

        data: Dict[str, Any] = {"model": "indic-transcribe"}
        if language:
            data["language"] = language

        with self._semaphore:
            resp = self.session.post(
                f"{self.base_url}/v1/audio/transcriptions",
                headers=self._headers(self.transcribe_key),
                files={"file": (file_name, audio_bytes, "audio/wav")},
                data=data,
                timeout=self.per_call_timeout or 60,
            )

        if not resp.ok:
            err = self._raise_or_extract_error(resp)
            raise RuntimeError(f"Bodhan Transcribe error ({err.get('code')}): {err.get('message')}")

        resp_json = resp.json()
        text = resp_json.get("text", "").strip()
        detected_lang = resp_json.get("language", language)

        self.cache.set(cache_key, {"text": text, "language": detected_lang})
        return TranscribeResult(
            text=text,
            language=detected_lang,
            duration_seconds=duration_seconds,
            cost_inr=cost,
            cached=False,
        )

    # -------------------------------------------------------------------------
    # Indic-Translate (Translation)
    # -------------------------------------------------------------------------
    def translate(
        self,
        text: str,
        target_language: str,
        script: str = "native",
        max_output_tokens: Optional[int] = None,
    ) -> TranslateResult:
        """Call indic-translate endpoint."""
        target_lang = normalize_language_code(target_language)
        if target_lang in ASR_ONLY_LANGUAGES:
            raise ValueError(
                f"Target language '{target_language}' is supported only by Indic-Transcribe (ASR), "
                f"not by Indic-Translate."
            )
        if target_lang not in TRANSLATE_LANGUAGES:
            raise ValueError(f"Target language '{target_language}' is not supported by Indic-Translate.")

        cache_key = self.cache.compute_hash(
            "indic-translate-v3",
            text,
            {
                "target_language": target_lang,
                "script": script,
                "max_output_tokens": max_output_tokens,
                "mock": self.mock_mode,
            },
        )
        cached_data = self.cache.get(cache_key)
        if cached_data:
            return TranslateResult(
                translation=cached_data["translation"],
                source_language=cached_data.get("source_language"),
                target_language=target_lang,
                script=script,
                output_tokens=cached_data.get("output_tokens", 0),
                cost_inr=0.0,
                cached=True,
                truncated=cached_data.get("truncated", False),
            )

        if self.mock_mode:
            mock_trans = self._mock_translate(text, target_lang, script)
            est_tokens = estimate_tokens_from_text(mock_trans)
            cost = calculate_translate_cost(est_tokens)
            result = TranslateResult(
                translation=mock_trans,
                source_language="auto",
                target_language=target_lang,
                script=script,
                output_tokens=est_tokens,
                cost_inr=cost,
                cached=False,
                truncated=False,
            )
            self.cache.set(
                cache_key,
                {
                    "translation": result.translation,
                    "source_language": result.source_language,
                    "output_tokens": result.output_tokens,
                    "truncated": result.truncated,
                },
            )
            return result

        if not self.translate_key:
            raise ValueError("BODHAN_TRANSLATE_KEY or BODHAN_API_KEY is required to call indic-translate.")

        headers = {**self._headers(self.translate_key), "Content-Type": "application/json"}
        with self._semaphore:
            # 1. Try OpenAI-compatible chat completions route first (supported by api.bodhan.ai/v1)
            target_name = LANGUAGE_CODE_TO_NAME.get(target_lang, target_lang)
            if script == "roman":
                script_hint = " in Roman (Latin) script"
            elif script == "codemix":
                script_hint = " in natural code-mixed style"
            else:
                script_hint = ""
            prompt = f"Translate the following text into {target_name}{script_hint}:\n\n{text}"
            chat_body = {
                "model": "indic-translate",
                "messages": [{"role": "user", "content": prompt}],
            }
            if max_output_tokens:
                chat_body["max_tokens"] = max_output_tokens

            resp = self.session.post(
                f"{self.base_url}/v1/chat/completions",
                headers=headers,
                json=chat_body,
                timeout=self.per_call_timeout or 60,
            )
            # 2. If chat/completions returns 404 or 400, fall back to /translate JSON endpoint
            if resp.status_code in (404, 400):
                body: Dict[str, Any] = {
                    "text": text,
                    "target_language": target_lang,
                    "script": script,
                }
                if max_output_tokens:
                    body["max_output_tokens"] = max_output_tokens
                resp = self.session.post(
                    f"{self.base_url}/translate",
                    headers=headers,
                    json=body,
                    timeout=self.per_call_timeout or 60,
                )

        if not resp.ok:
            err = self._raise_or_extract_error(resp)
            raise RuntimeError(f"Bodhan Translate error ({err.get('code')}): {err.get('message')}")

        data = resp.json()
        translation = ""
        output_tokens = 0
        truncated = False
        source_lang = None

        if "choices" in data and len(data["choices"]) > 0:
            translation = data["choices"][0]["message"].get("content", "").strip()
            # Strip occasional conversational preamble if emitted by chat-formatted translation endpoint
            translation = re.sub(
                r"^Here is the translation[^\n]*:\s*\n+",
                "",
                translation,
                flags=re.IGNORECASE,
            ).strip()
            usage = data.get("usage", {})
            output_tokens = usage.get("completion_tokens", estimate_tokens_from_text(translation))
            truncated = data["choices"][0].get("finish_reason") == "length"
        else:
            translation = data.get("translation", "").strip()
            usage = data.get("usage", {})
            output_tokens = usage.get("output_tokens", estimate_tokens_from_text(translation))
            truncated = bool(data.get("truncated", False))
            source_lang = data.get("source_language")

        cost = calculate_translate_cost(output_tokens)

        self.cache.set(
            cache_key,
            {
                "translation": translation,
                "source_language": data.get("source_language"),
                "output_tokens": output_tokens,
                "truncated": truncated,
            },
        )

        return TranslateResult(
            translation=translation,
            source_language=data.get("source_language"),
            target_language=target_lang,
            script=script,
            output_tokens=output_tokens,
            cost_inr=cost,
            cached=False,
            truncated=truncated,
        )

    # -------------------------------------------------------------------------
    # Indic-Speak (Text to Speech)
    # -------------------------------------------------------------------------
    def speak(
        self,
        text: str,
        voice: str,
        language: str,
        style: Optional[str] = None,
        output_path: Optional[str] = None,
        bypass_cache: bool = False,
    ) -> SpeakResult:
        """Call indic-speak for short sentence chunk (<= 30s)."""
        lang = normalize_language_code(language)
        char_count = len(text)
        cost = calculate_speak_cost(char_count)

        cache_key = self.cache.compute_hash(
            "indic-speak", text, {"voice": voice, "language": lang, "style": style, "mock": self.mock_mode}
        )
        cached_data = None if bypass_cache else self.cache.get(cache_key)
        
        target_path = Path(output_path) if output_path else Path("speech_out.wav")
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if cached_data and "audio_b64" in cached_data:
            audio_bytes = base64.b64decode(cached_data["audio_b64"])
            target_path.write_bytes(audio_bytes)
            duration = self._wav_duration(audio_bytes)
            return SpeakResult(
                audio_path=str(target_path.resolve()),
                voice=voice,
                language=lang,
                style=style,
                character_count=char_count,
                duration_seconds=duration,
                cost_inr=0.0,
                cached=True,
            )

        if self.mock_mode:
            audio_bytes = self._generate_mock_wav(duration_seconds=max(1.0, char_count * 0.08))
            target_path.write_bytes(audio_bytes)
            duration = self._wav_duration(audio_bytes)
            self.cache.set(
                cache_key,
                {
                    "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
                    "voice": voice,
                    "language": lang,
                },
            )
            return SpeakResult(
                audio_path=str(target_path.resolve()),
                voice=voice,
                language=lang,
                style=style,
                character_count=char_count,
                duration_seconds=duration,
                cost_inr=cost,
                cached=False,
            )

        if not self.speak_key:
            raise ValueError("BODHAN_SPEAK_KEY or BODHAN_API_KEY is required to call indic-speak.")

        instructions = {"lang": lang}
        if style:
            instructions["style"] = style

        body = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "instructions": json.dumps(instructions, ensure_ascii=False),
        }

        headers = {**self._headers(self.speak_key), "Content-Type": "application/json"}
        with self._semaphore:
            resp = self.session.post(
                f"{self.base_url}/v1/audio/speech",
                headers=headers,
                json=body,
                timeout=self.per_call_timeout or 120,
            )

        if not resp.ok:
            err = self._raise_or_extract_error(resp)
            raise RuntimeError(f"Bodhan Speak error ({err.get('code')}): {err.get('message')}")

        audio_bytes = resp.content
        target_path.write_bytes(audio_bytes)
        duration = self._wav_duration(audio_bytes)

        self.cache.set(
            cache_key,
            {
                "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
                "voice": voice,
                "language": lang,
            },
        )

        return SpeakResult(
            audio_path=str(target_path.resolve()),
            voice=voice,
            language=lang,
            style=style,
            character_count=char_count,
            duration_seconds=duration,
            cost_inr=cost,
            cached=False,
        )

    # -------------------------------------------------------------------------
    # Indic-OCR (Document OCR)
    # -------------------------------------------------------------------------
    def ocr_page(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        table_format: str = "markdown",
    ) -> OCRResult:
        """Call indic-ocr for a single image/page."""
        cost = calculate_ocr_cost(1)
        cache_key = self.cache.compute_hash("indic-ocr", image_bytes, {"table_format": table_format, "mock": self.mock_mode})
        cached_data = self.cache.get(cache_key)

        if cached_data:
            blocks = [OCRBlock(**b) for b in cached_data.get("blocks", [])]
            return OCRResult(
                markdown=cached_data["markdown"],
                page_count=1,
                blocks=blocks,
                cost_inr=0.0,
                cached=True,
            )

        if self.mock_mode:
            mock_md, mock_blocks = self._mock_ocr(image_bytes)
            self.cache.set(
                cache_key,
                {"markdown": mock_md, "blocks": [b.model_dump() for b in mock_blocks]},
            )
            return OCRResult(
                markdown=mock_md,
                page_count=1,
                blocks=mock_blocks,
                cost_inr=cost,
                cached=False,
            )

        if not self.ocr_key:
            raise ValueError("BODHAN_OCR_KEY or BODHAN_API_KEY is required to call indic-ocr.")

        b64 = base64.b64encode(image_bytes).decode("ascii")
        body = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                        }
                    ],
                }
            ],
            "table_format": table_format,
        }

        headers = {**self._headers(self.ocr_key), "Content-Type": "application/json"}
        with self._semaphore:
            resp = self.session.post(
                f"{self.base_url}/v1/chat/completions",
                headers=headers,
                json=body,
                timeout=self.per_call_timeout or 180,
            )

        if not resp.ok:
            err = self._raise_or_extract_error(resp)
            raise RuntimeError(f"Bodhan OCR error ({err.get('code')}): {err.get('message')}")

        data = resp.json()
        markdown = ""
        try:
            markdown = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            pass

        raw_blocks = data.get("blocks", [])
        blocks = []
        for i, b in enumerate(raw_blocks):
            blocks.append(
                OCRBlock(
                    order=b.get("order", i),
                    label=b.get("label", "paragraph"),
                    bbox_xyxy=b.get("bbox_xyxy", [0, 0, 100, 100]),
                    conf=float(b.get("conf", 0.95)),
                    text=b.get("text", ""),
                )
            )

        self.cache.set(
            cache_key,
            {"markdown": markdown, "blocks": [b.model_dump() for b in blocks]},
        )

        return OCRResult(
            markdown=markdown,
            page_count=1,
            blocks=blocks,
            cost_inr=cost,
            cached=False,
        )

    # -------------------------------------------------------------------------
    # Helper & Mock Simulators
    # -------------------------------------------------------------------------
    def _wav_duration(self, audio_bytes: bytes) -> float:
        """Calculate duration of WAV bytes using standard wave library."""
        try:
            import io
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return round(frames / float(rate), 2)
        except Exception:
            return 2.0

    def _generate_mock_wav(self, duration_seconds: float = 2.0) -> bytes:
        """Synthesize a silent/low-sine PCM16 24kHz mono WAV in pure Python."""
        import io
        buf = io.BytesIO()
        sample_rate = 24000
        num_samples = int(sample_rate * duration_seconds)
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            # Gentle 440 Hz low-volume sine so file is valid audio
            samples = []
            for i in range(num_samples):
                val = int(1000 * math.sin(2 * math.pi * 440 * (i / sample_rate)))
                samples.append(struct.pack("<h", val))
            wf.writeframes(b"".join(samples))
        return buf.getvalue()

    def _mock_transcribe(self, audio_bytes: bytes, language: Optional[str]) -> str:
        """Deterministic mock transcript."""
        lang = language or "hi"
        samples = {
            "hi": "यह बोधन एआई कुकबुक का एक ऑडियो नमूना है।",
            "ta": "இது போதன் ஏஐ குக்புக்கின் குரல் பதிவு ஆகும்.",
            "te": "ఇది బోధన్ ఏఐ కుక్‌బుక్ యొక్క ఆడియో నమూనా.",
            "bn": "এটি বোধন এআই কুকবুকের একটি অডিও নমুনা।",
            "mr": "हा बोधन एआय कूकबुकचा ऑडिओ नमुना आहे.",
            "en": "This is a sample audio recording for the Bodhan AI cookbook.",
        }
        return samples.get(lang, "यह बोधन एआई परीक्षण प्रतिलेखन है।")

    def _mock_translate(self, text: str, target_lang: str, script: str) -> str:
        """Deterministic mock translation."""
        translations = {
            "ta": "இது தமிழில் மொழிபெயர்க்கப்பட்ட உரை.",
            "te": "ఇది తెలుగులోకి అనువదించబడిన వచనం.",
            "hi": "यह हिंदी में अनुवादित पाठ है।",
            "mr": "हा मराठीत अनुवादित मजकूर आहे.",
            "bn": "এটি বাংলায় অনুবাদিত পাঠ্য।",
            "en": "This is the translated text in English.",
            "gu": "આ ગુજરાતીમાં અનુવાદિત ટેક્સ્ટ છે.",
            "kn": "ಇದು ಕನ್ನಡಕ್ಕೆ ಅನುವಾದಿಸಲಾದ ಪಠ್ಯ.",
            "ml": "ഇത് മലയാളത്തിലേക്ക് വിവർത്തനം ചെയ്ത വാചകം.",
            "pa": "ਇਹ ਪੰਜਾਬੀ ਵਿੱਚ ਅਨੁਵਾਦ ਕੀਤਾ ਪਾਠ ਹੈ।",
            "ur": "یہ اردو میں ترجمہ شدہ متن ہے۔",
        }
        if target_lang == "en":
            # If translating worksheet table or text
            if "|" in text:
                return text  # keep table structure
            return "This chapter covers the foundational principles and key summaries in English."
        return translations.get(target_lang, f"[{target_lang}]: {text}")

    def _mock_ocr(self, image_bytes: bytes) -> Tuple[str, List[OCRBlock]]:
        """Deterministic mock OCR output."""
        md = """# कार्यपत्रिका (Worksheet)

| क्रमांक | प्रश्न (Question) | गुण (Marks) |
|---|---|---|
| 1 | प्रकाशसंश्लेषण म्हणजे काय? | 5 |
| 2 | पाण्याचे रासायनिक सूत्र लिहा. | 2 |
| 3 | मानवी हृदयाची आकृती काढा. | 5 |

सूचना: सर्व प्रश्न सोडवणे अनिवार्य आहे."""
        blocks = [
            OCRBlock(order=0, label="title", bbox_xyxy=[50, 40, 400, 90], conf=0.98, text="कार्यपत्रिका (Worksheet)"),
            OCRBlock(order=1, label="table", bbox_xyxy=[50, 100, 800, 350], conf=0.96, text="| क्रमांक | प्रश्न | गुण |"),
            OCRBlock(order=2, label="paragraph", bbox_xyxy=[50, 370, 600, 410], conf=0.95, text="सूचना: सर्व प्रश्न सोडवणे अनिवार्य आहे."),
        ]
        return md, blocks
