"""Thin wrappers around the four Bodhan Indic models."""

import base64
import json
from typing import Any

from bodhan_mcp.clients.base import BaseBodhanClient


class BodhanClient:
    """High-level client providing methods for the four Bodhan models."""

    def __init__(self, base_client: BaseBodhanClient | None = None):
        self.client = base_client or BaseBodhanClient()

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "chunk.wav",
        language_hint: str | None = None,
        duration_s: float = 0.0,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        """Transcribe an audio snippet using Indic-Transcribe."""
        files = {
            "file": (filename, audio_bytes, "audio/wav"),
        }
        data: dict[str, Any] = {
            "model": "indic-transcribe",
        }
        if language_hint:
            data["language"] = language_hint

        return await self.client.call(
            model="indic-transcribe",
            endpoint="/audio/transcriptions",
            files=files,
            data=data,
            timeout_s=timeout_s,
            cost_metrics={"duration_s": duration_s},
        )

    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        """Translate text using Indic-Translate."""
        payload = {
            "model": "indic-translate",
            "messages": [
                {"role": "user", "content": text},
            ],
            "source_language": source_lang,
            "target_language": target_lang,
        }
        # Approximate output tokens as input chars / 3 for cost estimate if usage not returned yet
        estimated_output_tokens = max(1, len(text) // 3)
        res = await self.client.call(
            model="indic-translate",
            endpoint="/chat/completions",
            payload=payload,
            timeout_s=timeout_s,
            cost_metrics={"output_tokens": estimated_output_tokens},
        )
        return res

    async def speak(
        self,
        text: str,
        language: str,
        voice_id: str,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        """Synthesize speech using Indic-Speak."""
        voice_lower = voice_id.lower()
        if "ankur" in voice_lower or ("male" in voice_lower and "female" not in voice_lower):
            voice_name = "Ankur"
        else:
            voice_name = "Kavya"

        instructions = json.dumps({"lang": language, "style": "news"})
        payload = {
            "model": "indic-speak",
            "input": text,
            "voice": voice_name,
            "instructions": instructions,
        }
        res = await self.client.call(
            model="indic-speak",
            endpoint="/audio/speech",
            payload=payload,
            timeout_s=timeout_s,
            cost_metrics={"chars": len(text)},
        )
        if isinstance(res.get("data"), dict) and "audio_bytes" in res["data"]:
            res["audio_bytes"] = res["data"]["audio_bytes"]
        return res

    async def ocr(
        self,
        image_bytes: bytes,
        filename: str = "page.png",
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        """Extract markdown from an image using Indic-OCR via chat/completions."""
        b64_img = base64.b64encode(image_bytes).decode("utf-8")
        payload = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all text and markdown tables from this image."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                    ],
                }
            ],
        }
        res = await self.client.call(
            model="indic-ocr",
            endpoint="/chat/completions",
            payload=payload,
            timeout_s=timeout_s,
            cost_metrics={"pages": 1},
        )
        # Normalize response so consumers can access markdown or text directly
        data = res.get("data", {})
        if isinstance(data, dict):
            choices = data.get("choices", [])
            if choices and isinstance(choices[0], dict):
                content = choices[0].get("message", {}).get("content", "")
                data["markdown"] = content
                data["text"] = content
        return res
