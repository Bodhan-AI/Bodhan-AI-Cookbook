
import base64
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BODHAN_BASE_URL = os.getenv(
    "BODHAN_BASE_URL",
    "https://api.bodhan.ai/v1"
).rstrip("/")

IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp"
}


def extract_text(
    file_path: str,
    language: str = "auto"
) -> dict:
    """Extract text from an image using Bodhan Indic-OCR."""

    if not file_path.strip():
        return {
            "status": "error",
            "message": "File path cannot be empty."
        }

    path = Path(file_path).expanduser()

    if not path.is_file():
        return {
            "status": "error",
            "message": f"File not found: {file_path}"
        }

    extension = path.suffix.lower()

    if extension not in IMAGE_MIME_TYPES:
        return {
            "status": "error",
            "message": (
                "Unsupported format. Use PNG, JPG, JPEG or WebP. "
                "PDF support will be added separately."
            )
        }

    if path.stat().st_size == 0:
        return {
            "status": "error",
            "message": "Image file is empty."
        }

    api_key = os.getenv("BODHAN_OCR_KEY")

    if not api_key:
        return {
            "status": "error",
            "message": "BODHAN_OCR_KEY is missing from .env"
        }

    try:
        image_bytes = path.read_bytes()
        encoded_image = base64.b64encode(
            image_bytes
        ).decode("utf-8")

        data_url = (
            f"data:{IMAGE_MIME_TYPES[extension]};"
            f"base64,{encoded_image}"
        )

        payload = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url
                            }
                        }
                    ]
                }
            ]
        }

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{BODHAN_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json=payload
            )

            response.raise_for_status()
            data = response.json()

        extracted_text = data["choices"][0]["message"]["content"]

        if not isinstance(extracted_text, str):
            return {
                "status": "error",
                "message": "Unexpected OCR text format."
            }

        return {
            "status": "success",
            "file_path": str(path.resolve()),
            "language": language,
            "extracted_text": extracted_text,
            "model": "indic-ocr"
        }

    except httpx.HTTPStatusError as exc:
        return {
            "status": "error",
            "message": (
                f"Bodhan API returned HTTP "
                f"{exc.response.status_code}"
            )
        }

    except httpx.RequestError as exc:
        return {
            "status": "error",
            "message": (
                f"Network request failed: "
                f"{type(exc).__name__}"
            )
        }

    except (KeyError, IndexError, TypeError, ValueError):
        return {
            "status": "error",
            "message": "Unexpected Bodhan API response format."
        }

    except OSError as exc:
        return {
            "status": "error",
            "message": f"Could not read image: {type(exc).__name__}"
        }
