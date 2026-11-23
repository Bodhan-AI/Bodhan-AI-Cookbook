from __future__ import annotations

import importlib.util

from fastapi import APIRouter, Depends

from app.api.deps import get_settings_dep
from app.core.config import Settings
from app.services.audio.processing import ffmpeg_version

router = APIRouter(prefix="/config", tags=["config"])
NAMES = {"transcribe": "indic-transcribe", "translate": "indic-translate",
         "transliterate": "indic-transliterate", "ocr": "indic-ocr", "speak": "indic-speak"}


@router.get("/status")
def config_status(settings: Settings = Depends(get_settings_dep)):
    """Which services have a key configured. Never returns any key, prefix, suffix or length."""
    services = {}
    for key, name in NAMES.items():
        source = settings.key_source(key)
        services[name] = {"configured": source is not None, "key_source": source}
    configured = [v["configured"] for v in services.values()]
    ffmpeg = ffmpeg_version(settings.ffmpeg_path)
    return {
        "environment": settings.app_env,
        "services": services,
        "all_configured": all(configured),
        "none_configured": not any(configured),
        "ffmpeg": {"available": ffmpeg is not None},
        "pdf_support": any(importlib.util.find_spec(m) is not None for m in ("pymupdf", "fitz")),
        "note": "'configured' means a key is present, not that Bodhan accepted it. Use scripts/verify_bodhan.py.",
    }
