"""
Image validation and encoding utilities for Bodhan Indic-OCR.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from bodhan_core.errors import BodhanError, ErrorCode

log = logging.getLogger(__name__)

_MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
_ALLOWED_FORMATS = {"PNG", "JPEG", "JPG", "WEBP"}


def validate_image(path: Path) -> dict[str, Any]:
    """
    Validate that the image exists, is within the 10 MB size limit,
    and can be opened by Pillow. Returns metadata dict with dimensions.
    """
    path = Path(path)
    if not path.exists():
        raise BodhanError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Image file not found: {path}",
            how_to_fix="Ensure file exists at the given path.",
        )

    size = path.stat().st_size
    if size > _MAX_IMAGE_SIZE_BYTES:
        raise BodhanError(
            code=ErrorCode.INPUT_TOO_LARGE,
            message=f"Image size ({size / (1024*1024):.1f} MB) exceeds maximum allowed 10 MB.",
            how_to_fix="Resize or compress the image before processing.",
        )

    try:
        from PIL import Image
        with Image.open(path) as img:
            fmt = (img.format or "").upper()
            if fmt not in _ALLOWED_FORMATS:
                raise BodhanError(
                    code=ErrorCode.UNSUPPORTED_FORMAT,
                    message=f"Image format '{fmt}' is not supported. Use PNG or JPEG.",
                    how_to_fix="Convert image to PNG or JPEG format.",
                )
            width, height = img.size
            return {
                "width": width,
                "height": height,
                "format": fmt,
                "size_bytes": size,
            }
    except BodhanError:
        raise
    except Exception as exc:
        raise BodhanError(
            code=ErrorCode.UNSUPPORTED_FORMAT,
            message=f"Could not open image {path.name}: {exc}",
            how_to_fix="Ensure file is a valid image.",
        )


def to_base64_data_url(path: Path) -> str:
    """
    Encode image bytes into a data URL format expected by OpenAI-compatible vision / Bodhan OCR.
    Example: 'data:image/png;base64,...'
    """
    path = Path(path)
    suffix = path.suffix.lower()
    mime = "image/png" if suffix in {".png"} else "image/jpeg"

    data = path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"
