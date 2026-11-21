"""Document ingestion and translation pipeline using Bodhan OCR and Translation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from langchain_core.documents import Document

from bodhan_langchain import (
    BodhanClient,
    BodhanOCRLoader,
    BodhanTranslationTransformer,
)

logger = logging.getLogger("bodhan.rag.ingestion")


def ingest_documents(
    sample_dir: Path | str,
    ocr_client: BodhanClient,
    translation_client: BodhanClient,
    target_language: str = "en",
    dpi: int = 150,
) -> List[Document]:
    """Ingest multilingual PDF notices, extract text via Bodhan OCR, and normalize to English.

    Args:
        sample_dir: Directory containing sample PDF documents.
        ocr_client: Configured BodhanClient with OCR permissions.
        translation_client: Configured BodhanClient with Translation permissions.
        target_language: Target normalization language code (default 'en').
        dpi: Rasterization DPI for PDF pages.

    Returns:
        List of immutable LangChain Document instances translated to English with full metadata.

    Raises:
        FileNotFoundError: If sample_dir does not exist or contains no PDFs.
        RuntimeError: If document ingestion fails completely.
    """
    directory = Path(sample_dir).resolve()
    if not directory.exists() or not directory.is_dir():
        raise FileNotFoundError(f"Sample directory '{directory}' does not exist.")

    pdf_files = sorted(list(directory.glob("*.pdf")))
    if not pdf_files:
        raise FileNotFoundError(f"No PDF files found in '{directory}'.")

    raw_docs: List[Document] = []
    failed_files: List[str] = []

    print(f"  -> Found {len(pdf_files)} PDF notice(s) to ingest.")

    for pdf_path in pdf_files:
        try:
            print(f"     [OCR] Ingesting {pdf_path.name}...")
            loader = BodhanOCRLoader(pdf_path, client=ocr_client, dpi=dpi)
            loaded_pages = loader.load()
            if not loaded_pages:
                logger.warning("No pages extracted from %s", pdf_path.name)
                failed_files.append(pdf_path.name)
                continue
            raw_docs.extend(loaded_pages)
            print(f"           Extracted {len(loaded_pages)} page(s) ({sum(len(p.page_content) for p in loaded_pages)} chars).")
        except Exception as exc:
            logger.error("Failed to OCR document '%s': %s", pdf_path.name, exc)
            failed_files.append(f"{pdf_path.name} ({exc.__class__.__name__})")

    if not raw_docs:
        raise RuntimeError(f"Document ingestion failed for all files: {', '.join(failed_files)}")

    if failed_files:
        print(f"  [WARNING] The following documents failed to process and were omitted: {', '.join(failed_files)}")

    print(f"\n  -> Translating {len(raw_docs)} extracted page(s) to English ({target_language})...")
    transformer = BodhanTranslationTransformer(
        target_language=target_language,
        client=translation_client,
        preserve_original_content=True,
    )

    try:
        translated_docs = transformer.transform_documents(raw_docs)
    except Exception as exc:
        raise RuntimeError(f"Translation transformation failed: {exc}") from exc

    # Validate metadata preservation
    for doc in translated_docs:
        src = Path(doc.metadata.get("source", "")).name
        page = doc.metadata.get("page_number", doc.metadata.get("page", 1))
        src_lang = doc.metadata.get("source_language", "unknown")
        snippet = doc.page_content.replace("\n", " ").strip()[:60]
        print(f"     [Translated] {src} (Page {page}) [{src_lang} -> {target_language}]: \"{snippet}...\"")

    return translated_docs
