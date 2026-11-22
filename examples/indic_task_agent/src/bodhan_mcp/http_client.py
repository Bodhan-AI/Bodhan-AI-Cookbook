"""HTTP adapter for the Bodhan endpoints exercised by this recipe."""

from __future__ import annotations

import base64
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .client import OcrResult, SpeechResult, TranscribeResult, TranslateResult


class BodhanApiError(RuntimeError):
    def __init__(self, status_code: int, message: str, request_id: str | None = None):
        self.status_code = status_code
        self.request_id = request_id
        suffix = f" (request_id={request_id})" if request_id else ""
        super().__init__(f"Bodhan API returned HTTP {status_code}: {message}{suffix}")


class HttpBodhanClient:
    def __init__(
        self,
        transcribe_key: str | None = None,
        translate_key: str | None = None,
        speak_key: str | None = None,
        ocr_key: str | None = None,
        base_url: str = "https://api.bodhan.ai",
        timeout: float = 60,
        client: httpx.Client | None = None,
    ) -> None:
        self._keys = {
            "transcribe": transcribe_key,
            "translate": translate_key,
            "speak": speak_key,
            "ocr": ocr_key,
        }
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout))
        self._request_slots = threading.BoundedSemaphore(2)

    @classmethod
    def from_env(cls) -> HttpBodhanClient:
        return cls(
            transcribe_key=os.environ.get("BODHAN_TRANSCRIBE_KEY"),
            translate_key=os.environ.get("BODHAN_TRANSLATE_KEY"),
            speak_key=os.environ.get("BODHAN_SPEAK_KEY"),
            ocr_key=os.environ.get("BODHAN_OCR_KEY"),
            base_url=os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai"),
        )

    def _headers(self, capability: str) -> dict[str, str]:
        key = self._keys[capability]
        if not key:
            variable = {
                "transcribe": "BODHAN_TRANSCRIBE_KEY",
                "translate": "BODHAN_TRANSLATE_KEY",
                "speak": "BODHAN_SPEAK_KEY",
                "ocr": "BODHAN_OCR_KEY",
            }[capability]
            raise RuntimeError(f"Missing {variable}; set it in the environment before calling this tool.")
        return {"Authorization": f"Bearer {key}"}

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        request_id = response.headers.get("x-request-id")
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("detail") or str(error)
            else:
                message = body.get("detail") or str(body)
        else:
            message = str(body)
        raise BodhanApiError(response.status_code, str(message)[:1000], request_id)

    def _post(self, url: str, capability: str, **kwargs: Any) -> httpx.Response:
        with self._request_slots:
            for attempt in range(3):
                response = self._client.post(
                    url,
                    headers=self._headers(capability),
                    **kwargs,
                )
                if response.status_code != 429 or attempt == 2:
                    return response
                retry_after = response.headers.get("retry-after")
                try:
                    delay = min(float(retry_after), 10.0) if retry_after else 0.5 * (2**attempt)
                except ValueError:
                    delay = 0.5 * (2**attempt)
                time.sleep(delay)
        raise RuntimeError("Request retry loop ended unexpectedly.")

    def translate(
        self,
        text: str,
        target_language: str,
        source_language: str | None = None,
        script: str = "native",
        max_output_tokens: int | None = None,
    ) -> TranslateResult:
        body: dict[str, Any] = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "target_language": target_language,
        }
        if source_language:
            body["source_language"] = source_language
        if script != "native":
            body["script"] = script
        if max_output_tokens is not None:
            if max_output_tokens <= 0:
                raise ValueError("max_output_tokens must be greater than zero.")
            body["max_output_tokens"] = max_output_tokens
        response = self._post(
            f"{self.base_url}/v1/chat/completions",
            "translate",
            json=body,
        )
        self._raise_for_error(response)
        try:
            payload = response.json()
            choice = payload["choices"][0]
            translated = choice["message"]["content"]
            if not isinstance(translated, str) or not translated.strip():
                raise ValueError("The response did not contain translated text.")
            return TranslateResult(
                text=translated.strip(),
                truncated=choice.get("finish_reason") == "length",
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid Indic-Translate response: {exc}") from exc

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        style: str | None = None,
    ) -> SpeechResult:
        import json

        instructions: dict[str, str] = {"lang": language}
        if style:
            instructions["style"] = style
        body: dict[str, Any] = {
            "model": "indic-speak",
            "input": text,
            "instructions": json.dumps(instructions, ensure_ascii=False),
        }
        if voice:
            body["voice"] = voice
        response = self._post(
            f"{self.base_url}/v1/audio/speech",
            "speak",
            json=body,
        )
        self._raise_for_error(response)
        audio = response.content
        if not audio:
            raise RuntimeError("Indic-Speak returned an empty audio response.")
        if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
            audio_format = "wav"
        elif audio.startswith(b"ID3") or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
            audio_format = "mp3"
        else:
            raise RuntimeError(
                f"Indic-Speak returned an unrecognized audio payload "
                f"(content-type={response.headers.get('content-type', 'unknown')})."
            )
        return SpeechResult(audio_bytes=audio, format=audio_format)

    def transcribe(self, audio_path: str | Path, language: str | None = None) -> TranscribeResult:
        source = Path(audio_path)
        with source.open("rb") as audio_file:
            response = self._post(
                f"{self.base_url}/v1/audio/transcriptions",
                "transcribe",
                files={"file": (source.name, audio_file, "audio/wav")},
                data={"model": "indic-transcribe", **({"language": language} if language else {})},
            )
        self._raise_for_error(response)
        try:
            text = response.json()["text"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"Invalid Indic-Transcribe response: {exc}") from exc
        if not isinstance(text, str):
            raise RuntimeError("Invalid Indic-Transcribe response: 'text' must be a string.")
        return TranscribeResult(text=text)

    def ocr(self, image_path: str | Path) -> OcrResult:
        import mimetypes

        source = Path(image_path)
        mime_type = mimetypes.guess_type(source.name)[0] or "image/png"
        image_data = base64.b64encode(source.read_bytes()).decode("ascii")
        response = self._post(
            f"{self.base_url}/v1/chat/completions",
            "ocr",
            json={
                "model": "indic-ocr",
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_data}"},
                    }],
                }],
            },
        )
        self._raise_for_error(response)
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Invalid Indic-OCR response: {exc}") from exc
        if isinstance(content, str):
            return OcrResult(markdown=content)
        if isinstance(content, list):
            markdown = "\n".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            ).strip()
            return OcrResult(markdown=markdown)
        raise RuntimeError("Invalid Indic-OCR response: message content must be text.")