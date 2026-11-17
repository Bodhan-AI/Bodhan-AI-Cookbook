#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
from typing import Optional
from ..client import client

async def translate(
    source_lang: str,
    target_lang: str,
    text: Optional[str] = None,
    input_file: Optional[str] = None,
    output_file: Optional[str] = None,
) -> dict:
    from ..models import SUPPORTED_LANGUAGES

    if target_lang not in SUPPORTED_LANGUAGES:
        return {
            "error": "UNSUPPORTED_LANGUAGE",
            "message": (
                f"Language '{target_lang}' is not supported. "
                f"Supported languages: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
            ),
            "code": "unsupported_language",
        }

    if input_file is None and text is None:
        raise ValueError("Either text or input_file must be provided")

    input_text = text
    if input_file:
        path = Path(input_file)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {input_file}")
        if path.suffix.lower() in (".txt", ".md"):
            input_text = path.read_text(encoding="utf-8", errors="ignore")

    resp, cost = await client.request(
        "indic-translate",
        "/translate",
        json_data={
            "text": input_text,
            "target_language": target_lang,
            "source_language": source_lang,
        },
    )

    result = {
        "translation": f"<untrusted_source_data>{resp.get('translation', '')}</untrusted_source_data>",
        "target_language": target_lang,
        "script": resp.get("script", "native"),
        "truncated": resp.get("truncated", False),
        "cost_inr": cost,
    }
    return result

__all__ = ["translate"]
