#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..client import client
from ..models import (
    SUPPORTED_TRANSLATE_LANGUAGES,
    TRANSLATE_SCRIPTS,
    ErrorCode,
)
from ..media import chunk_text_for_translation, script_ratio


def _error(
    code: ErrorCode, message: str, retryable: bool = False,
    details: dict | None = None,
) -> dict:
    return {
        "error": code.value,
        "message": message,
        "code": code.value,
        "retryable": retryable,
        "details": details or {},
    }


async def translate_text(
    source_lang: str,
    target_lang: str,
    text: Optional[str] = None,
    input_file: Optional[str] = None,
    output_file: Optional[str] = None,
    script: str = "native",
) -> dict:
    if source_lang not in SUPPORTED_TRANSLATE_LANGUAGES:
        return _error(
            ErrorCode.UNSUPPORTED_LANGUAGE,
            f"Source language '{source_lang}' is not supported for translation",
            retryable=False,
            details={"supported": sorted(SUPPORTED_TRANSLATE_LANGUAGES)},
        )

    if target_lang not in SUPPORTED_TRANSLATE_LANGUAGES:
        return _error(
            ErrorCode.UNSUPPORTED_LANGUAGE,
            f"Target language '{target_lang}' is not supported for translation",
            retryable=False,
            details={"supported": sorted(SUPPORTED_TRANSLATE_LANGUAGES)},
        )

    if script not in TRANSLATE_SCRIPTS:
        supported = ", ".join(sorted(TRANSLATE_SCRIPTS))
        return _error(
            ErrorCode.VALIDATION_ERROR,
            f"Invalid script '{script}'. Must be one of: {supported}",
            retryable=False,
        )

    if input_file is None and text is None:
        return _error(
            ErrorCode.VALIDATION_ERROR,
            "Either text or input_file must be provided",
            retryable=False,
        )

    input_text = text
    if input_file:
        path = Path(input_file)
        if not path.exists():
            return _error(
                ErrorCode.FILE_NOT_FOUND,
                f"File not found: {input_file}",
                retryable=False,
            )
        if path.suffix.lower() in (".txt", ".md"):
            try:
                input_text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                return _error(
                    ErrorCode.MEDIA_PROCESSING_ERROR,
                    f"Failed to read input file: {e}",
                    retryable=False,
                )
        else:
            return _error(
                ErrorCode.VALIDATION_ERROR,
                f"Unsupported input file format: {path.suffix}",
                retryable=False,
                details={"supported": [".txt", ".md"]},
            )

    if not input_text or not input_text.strip():
        return {
            "translation": "<untrusted_source_data></untrusted_source_data>",
            "target_language": target_lang,
            "script": script,
            "target_script_verified": False,
            "cost_inr": 0.0,
            "output_file": output_file,
            "cached": False,
        }

    chunks = chunk_text_for_translation(input_text)
    translations = []
    total_cost = 0.0

    for chunk in chunks:
        try:
            api_resp = await client.request(
                "indic-translate",
                "/chat/completions",
                json_data={
                    "model": "indic-translate",
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Translate '{chunk}' to {target_lang}",
                        }
                    ],
                    "script": script,
                },
                timeout=60,
            )
            resp = api_resp.data
            cost = api_resp.cost_inr
        except Exception as e:
            return _error(
                ErrorCode.NETWORK_ERROR,
                f"Translation API call failed: {e}",
                retryable=True,
            )
        total_cost += cost
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        translations.append(content)

    full_translation = "\n\n".join(translations)

    target_script_map = {
        "devanagari": ["hi", "mr", "ne", "sa", "bn", "as", "gu", "pa", "or"],
        "tamil": ["ta"],
        "telugu": ["te"],
        "bengali": ["bn", "as"],
        "kannada": ["kn"],
        "gujarati": ["gu"],
        "malayalam": ["ml"],
        "oriya": ["or"],
        "gurmukhi": ["pa"],
        "arabic": ["ur", "ks", "sd-Arab"],
    }

    expected_scripts = []
    for scr, langs in target_script_map.items():
        if target_lang in langs:
            expected_scripts.append(scr)

    target_script_verified = False
    if expected_scripts:
        target_script_verified = any(
            script_ratio(full_translation, scr) >= 0.85 for scr in expected_scripts
        )

    if output_file:
        try:
            Path(output_file).write_text(full_translation, encoding="utf-8")
        except Exception as e:
            return _error(
                ErrorCode.MEDIA_PROCESSING_ERROR,
                f"Failed to write output file: {e}",
                retryable=False,
            )

    return {
        "translation": (
            f"<untrusted_source_data>{full_translation}</untrusted_source_data>"
        ),
        "target_language": target_lang,
        "script": script,
        "target_script_verified": target_script_verified,
        "cost_inr": round(total_cost, 4),
        "output_file": output_file,
        "cached": False,
    }


__all__ = ["translate_text"]
