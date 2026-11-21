"""Bodhan OCR Document Loader for LangChain."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Literal

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document

from .client import BodhanAsyncClient, BodhanClient
from .exceptions import BodhanValidationError
from .models import OCRBlock

logger = logging.getLogger(__name__)

# Bodhan indic-ocr accepts PNG and JPEG image formats
SUPPORTED_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})
PageErrorHandling = Literal["raise", "ignore", "warn"]


class BodhanOCRLoader(BaseLoader):
    """Document loader that extracts Markdown and layout blocks using Bodhan indic-ocr.

    Supports single scanned images (PNG, JPEG) and multi-page PDF documents.
    Multi-page PDFs are rasterized page-by-page using PyMuPDF and sent as individual
    page OCR requests to Bodhan's OpenAI-compatible API.

    Each page is returned as a distinct LangChain `Document` containing:
        - `page_content`: Full extracted Markdown text.
        - `metadata`: Source file path, page number, total pages, model ID,
          truncation flag, and structured OCR layout blocks.

    Usage:
        ```python
        from bodhan_langchain import BodhanClient, BodhanOCRLoader

        loader = BodhanOCRLoader("scanned_notice.pdf", dpi=150)
        documents = loader.load()
        for doc in documents:
            print(f"Page {doc.metadata['page']}: {doc.page_content[:100]}...")
        ```
    """

    def __init__(
        self,
        file_path: str | Path,
        client: BodhanClient | None = None,
        async_client: BodhanAsyncClient | None = None,
        dpi: int = 150,
        table_format: str = "markdown",
        max_pages: int | None = None,
        page_error_handling: PageErrorHandling = "raise",
        max_tokens: int | None = None,
    ) -> None:
        self.file_path = Path(file_path).resolve()
        self.dpi = dpi
        self.table_format = table_format
        self.max_pages = max_pages
        self.page_error_handling = page_error_handling
        self.max_tokens = max_tokens
        self._client = client
        self._async_client = async_client

        if self.dpi <= 0:
            raise BodhanValidationError("DPI must be a positive integer.", parameter="dpi")
        if self.max_pages is not None and self.max_pages <= 0:
            raise BodhanValidationError("max_pages must be a positive integer.", parameter="max_pages")
        if self.page_error_handling not in ("raise", "ignore", "warn"):
            raise BodhanValidationError(
                f"Invalid page_error_handling '{self.page_error_handling}'. Must be 'raise', 'ignore', or 'warn'.",
                parameter="page_error_handling",
            )

    def _get_client(self) -> tuple[BodhanClient, bool]:
        """Return a client instance and a boolean indicating whether it is owned internally."""
        if self._client is not None:
            return self._client, False
        return BodhanClient(), True

    def _get_async_client(self) -> tuple[BodhanAsyncClient, bool]:
        """Return an async client instance and a boolean indicating whether it is owned internally."""
        if self._async_client is not None:
            return self._async_client, False
        return BodhanAsyncClient(), True

    def _validate_file(self) -> str:
        """Validate that the file exists, is non-empty, and has a supported extension.

        Returns:
            The normalized lower-case extension (e.g. '.pdf', '.png').
        """
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: '{self.file_path}'")
        if not self.file_path.is_file():
            raise BodhanValidationError(f"Path is not a regular file: '{self.file_path}'", parameter="file_path")
        if self.file_path.stat().st_size == 0:
            raise BodhanValidationError(f"File is empty (0 bytes): '{self.file_path}'", parameter="file_path")

        ext = self.file_path.suffix.lower()
        if ext == ".webp":
            raise BodhanValidationError(
                f"Unsupported image format '{ext}' for file '{self.file_path.name}'. Bodhan indic-ocr accepts PNG and JPEG images only. "
                f"Please convert your image to PNG or JPEG before submission.",
                parameter="file_path",
            )
        if ext != ".pdf" and ext not in SUPPORTED_IMAGE_EXTENSIONS:
            supported = [".pdf"] + sorted(SUPPORTED_IMAGE_EXTENSIONS)
            raise BodhanValidationError(
                f"Unsupported file format '{ext}'. Supported formats: {', '.join(supported)}",
                parameter="file_path",
            )
        return ext

    def _blocks_to_metadata(self, blocks: list[OCRBlock]) -> list[dict[str, Any]]:
        """Convert OCRBlock instances into JSON-serializable dictionaries for LangChain metadata."""
        serializable: list[dict[str, Any]] = []
        for b in blocks:
            serializable.append({
                "order": b.order,
                "label": b.label,
                "type": b.type,
                "bbox_xyxy": b.bbox_xyxy,
                "conf": b.conf,
                "text": b.text,
            })
        return serializable

    def lazy_load(self) -> Iterator[Document]:
        """Lazily load documents page-by-page.

        Yields:
            Document: LangChain Document for each page.
        """
        ext = self._validate_file()
        client, owns_client = self._get_client()

        try:
            if ext in SUPPORTED_IMAGE_EXTENSIONS:
                try:
                    ocr_resp = client.ocr(
                        self.file_path,
                        table_format=self.table_format,
                        max_tokens=self.max_tokens,
                    )
                except Exception as e:
                    if self.page_error_handling == "raise":
                        raise
                    if self.page_error_handling == "warn":
                        logger.warning(f"OCR failed for image '{self.file_path}': {e}")
                    return

                yield Document(
                    page_content=ocr_resp.text,
                    metadata={
                        "source": str(self.file_path),
                        "page": 1,
                        "page_number": 1,
                        "total_pages": 1,
                        "model": ocr_resp.model,
                        "truncated": ocr_resp.truncated,
                        "finish_reason": ocr_resp.finish_reason,
                        "blocks": self._blocks_to_metadata(ocr_resp.blocks),
                    },
                )
                return

            # PDF processing via PyMuPDF
            try:
                import pymupdf
            except ImportError as exc:
                raise ImportError(
                    "PyMuPDF is required for PDF OCR loading. Install it via `pip install pymupdf`."
                ) from exc

            try:
                pdf_doc = pymupdf.open(str(self.file_path))
            except Exception as exc:
                raise BodhanValidationError(f"Failed to open PDF document '{self.file_path}': {exc}") from exc

            try:
                total_doc_pages = len(pdf_doc)
                if total_doc_pages == 0:
                    raise BodhanValidationError(f"PDF document '{self.file_path}' has 0 pages.")

                pages_to_process = (
                    min(total_doc_pages, self.max_pages)
                    if self.max_pages is not None
                    else total_doc_pages
                )

                for page_idx in range(pages_to_process):
                    page_num = page_idx + 1
                    try:
                        page = pdf_doc[page_idx]
                        pix = page.get_pixmap(dpi=self.dpi)
                        png_bytes = pix.tobytes("png")
                        if not png_bytes:
                            raise BodhanValidationError(f"Failed to rasterize page {page_num} of '{self.file_path}'")

                        ocr_resp = client.ocr(
                            png_bytes,
                            table_format=self.table_format,
                            max_tokens=self.max_tokens,
                        )

                        yield Document(
                            page_content=ocr_resp.text,
                            metadata={
                                "source": str(self.file_path),
                                "page": page_num,
                                "page_number": page_num,
                                "total_pages": total_doc_pages,
                                "model": ocr_resp.model,
                                "truncated": ocr_resp.truncated,
                                "finish_reason": ocr_resp.finish_reason,
                                "blocks": self._blocks_to_metadata(ocr_resp.blocks),
                            },
                        )
                    except Exception as exc:
                        if self.page_error_handling == "raise":
                            raise
                        if self.page_error_handling == "warn":
                            logger.warning(f"Error processing page {page_num} of '{self.file_path}': {exc}")
            finally:
                pdf_doc.close()
        finally:
            if owns_client:
                client.close()

    async def alazy_load(self) -> AsyncIterator[Document]:
        """Asynchronously and lazily load documents page-by-page."""
        ext = self._validate_file()
        async_client, owns_client = self._get_async_client()

        try:
            if ext in SUPPORTED_IMAGE_EXTENSIONS:
                try:
                    ocr_resp = await async_client.ocr(
                        self.file_path,
                        table_format=self.table_format,
                        max_tokens=self.max_tokens,
                    )
                except Exception as e:
                    if self.page_error_handling == "raise":
                        raise
                    if self.page_error_handling == "warn":
                        logger.warning(f"Async OCR failed for image '{self.file_path}': {e}")
                    return

                yield Document(
                    page_content=ocr_resp.text,
                    metadata={
                        "source": str(self.file_path),
                        "page": 1,
                        "page_number": 1,
                        "total_pages": 1,
                        "model": ocr_resp.model,
                        "truncated": ocr_resp.truncated,
                        "finish_reason": ocr_resp.finish_reason,
                        "blocks": self._blocks_to_metadata(ocr_resp.blocks),
                    },
                )
                return

            try:
                import pymupdf
            except ImportError as exc:
                raise ImportError(
                    "PyMuPDF is required for PDF OCR loading. Install it via `pip install pymupdf`."
                ) from exc

            try:
                pdf_doc = pymupdf.open(str(self.file_path))
            except Exception as exc:
                raise BodhanValidationError(f"Failed to open PDF document '{self.file_path}': {exc}") from exc

            try:
                total_doc_pages = len(pdf_doc)
                if total_doc_pages == 0:
                    raise BodhanValidationError(f"PDF document '{self.file_path}' has 0 pages.")

                pages_to_process = (
                    min(total_doc_pages, self.max_pages)
                    if self.max_pages is not None
                    else total_doc_pages
                )

                for page_idx in range(pages_to_process):
                    page_num = page_idx + 1
                    try:
                        page = pdf_doc[page_idx]
                        pix = page.get_pixmap(dpi=self.dpi)
                        png_bytes = pix.tobytes("png")

                        ocr_resp = await async_client.ocr(
                            png_bytes,
                            table_format=self.table_format,
                            max_tokens=self.max_tokens,
                        )

                        yield Document(
                            page_content=ocr_resp.text,
                            metadata={
                                "source": str(self.file_path),
                                "page": page_num,
                                "page_number": page_num,
                                "total_pages": total_doc_pages,
                                "model": ocr_resp.model,
                                "truncated": ocr_resp.truncated,
                                "finish_reason": ocr_resp.finish_reason,
                                "blocks": self._blocks_to_metadata(ocr_resp.blocks),
                            },
                        )
                    except Exception as exc:
                        if self.page_error_handling == "raise":
                            raise
                        if self.page_error_handling == "warn":
                            logger.warning(f"Error processing page {page_num} of '{self.file_path}': {exc}")
            finally:
                pdf_doc.close()
        finally:
            if owns_client:
                await async_client.aclose()
