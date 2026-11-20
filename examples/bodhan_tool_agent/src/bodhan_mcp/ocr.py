
import base64
import io
import os
import logging
from pathlib import Path

import requests
import pypdfium2 as pdfium
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

BODHAN_BASE_URL = os.getenv(
    "BODHAN_BASE_URL",
    "https://api.bodhan.ai"
)

OCR_URL = f"{BODHAN_BASE_URL}/v1/chat/completions"


def _ocr_image_bytes(
    image_bytes: bytes,
    mime_type: str,
    table_format: str,
    max_tokens: int,
) -> dict:
    """Send one image page to Bodhan Indic-OCR."""

    api_key = os.getenv("BODHAN_OCR_KEY")

    if not api_key:
        raise ValueError(
            "BODHAN_OCR_KEY is not configured."
        )

    encoded_image = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    image_data_uri = (
        f"data:{mime_type};base64,{encoded_image}"
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
                            "url": image_data_uri
                        },
                    }
                ],
            }
        ],
        "table_format": table_format,
        "max_tokens": max_tokens,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        OCR_URL,
        headers=headers,
        json=payload,
        timeout=120,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Bodhan authentication failed. "
            "Check your OCR API key."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "Bodhan rate or credit limit reached."
        )

    if response.status_code == 502:
        raise RuntimeError(
            "Bodhan could not transcribe the image. "
            "The page may exceed the block token limit "
            "or the image could not be decoded. "
            "Consider increasing max_tokens."
        )

    response.raise_for_status()

    result = response.json()

    try:
        extracted_text = (
            result["choices"][0]["message"]["content"]
        )
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            "Unexpected Bodhan response: OCR text is missing."
        )

    return {
        "text": extracted_text,
        "blocks": result.get("blocks", []),
        "usage": result.get("usage"),
    }


def _combine_usage(usages: list) -> dict:
    """Combine numeric usage values across OCR calls."""

    combined = {}

    for usage in usages:

        if not isinstance(usage, dict):
            continue

        for key, value in usage.items():

            if isinstance(value, (int, float)):
                combined[key] = (
                    combined.get(key, 0) + value
                )

    return combined if combined else None


def _extract_image(
    image_path: Path,
    table_format: str,
    max_tokens: int,
) -> dict:
    """OCR a PNG or JPEG image file."""

    try:
        with Image.open(image_path) as image:
            image_format = image.format

            if image_format not in {"PNG", "JPEG"}:
                raise ValueError(
                    "Only PNG and JPEG images are supported."
                )

            image.verify()

    except (OSError, Image.UnidentifiedImageError) as exc:
        raise ValueError(
            f"Invalid or unreadable image: {image_path}"
        ) from exc

    mime_type = (
        "image/png"
        if image_format == "PNG"
        else "image/jpeg"
    )

    return _ocr_image_bytes(
        image_bytes=image_path.read_bytes(),
        mime_type=mime_type,
        table_format=table_format,
        max_tokens=max_tokens,
    )


def _extract_pdf(
    pdf_path: Path,
    table_format: str,
    max_tokens: int,
) -> dict:
    """Render every PDF page and OCR it individually."""

    document = pdfium.PdfDocument(str(pdf_path))

    page_texts = []
    all_blocks = []
    usages = []

    try:
        total_pages = len(document)

        for page_index in range(total_pages):

            logging.info(
                "OCR processing PDF page %s/%s...",
                page_index + 1,
                total_pages,
            )

            page = document[page_index]

            # Render PDF page to an image
            bitmap = page.render(scale=2)
            pil_image = bitmap.to_pil()

            image_buffer = io.BytesIO()

            pil_image.save(
                image_buffer,
                format="PNG"
            )

            image_bytes = image_buffer.getvalue()

            # OCR the rendered page using Bodhan
            result = _ocr_image_bytes(
                image_bytes=image_bytes,
                mime_type="image/png",
                table_format=table_format,
                max_tokens=max_tokens,
            )

            page_number = page_index + 1

            page_texts.append(
                f"## Page {page_number}\n\n"
                f"{result['text']}"
            )

            # Preserve page information with blocks
            for block in result.get("blocks", []):
                if isinstance(block, dict):
                    block_with_page = dict(block)
                    block_with_page["page"] = page_number
                    all_blocks.append(block_with_page)
                else:
                    all_blocks.append({
                        "page": page_number,
                        "data": block,
                    })

            usages.append(result.get("usage"))

            pil_image.close()
            page.close()

    finally:
        document.close()

    combined_text = "\n\n".join(page_texts)

    return {
        "text": combined_text,
        "blocks": all_blocks,
        "usage": _combine_usage(usages),
        "pages_processed": total_pages,
    }


def extract_text(
    file_path: str,
    table_format: str = "html",
    max_tokens: int = 2048,
) -> dict:
    """
    Extract text and layout using Bodhan Indic-OCR.

    Supported:
    - PNG
    - JPEG
    - Multi-page PDF

    PDFs are rendered page-by-page and each page is
    sent separately to Bodhan Indic-OCR.
    """

    api_key = os.getenv("BODHAN_OCR_KEY")

    if not api_key:
        raise ValueError(
            "BODHAN_OCR_KEY is not configured."
        )

    file_path_obj = Path(file_path)

    if not file_path_obj.exists():
        raise FileNotFoundError(
            f"File not found: {file_path}"
        )

    if not file_path_obj.is_file():
        raise ValueError(
            f"Expected a file: {file_path}"
        )

    if table_format not in {"html", "markdown"}:
        raise ValueError(
            "table_format must be html or markdown."
        )

    if max_tokens < 1:
        raise ValueError(
            "max_tokens must be positive."
        )

    extension = file_path_obj.suffix.lower()

    # PDF processing
    if extension == ".pdf":

        result = _extract_pdf(
            pdf_path=file_path_obj,
            table_format=table_format,
            max_tokens=max_tokens,
        )

        return {
            "model": "indic-ocr",
            "text": result["text"],
            "blocks": result["blocks"],
            "usage": result["usage"],
            "file": file_path_obj.name,
            "pages_processed": result["pages_processed"],
        }

    # Image processing
    if extension in {".png", ".jpg", ".jpeg"}:

        result = _extract_image(
            image_path=file_path_obj,
            table_format=table_format,
            max_tokens=max_tokens,
        )

        return {
            "model": "indic-ocr",
            "text": result["text"],
            "blocks": result["blocks"],
            "usage": result["usage"],
            "file": file_path_obj.name,
            "pages_processed": 1,
        }

    raise ValueError(
        "Unsupported file format. "
        "Provide PNG, JPEG or PDF."
    )
