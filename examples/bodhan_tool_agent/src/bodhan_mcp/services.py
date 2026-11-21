"""One function per Bodhan endpoint: cache -> budget reserve -> HTTP (retries) -> settle -> cache.

Every paid call in the project goes through here, so the 'no repeated call' and 'never exceed budget'
guarantees live in one place. Each function returns (value, cost_inr).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from bodhan_core import ledger as L
from bodhan_core.cache import make_key
from bodhan_core.errors import ToolError
from bodhan_core.runtime import Runtime


def stt_window(rt: Runtime, wav: bytes, seconds: float, language: str | None) -> tuple[dict[str, Any], float]:
    key = make_key("indic-transcribe", wav, language)
    if (hit := rt.cache.get_json(key)) is not None:
        rt.ledger.note_cache_hit()
        return hit, 0.0
    est = rt.ledger.reserve(L.est_transcribe(seconds))
    try:
        data = {"model": "indic-transcribe"}
        if language:
            data["language"] = language
        resp = rt.client.post_multipart("indic-transcribe", "/v1/audio/transcriptions", {"file": ("chunk.wav", wav, "audio/wav")}, data)
        body = resp.json()
    except Exception:
        rt.ledger.release(est)
        raise
    rt.ledger.settle("indic-transcribe", est)
    out = {"text": (body.get("text") or "").strip(), "language": body.get("language")}
    rt.cache.put_json(key, out)
    return out, est


def translate_chunk(rt: Runtime, text: str, target: str, script: str | None = None) -> tuple[str, float]:
    key = make_key("indic-translate", text, target, script)
    if (hit := rt.cache.get_json(key)) is not None:
        rt.ledger.note_cache_hit()
        return hit["translation"], 0.0
    est = rt.ledger.reserve(L.est_translate(max(len(text) * 2, 40)))  # worst case: output ~2x input chars
    try:
        body: dict[str, Any] = {"text": text, "target_language": target}
        if script:
            body["script"] = script
        data = rt.client.post_json("indic-translate", "/translate", body).json()
        translation = data.get("translation")
        if not isinstance(translation, str):
            raise ToolError("bad_response", "Indic-Translate returned no 'translation' field.", "Retry later.", True)
    except Exception:
        rt.ledger.release(est)
        raise
    tokens = (data.get("usage") or {}).get("output_tokens")
    actual = tokens / 10_000 * L.INR_PER_10K_OUTPUT_TOKENS if isinstance(tokens, (int, float)) else L.est_translate(len(translation))
    rt.ledger.settle("indic-translate", est, min(actual, est))
    rt.cache.put_json(key, {"translation": translation})
    return translation, min(actual, est)


def ocr_image(rt: Runtime, image: bytes, mime: str, table_format: str) -> tuple[dict[str, Any], float]:
    key = make_key("indic-ocr", image, table_format)
    if (hit := rt.cache.get_json(key)) is not None:
        rt.ledger.note_cache_hit()
        return hit, 0.0
    est = rt.ledger.reserve(L.est_ocr(1))
    try:
        b64 = base64.b64encode(image).decode()  # base64 goes to the Bodhan API only, never into LLM context
        body = {
            "model": "indic-ocr",
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
            "table_format": table_format,
        }
        data = rt.client.post_json("indic-ocr", "/v1/chat/completions", body).json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        rt.ledger.release(est)
        raise ToolError("bad_response", f"Unexpected Indic-OCR response shape ({e!r}).", "Retry later.", True) from e
    except Exception:
        rt.ledger.release(est)
        raise
    rt.ledger.settle("indic-ocr", est)
    out = {"markdown": content or "", "blocks": len(data.get("blocks") or [])}
    rt.cache.put_json(key, out)
    return out, est


def tts_chunk(rt: Runtime, text: str, voice: str, lang: str, style: str | None) -> tuple[bytes, float]:
    key = make_key("indic-speak", text, voice, lang, style)
    if (hit := rt.cache.get_blob(key)) is not None:
        rt.ledger.note_cache_hit()
        return hit, 0.0
    est = rt.ledger.reserve(L.est_speak(len(text)))
    try:
        instr: dict[str, str] = {"lang": lang}
        if style:
            instr["style"] = style
        resp = rt.client.post_json(
            "indic-speak", "/v1/audio/speech",
            {"model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps(instr)},
        )
        audio = resp.content
    except Exception:
        rt.ledger.release(est)
        raise
    rt.ledger.settle("indic-speak", est)
    rt.cache.put_blob(key, audio)
    return audio, est
