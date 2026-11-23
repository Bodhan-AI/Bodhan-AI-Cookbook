from __future__ import annotations

from __future__ import annotations

import io
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_bodhan, get_current_user, get_db, get_settings_dep
from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import redact
from app.models import User
from app.services.audio.processing import ffmpeg_version
from app.services.audio.wav import wav_duration_seconds
from app.services.bodhan import ocr, speech, transcription, translation, transliteration
from app.services.bodhan.exceptions import BodhanError, BodhanNotConfigured

router = APIRouter(tags=["health"])

SERVICE_MAP = {
    "indic-speak": "speak",
    "indic-transcribe": "transcribe",
    "indic-translate": "translate",
    "indic-transliterate": "transliterate",
    "indic-ocr": "ocr",
}
REVERSE_SERVICE_MAP = {v: k for k, v in SERVICE_MAP.items()}

_verification_cache: dict[str, dict[str, Any]] = {}


def _get_test_png() -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (600, 150), "white")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=36)
        except TypeError:
            font = ImageFont.load_default()
        d.text((20, 50), "HERITAGELENS HEALTH CHECK", fill="black", font=font)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        # 1x1 white PNG fallback
        return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"


def _categorize_error(exc: Exception) -> str:
    if isinstance(exc, BodhanNotConfigured):
        return "missing_api_key"
    if isinstance(exc, BodhanError):
        code = (exc.code or "").lower()
        if exc.status_code == 401 or "auth" in code:
            return "invalid_credentials"
        if exc.status_code == 403 or "forbidden" in code:
            return "permission_denied"
        if exc.status_code == 415 or "format" in code or "media" in code:
            return "unsupported_format"
        if exc.status_code == 413 or "too_large" in code:
            return "payload_too_large"
        if exc.status_code == 429 or "rate" in code:
            return "rate_limited"
        if exc.status_code == 400:
            return "bad_request"
        if exc.status_code and exc.status_code >= 500:
            return "upstream_server_error"
        if "timeout" in str(type(exc)).lower() or "timeout" in str(exc).lower():
            return "timeout"
        if "network" in str(type(exc)).lower() or "connect" in str(exc).lower():
            return "network_error"
    return "unknown_error"


async def _execute_check(full_name: str, short_name: str, client, settings) -> tuple[str, str, int, str]:
    """Returns (status, detail, http_status, response_type)."""
    if short_name == "speak":
        r = await speech.synthesize_speech(client, "నమస్కారం, మా గ్రామానికి స్వాగతం.", "te", "Sravani")
        return ("pass", f"Generated WAV audio: {wav_duration_seconds(r.wav_bytes):.2f}s ({len(r.wav_bytes)} bytes), PCM16 24kHz mono", 200, "audio/wav")
    elif short_name == "transcribe":
        sp = await speech.synthesize_speech(client, "నమస్కారం", "te", "Sravani")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(sp.wav_bytes)
            tmp_path = f.name
        try:
            r = await transcription.transcribe_file(client, tmp_path, "te", ffmpeg_path=settings.ffmpeg_path)
            return ("pass", f"Recognised speech: {r.text!r} ({len(r.segments)} segment)", 200, "application/json")
        finally:
            import os
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    elif short_name == "translate":
        r = await translation.translate_text(client, "నమస్కారం, మా గ్రామానికి స్వాగతం.", "en")
        return ("pass", f"Translated: {r.translation!r} (truncated: {r.truncated})", 200, "application/json")
    elif short_name == "transliterate":
        r = await transliteration.transliterate_text(client, "నమస్కారం", "te", script="roman")
        return ("pass", f"Transliterated: {r.converted_text!r} (script: roman)", 200, "application/json")
    elif short_name == "ocr":
        img = _get_test_png()
        r = await ocr.ocr_document(client, img)
        page = r.pages[0]
        if not page.ok:
            raise RuntimeError(page.error)
        return ("pass", f"Extracted {len(page.blocks)} block(s): {page.markdown[:60]!r}", 200, "application/json")
    raise ValueError(f"Unknown service: {full_name}")


