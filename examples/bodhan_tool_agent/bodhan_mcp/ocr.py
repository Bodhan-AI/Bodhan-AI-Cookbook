"""Read local image and PDF pages using Bodhan Indic-OCR."""

import base64
from collections.abc import Callable
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from typing import Annotated

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

from .request_budget import OCR_RATE, reserve

OCR_URL = "https://api.bodhan.ai/v1/chat/completions"
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class OcrPage(BaseModel):
    page_number: int
    markdown: str


class OcrResult(BaseModel):
    pages: list[OcrPage]


class _OcrMessage(BaseModel):
    content: str


class _OcrChoice(BaseModel):
    finish_reason: str
    message: _OcrMessage


class _OcrResponse(BaseModel):
    choices: list[_OcrChoice] = Field(min_length=1, max_length=1)


def _poppler(command: list[str]) -> str:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=90,
            env={**os.environ, "LC_ALL": "C"},
        ).stdout
    except FileNotFoundError:
        raise ToolError("missing_poppler: Install Poppler and put pdfinfo and pdftoppm on PATH.") from None
    except subprocess.TimeoutExpired:
        raise ToolError("pdf_render_timeout: Try a smaller PDF.") from None
    except subprocess.CalledProcessError:
        raise ToolError("invalid_pdf: Check that the PDF is readable and unlocked.") from None


def _render_pdf(source: Path, directory: Path) -> list[Path]:
    info = _poppler(["pdfinfo", str(source)])
    match = re.search(r"^Pages:\s+(\d+)$", info, re.MULTILINE)
    if match is None or int(match[1]) < 1:
        raise ToolError("invalid_pdf: Could not determine the PDF page count.")
    _poppler([
        "pdftoppm", "-png", "-r", "180", "-scale-to", "2400",
        str(source), str(directory / "page"),
    ])
    pages = sorted(directory.glob("page-*.png"), key=lambda page: int(page.stem.split("-")[1]))
    if [int(page.stem.split("-")[1]) for page in pages] != list(range(1, int(match[1]) + 1)):
        raise ToolError("incomplete_render: The rendered pages do not match the PDF page count.")
    return pages


def _read_page(client: httpx.Client, image: Path, key: str) -> str:
    try:
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    except OSError:
        raise ToolError("unreadable_image: Check that the page image can be read.") from None

    data_uri = f"data:{IMAGE_TYPES[image.suffix.lower()]};base64,{encoded}"
    reserve("indic-ocr", OCR_RATE)
    try:
        response = client.post(
            OCR_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "indic-ocr",
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ]}],
                "max_tokens": 4096,
            },
        )
    except httpx.TimeoutException:
        raise ToolError("timeout: Bodhan did not return the page in time.") from None
    except httpx.RequestError:
        raise ToolError("connection_error: Could not reach the Bodhan OCR API.") from None

    if response.status_code != 200:
        errors = {
            400: "invalid_request: Check that the source image is readable.",
            401: "authentication_failed: Check the Bodhan OCR key.",
            403: "access_denied: Check that the key has OCR access.",
            413: "image_too_large: Try a smaller page image.",
            429: "rate_limited: Wait before retrying.",
            502: "page_failed: Bodhan could not decode the page or a block exceeded its token budget.",
        }
        raise ToolError(errors.get(response.status_code, f"upstream_error: Bodhan returned HTTP {response.status_code}."))
    try:
        choice = _OcrResponse.model_validate_json(response.content, strict=True).choices[0]
    except ValidationError:
        raise ToolError("invalid_response: Bodhan returned an unexpected OCR response.") from None
    if choice.finish_reason != "stop":
        raise ToolError("incomplete_ocr: Bodhan did not finish reading the page.")
    return choice.message.content


def extract_document_text(
    document_path: Annotated[str, Field(
        min_length=1, description="Path to a local PDF, PNG, JPG or JPEG file.",
    )],
) -> OcrResult:
    """Extract page text with Indic-OCR and return Markdown in original page order.

    Pass a local file path. The tool renders PDF pages and submits one image per
    request. A blank page may have empty Markdown. PNG and JPEG count as one page.
    """
    return read_document(document_path)


def read_document(document_path: str, on_page: Callable[[OcrPage], None] | None = None) -> OcrResult:
    source = Path(document_path).expanduser().absolute()
    if not source.is_file():
        raise ToolError("missing_document: Provide a path to an existing document.")
    suffix = source.suffix.lower()
    if suffix != ".pdf" and suffix not in IMAGE_TYPES:
        raise ToolError("unsupported_document: Provide a PDF, PNG or JPEG file.")
    key = os.getenv("BODHAN_OCR_KEY", "").strip()
    if not key:
        raise ToolError("missing_key: Set BODHAN_OCR_KEY in the recipe's .env file.")

    pages: list[OcrPage] = []
    with TemporaryDirectory(prefix="bodhan-ocr-") as directory, httpx.Client(timeout=45.0) as client:
        images = _render_pdf(source, Path(directory)) if suffix == ".pdf" else [source]
        for page_number, image in enumerate(images, start=1):
            try:
                markdown = _read_page(client, image, key)
            except ToolError as exc:
                raise ToolError(f"Page {page_number} of {len(images)}: {exc}") from None
            page = OcrPage(page_number=page_number, markdown=markdown)
            pages.append(page)
            if on_page is not None:
                on_page(page)
    return OcrResult(pages=pages)