async def _verify_service(full_name: str, client, settings) -> dict[str, Any]:
    short_name = SERVICE_MAP.get(full_name) or full_name
    canon_name = REVERSE_SERVICE_MAP.get(short_name, full_name)
    is_conf = settings.key_for(short_name) is not None
    now_iso = datetime.now(timezone.utc).isoformat()

    if not is_conf:
        entry = {
            "service": canon_name,
            "configured": False,
            "last_verified_at": now_iso,
            "last_result": "fail",
            "status": "not_configured",
            "error_category": "missing_api_key",
            "diagnostic_detail": "No API key configured for this service. Set the environment variable in backend/.env.",
            "http_status": None,
            "response_type": None,
        }
        _verification_cache[canon_name] = entry
        return entry

    started = time.monotonic()
    try:
        res_status, detail, http_status, resp_type = await _execute_check(canon_name, short_name, client, settings)
        entry = {
            "service": canon_name,
            "configured": True,
            "last_verified_at": now_iso,
            "last_result": "pass",
            "status": "operational",
            "error_category": None,
            "diagnostic_detail": detail,
            "http_status": http_status,
            "response_type": resp_type,
            "response_time_ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        cat = _categorize_error(exc)
        code = getattr(exc, "status_code", None)
        entry = {
            "service": canon_name,
            "configured": True,
            "last_verified_at": now_iso,
            "last_result": "fail",
            "status": "error",
            "error_category": cat,
            "diagnostic_detail": redact(str(exc)),
            "http_status": code,
            "response_type": "error",
            "response_time_ms": round((time.monotonic() - started) * 1000),
        }
    _verification_cache[canon_name] = entry
    return entry


@router.get("/health/bodhan")
def get_bodhan_health(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
):
    """Administrator health report for all five Bodhan services."""
    services = []
    for full_name, short_name in SERVICE_MAP.items():
        is_conf = settings.key_for(short_name) is not None
        cached = _verification_cache.get(full_name)
        if cached:
            entry = dict(cached)
            entry["configured"] = is_conf
        else:
            entry = {
                "service": full_name,
                "configured": is_conf,
                "last_verified_at": None,
                "last_result": "untested",
                "status": "operational" if is_conf else "not_configured",
                "error_category": None if is_conf else "missing_api_key",
                "diagnostic_detail": "Ready for live testing. Click 'Test Connection'." if is_conf else "API key is not configured.",
                "http_status": None,
                "response_type": None,
            }
        services.append(entry)
    return {"services": services, "checked_at": datetime.now(timezone.utc).isoformat()}


@router.post("/health/bodhan/test/{service_name}")
async def test_bodhan_service(
    service_name: str,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
    client=Depends(get_bodhan),
):
    """Run a live verification test for a specific Bodhan service."""
    full_name = REVERSE_SERVICE_MAP.get(service_name) or service_name
    if full_name not in SERVICE_MAP:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not recognised.")
    return await _verify_service(full_name, client, settings)


@router.post("/health/bodhan/test-all")
async def test_all_bodhan_services(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
    client=Depends(get_bodhan),
):
    """Run live verification tests for all five Bodhan services."""
    results = []
    for full_name in SERVICE_MAP:
        res = await _verify_service(full_name, client, settings)
        results.append(res)
    return {"services": results, "checked_at": datetime.now(timezone.utc).isoformat()}


@router.get("/health")
def health(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "error"
    version = ffmpeg_version(settings.ffmpeg_path)
    return {
        "status": "ok" if database == "ok" else "degraded",
        "app": settings.app_name,
        "environment": settings.app_env,
        "database": database,
        "ffmpeg": {"available": version is not None, "version": version},
        "bodhan": {
            "keys_configured": settings.configured_services(),
            "note": "'configured' only means a key is present. Run scripts/verify_bodhan.py to test the live services.",
        },
    }
